"""LTI 1.3 launch: the platform sends the person here, signed, and Sympraxi signs them in.

The steps (the standard's "OpenID Connect third-party-initiated login"):

1. The platform opens `/lti/login/` with who it is and a login hint. Sympraxi answers by
   sending the browser to the platform's authorization address with a one-time `nonce`
   and a signed `state`.
2. The platform posts back a signed `id_token` and the same `state`. Sympraxi checks the
   state is its own and fresh, the token's signature against the platform's published
   keys, the issuer, audience, deployment, expiry, and that the nonce is one it issued
   and has not seen used.
3. The person is matched to an account the way a CAS sign-in is: by the platform's own
   identifier for them after the first time, by email the first time, with the email
   at one of SYMPRAXI_SIGNUP_EMAIL_DOMAINS. The platform's role claims are ignored:
   a launch makes someone a student of the center, never staff.

The state is signed rather than kept in the session because the platform's post comes from
another site, and the browser doesn't send session cookies with that.
"""

import json
import re
import secrets
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any, cast

import jwt
from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.cas import CasError
from sympraxi.accounts.models import User
from sympraxi.centers.models import Membership, Role
from sympraxi.lti.models import LtiLink, LtiNonce, LtiPlatform
from sympraxi.privacy.audit import AuditAction, record

STATE_SALT = "sympraxi.lti.state"
STATE_MAX_AGE = 600
KEYS_MAX_AGE = 300
CLAIM = "https://purl.imsglobal.org/spec/lti/claim/"
MESSAGE_TYPE = "LtiResourceLinkRequest"


class LtiError(CasError):
    """The launch can't proceed. `reason` is a short code for the audit log."""


def find_platform(issuer: str, client_id: str) -> LtiPlatform:
    rows = LtiPlatform.objects.filter(issuer=issuer.rstrip("/"), is_active=True).select_related(
        "center"
    )
    if client_id:
        rows = rows.filter(client_id=client_id)
    found = list(rows[:2])
    if len(found) != 1:
        raise LtiError("unknown_platform", _("This learning system isn't set up for sign-in here."))
    return found[0]


def begin(platform: LtiPlatform) -> tuple[str, str]:
    """A fresh (state, nonce) pair for the redirect to the platform."""
    nonce = secrets.token_urlsafe(24)
    LtiNonce.objects.filter(created_at__lt=timezone.now() - timedelta(hours=1)).delete()
    LtiNonce.objects.create(nonce=nonce)
    state = signing.dumps({"p": platform.pk, "n": nonce}, salt=STATE_SALT)
    return state, nonce


def fetch_keys(url: str) -> dict[str, Any]:
    """The platform's published keys. Replaced in tests."""
    cached = cache.get(f"lti-keys:{url}")
    if cached is not None:
        return cast(dict[str, Any], cached)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
            keys = json.loads(answer.read(500_000))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise LtiError(
            "keys_unreachable", _("Couldn't reach the learning system's keys.")
        ) from error
    cache.set(f"lti-keys:{url}", keys, KEYS_MAX_AGE)
    return cast(dict[str, Any], keys)


def verify(state: str, token: str) -> tuple[LtiPlatform, dict[str, Any]]:
    try:
        issued = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
        platform = LtiPlatform.objects.select_related("center").get(pk=issued["p"], is_active=True)
    except (signing.BadSignature, LtiPlatform.DoesNotExist, KeyError, TypeError) as error:
        raise LtiError(
            "bad_state", _("That launch expired. Open the tool from your course again.")
        ) from error
    # One use only: delete reports how many rows it removed.
    if not LtiNonce.objects.filter(pk=issued["n"]).delete()[0]:
        raise LtiError("replayed", _("That launch was already used. Open the tool again."))
    unchecked = _("The learning system's sign-in couldn't be checked.")
    try:
        header = jwt.get_unverified_header(token)
        keys = fetch_keys(platform.jwks_url).get("keys", [])
        match = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if match is None or header.get("alg") != "RS256":
            raise LtiError("bad_token", unchecked)
        claims = jwt.decode(
            token,
            key=jwt.PyJWK.from_dict(match).key,
            algorithms=["RS256"],
            audience=platform.client_id,
            issuer=platform.issuer.rstrip("/"),
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        raise LtiError("bad_token", unchecked) from error
    if claims["nonce"] != issued["n"]:
        raise LtiError("bad_nonce", _("The learning system's sign-in didn't match. Try again."))
    if isinstance(claims["aud"], list) and claims.get("azp") != platform.client_id:
        raise LtiError("bad_audience", _("The learning system's sign-in wasn't meant for us."))
    if (
        claims.get(CLAIM + "version") != "1.3.0"
        or claims.get(CLAIM + "message_type") != MESSAGE_TYPE
    ):
        raise LtiError("bad_message", _("That kind of launch isn't supported."))
    if claims.get(CLAIM + "deployment_id") not in (platform.deployment_ids or []):
        raise LtiError("bad_deployment", _("This course's tool setup isn't registered here."))
    return platform, claims


def email_of(claims: dict[str, Any]) -> str:
    email = str(claims.get("email") or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise LtiError(
            "no_email",
            _(
                "The learning system didn't share your email address, so we can't sign "
                "you in. Ask your instructor or administrator."
            ),
        )
    allowed = settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS
    if not allowed or email.rsplit("@", 1)[-1] not in allowed:
        raise LtiError("wrong_email_domain", _("Please use your college email address."))
    return email


def user_for(platform: LtiPlatform, claims: dict[str, Any], request: HttpRequest) -> User:
    subject = str(claims["sub"])[:255]
    link = LtiLink.objects.select_related("user").filter(platform=platform, subject=subject).first()
    off = _("That account is turned off. Ask your writing center.")
    if link is not None:
        if not link.user.is_active:
            raise LtiError("account_off", off)
        return link.user
    email = email_of(claims)
    existing = User.objects.filter(email__iexact=email).first()
    if existing is not None:
        if LtiLink.objects.filter(platform=platform, user=existing).exists():
            raise LtiError(
                "already_linked",
                _(
                    "The account with this email address is already linked to a different "
                    "learning system sign-in. Ask your writing center to check it."
                ),
            )
        if not existing.is_active:
            raise LtiError("account_off", off)
        user = existing
    elif settings.SYMPRAXI_SIGNUP_OPEN:
        user = User.objects.create_user(
            email, password=None, full_name=str(claims.get("name") or "")[:150]
        )
        record(AuditAction.ACCOUNT_CREATED, request=request, target=user, via="lti")
    else:
        raise LtiError(
            "signup_closed",
            _("There's no account for that address yet. Ask your writing center to add you."),
        )
    LtiLink.objects.create(platform=platform, user=user, subject=subject)
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    return user


def join_center(user: User, platform: LtiPlatform) -> None:
    """A launch from a course makes the person a student of the center if they have no role."""
    if not Membership.objects.filter(user=user, center=platform.center).exists():
        Membership.objects.create(user=user, center=platform.center, role=Role.STUDENT)
