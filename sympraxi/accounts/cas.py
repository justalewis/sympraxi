"""Single sign-on through a CAS server (Apereo CAS, and others that speak CAS 2.0 or 3.0).

The person is sent to the CAS server to sign in and comes back with a one-time ticket.
Sympraxi asks the CAS server, over TLS and from its own server, whether the ticket is
good and for whom; nothing in the browser is trusted. The ticket is checked against
the exact address the person was sent back to, and a CAS ticket works once.

CAS has no "email verified" claim. What it gives is the CAS server's own word about who
signed in, so the rules are:

- the CAS server is the one the host configured (`SYMPRAXI_CAS_SERVER_URL`, https only);
- the person's email, from an attribute (`SYMPRAXI_CAS_EMAIL_ATTRIBUTE`) or the CAS user
  name when that is an address, is at one of SYMPRAXI_SIGNUP_EMAIL_DOMAINS, which must be set;
- the first sign-in links the CAS user name to the account with that email, and later
  sign-ins match on the CAS user name, so a college that hands an old address to a new
  person doesn't hand over the records: the new person's CAS name won't match.

The XML answer is read with the standard library after refusing any that declares a DTD
or entity, which is all a CAS answer needs to avoid.
"""

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, cast

from allauth.account.models import EmailAddress, Login
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import CasLink, User
from sympraxi.privacy.audit import AuditAction, record

CAS_NS = "{http://www.yale.edu/tp/cas}"
TIMEOUT = 10


class CasError(Exception):
    """The sign-in can't proceed. `reason` is a short code for the audit log."""

    def __init__(self, reason: str, message: Any) -> None:
        super().__init__(reason)
        self.reason, self.message = reason, message


@dataclass
class CasIdentity:
    user: str
    attributes: dict[str, str] = field(default_factory=dict)


def configured() -> bool:
    return bool(settings.SYMPRAXI_CAS_SERVER_URL)


def server_url() -> str:
    return str(settings.SYMPRAXI_CAS_SERVER_URL).rstrip("/")


def login_url(service: str) -> str:
    return f"{server_url()}/login?" + urllib.parse.urlencode({"service": service})


def validate_url(service: str, ticket: str) -> str:
    path = "/p3/serviceValidate" if settings.SYMPRAXI_CAS_VERSION >= 3 else "/serviceValidate"
    return f"{server_url()}{path}?" + urllib.parse.urlencode({"service": service, "ticket": ticket})


def fetch(url: str) -> bytes:
    """Ask the CAS server. Replaced in tests."""
    request = urllib.request.Request(url, headers={"Accept": "application/xml"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:  # noqa: S310
            return cast(bytes, answer.read(200_000))
    except (urllib.error.URLError, OSError) as error:
        raise CasError("unreachable", _("The college's sign-in service didn't answer.")) from error


def parse(body: bytes) -> CasIdentity:
    """Read a serviceValidate answer."""
    lowered = body[:4000].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise CasError("bad_response", _("The college's sign-in service gave an unusable answer."))
    try:
        root = ET.fromstring(body)  # noqa: S314 (no DTD or entities: refused above)
    except ET.ParseError as error:
        raise CasError(
            "bad_response", _("The college's sign-in service gave an unusable answer.")
        ) from error
    success = root.find(f"{CAS_NS}authenticationSuccess")
    if success is None:
        raise CasError(
            "ticket_refused", _("The college's sign-in service didn't accept that sign-in.")
        )
    name = (success.findtext(f"{CAS_NS}user") or "").strip()
    if not name:
        raise CasError("bad_response", _("The college's sign-in service gave an unusable answer."))
    attributes: dict[str, str] = {}
    block = success.find(f"{CAS_NS}attributes")
    if block is not None:
        for child in block:
            key = child.tag.removeprefix(CAS_NS)
            if child.text and child.text.strip() and key not in attributes:
                attributes[key] = child.text.strip()
    return CasIdentity(name, attributes)


def validate(service: str, ticket: str) -> CasIdentity:
    if not re.fullmatch(r"[A-Za-z0-9._~:\-]{1,200}", ticket):
        raise CasError("bad_ticket", _("That sign-in link isn't valid. Try again."))
    return parse(fetch(validate_url(service, ticket)))


def email_of(identity: CasIdentity) -> str:
    candidate = identity.attributes.get(settings.SYMPRAXI_CAS_EMAIL_ATTRIBUTE, "")
    if not candidate and "@" in identity.user:
        candidate = identity.user
    email = candidate.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise CasError(
            "no_email",
            _(
                "The college's sign-in service didn't give us your email address, "
                "so we can't sign you in."
            ),
        )
    allowed = settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS
    if not allowed or email.rsplit("@", 1)[-1] not in allowed:
        raise CasError("wrong_email_domain", _("Please sign in with your college email address."))
    return email


def user_for(identity: CasIdentity, request: HttpRequest) -> User:
    """The account this CAS sign-in is for, linking or creating it as the rules allow."""
    email = email_of(identity)
    link = CasLink.objects.select_related("user").filter(cas_user=identity.user).first()
    if link is not None:
        if not link.user.is_active:
            raise CasError("account_off", _("That account is turned off. Ask your writing center."))
        return link.user
    existing = User.objects.filter(email__iexact=email).first()
    if existing is not None:
        if CasLink.objects.filter(user=existing).exists():
            # The address belongs to an account already tied to someone else at CAS.
            raise CasError(
                "already_linked",
                _(
                    "The account with this email address is already linked to a different "
                    "college sign-in. Ask your writing center to check it."
                ),
            )
        user = existing
    elif settings.SYMPRAXI_SIGNUP_OPEN:
        user = User.objects.create_user(
            email,
            password=None,
            full_name=identity.attributes.get(settings.SYMPRAXI_CAS_NAME_ATTRIBUTE, ""),
            student_id=identity.attributes.get(settings.SYMPRAXI_CAS_STUDENT_ID_ATTRIBUTE) or None,
        )
        record(AuditAction.ACCOUNT_CREATED, request=request, target=user, via="cas")
    else:
        raise CasError(
            "signup_closed",
            _("There's no account for that address yet. Ask your writing center to add you."),
        )
    CasLink.objects.create(user=user, cas_user=identity.user)
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    fill_student_id(user, identity)
    return user


def fill_student_id(user: User, identity: CasIdentity) -> None:
    value = identity.attributes.get(settings.SYMPRAXI_CAS_STUDENT_ID_ATTRIBUTE, "")
    if not value or user.student_id:
        return
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", value):
        return
    if User.objects.filter(student_id=value).exclude(pk=user.pk).exists():
        return
    user.student_id = value
    user.save(update_fields=["student_id"])


def sign_in(request: HttpRequest, user: User, next_url: str) -> HttpResponse:
    """Sign the person in through allauth, so two-factor and the sign-in audit apply."""
    from allauth.account.internal.flows.login import perform_login

    login = Login(
        user=user, redirect_url=next_url, signal_kwargs={"sociallogin": None, "cas": True}
    )
    return perform_login(request, login)
