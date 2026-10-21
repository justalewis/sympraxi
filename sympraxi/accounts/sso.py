"""What a single sign-on login must prove before Sympraxi accepts it.

The only evidence is the ID token the identity provider returned straight to the
server over TLS. A sign-in is accepted, and may be linked to an existing account
with the same address, only when the token shows that:

- the person belongs to this college: Entra's `tid` is the configured tenant, or
  Google's `hd` is the configured Workspace domain;
- the provider vouches for the email address: Entra's `xms_edov` (the address is
  in a domain the tenant has verified; Entra's `email` claim alone is not
  verified) or Google's `email_verified`;
- the address is at one of SYMPRAXI_SIGNUP_EMAIL_DOMAINS, when that is set.

These rules run on every sign-in, not only the first, so an account that later
fails them (a changed address, a removed domain) can't keep signing in through it.

With SYMPRAXI_TRUST_IDP_MFA on, a token whose `amr` claim contains "mfa" also
stands in for Sympraxi's own two-factor step. Nothing else in the token does.
"""

import re
from dataclasses import dataclass
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.sso_config import ENTRA, GOOGLE, OIDC, entra_issuer

# Set in the session when a staff member's sign-in proved multi-factor login at
# the identity provider and the installation trusts that.
IDP_MFA_SESSION_KEY = "sympraxi_idp_mfa"


@dataclass(frozen=True)
class Rejection:
    reason: str  # a short code for the audit log
    message: Any  # what the person sees


WRONG_ORGANIZATION = Rejection(
    "wrong_organization",
    _("That account isn't from this college. Sign in with your college account."),
)
EMAIL_NOT_VERIFIED = Rejection(
    "email_not_verified",
    _(
        "Your college account didn't confirm your email address, so we can't sign you "
        "in with it. Your campus IT office can help."
    ),
)
WRONG_EMAIL_DOMAIN = Rejection(
    "wrong_email_domain",
    _("Please sign in with your college email address."),
)
ALREADY_LINKED = Rejection(
    "already_linked",
    _(
        "The account with this email address is already linked to a different "
        "%(provider)s account. Ask your writing center to check it."
    ),
)
LOCAL_LOGIN_OFF = Rejection(
    "local_login_off",
    _("Please sign in with your college account."),
)


def provider_apps() -> list[dict[str, Any]]:
    """The providers this installation has, as built from the environment."""
    apps = settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
    return [app for app in apps if app.get("provider_id") in (ENTRA, GOOGLE, OIDC)]


def app_settings(provider_id: str) -> dict[str, Any] | None:
    for app in provider_apps():
        if app["provider_id"] == provider_id:
            return dict(app.get("settings", {}))
    return None


def provider_name(provider_id: str) -> str:
    for app in provider_apps():
        if app["provider_id"] == provider_id:
            return str(app.get("name", provider_id))
    return provider_id


def sso_only() -> bool:
    """Whether password sign-in and sign-up are hidden from everyone but site admins.

    Only with a provider set up: otherwise nobody but site admins could sign in.
    """
    return bool(
        settings.SYMPRAXI_SSO_ONLY and (provider_apps() or settings.SYMPRAXI_CAS_SERVER_URL)
    )


def id_token_claims(sociallogin: Any) -> dict[str, Any]:
    claims = sociallogin.account.extra_data.get("id_token")
    return claims if isinstance(claims, dict) else {}


def asserted_email(claims: dict[str, Any]) -> str:
    email = claims.get("email")
    return email.strip() if isinstance(email, str) else ""


def _is_true(value: Any) -> bool:
    # Booleans in ID tokens are JSON true; accept "true" and 1 as well, since the
    # token is signed by the provider and a string form shouldn't lock people out.
    return value is not None and str(value).lower() in {"true", "1"}


def check_claims(provider_id: str, claims: dict[str, Any]) -> Rejection | None:
    """None if the token passes every rule above, else why it doesn't."""
    config = app_settings(provider_id)
    if config is None:
        return WRONG_ORGANIZATION
    if provider_id == ENTRA:
        tenant = config["tenant_id"]
        same_tenant = str(claims.get("tid", "")).lower() == tenant
        if not same_tenant or claims.get("iss") != entra_issuer(tenant):
            return WRONG_ORGANIZATION
        verified = _is_true(claims.get("xms_edov"))
    elif provider_id == GOOGLE:
        if str(claims.get("hd", "")).lower() != config["hosted_domain"]:
            return WRONG_ORGANIZATION
        verified = _is_true(claims.get("email_verified"))
    elif provider_id == OIDC:
        # Nothing in a generic token names the college, so the issuer must be the
        # configured one and the address's domain (checked below) must be the college's.
        if str(claims.get("iss", "")).rstrip("/") != str(config["server_url"]).rstrip("/"):
            return WRONG_ORGANIZATION
        verified = _is_true(claims.get("email_verified"))
        if not settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS:
            return WRONG_EMAIL_DOMAIN
    else:
        return WRONG_ORGANIZATION
    email = asserted_email(claims)
    if not email or not verified:
        return EMAIL_NOT_VERIFIED
    allowed = settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS
    if allowed and email.rsplit("@", 1)[-1].lower() not in allowed:
        return WRONG_EMAIL_DOMAIN
    return None


def check(sociallogin: Any) -> Rejection | None:
    return check_claims(sociallogin.account.provider, id_token_claims(sociallogin))


def student_id_in(claims: dict[str, Any]) -> str | None:
    """The student ID the provider sent, if the host named the claim and the value is sane."""
    name = settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM
    value = claims.get(name) if name else None
    text = str(value).strip() if isinstance(value, str | int) else ""
    return text if re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", text) else None


def fill_student_id(user: Any, claims: dict[str, Any], request: HttpRequest | None = None) -> None:
    """Give an account that has no student ID the one the provider sent.

    A student ID a director entered is never overwritten, and an ID already on another
    account is left alone; either way the mismatch is in the audit log, because it means
    the center's records and the college's disagree.
    """
    from sympraxi.accounts.models import User
    from sympraxi.privacy.audit import AuditAction, record

    sent = student_id_in(claims)
    if sent is None or user.pk is None:
        return
    if user.student_id == sent:
        return
    if user.student_id:
        record(AuditAction.SSO_STUDENT_ID_MISMATCH, request=request, actor=user, target=user)
        return
    if User.objects.filter(student_id=sent).exclude(pk=user.pk).exists():
        record(
            AuditAction.SSO_STUDENT_ID_MISMATCH,
            request=request,
            actor=user,
            target=user,
            conflict=True,
        )
        return
    user.student_id = sent
    user.save(update_fields=["student_id"])
    record(AuditAction.SSO_STUDENT_ID_SET, request=request, actor=user, target=user)


def mark_email_verified(sociallogin: Any) -> None:
    """Record the address as verified, once `check()` has passed.

    allauth takes `email_verified` at face value, and Entra never sends it. The
    rules above are what make the address trustworthy, for both providers.
    """
    email = asserted_email(id_token_claims(sociallogin)).lower()
    for address in sociallogin.email_addresses:
        if address.email.lower() == email:
            address.verified = True


def verify_linked_address(sociallogin: Any) -> None:
    """Mark the account's own copy of the address verified once it is linked.

    A director can add someone before they ever sign in (sympraxi/centers/people.py),
    so the account's address starts unverified. When single sign-on links that
    account, the provider has just vouched for the address (`check()` passed), so
    the person isn't also asked to confirm it by email.

    This must run after allauth's `wipe_password()`, which takes the password off
    an account matched by an unverified address, in case someone else registered
    the address first. Marking it verified sooner, in `pre_social_login()`, would
    let that password survive. `accounts/signals.py` calls this when the link is
    saved, which allauth does right after the wipe.
    """
    if sociallogin.user.pk is None:
        return
    email = asserted_email(id_token_claims(sociallogin))
    if email:
        EmailAddress.objects.filter(
            user=sociallogin.user, email__iexact=email, verified=False
        ).update(verified=True)


def proves_mfa(claims: dict[str, Any]) -> bool:
    amr = claims.get("amr")
    return isinstance(amr, list) and "mfa" in amr


def sociallogin_proves_mfa(sociallogin: Any) -> bool:
    """True if the installation trusts IdP MFA and this sign-in's token proves it."""
    if not settings.SYMPRAXI_TRUST_IDP_MFA or sociallogin is None:
        return False
    if check(sociallogin) is not None:
        return False
    return proves_mfa(id_token_claims(sociallogin))


def idp_mfa_trusted(request: HttpRequest) -> bool:
    session = getattr(request, "session", None)
    return bool(
        settings.SYMPRAXI_TRUST_IDP_MFA and session is not None and session.get(IDP_MFA_SESSION_KEY)
    )
