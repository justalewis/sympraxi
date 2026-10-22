"""Single sign-on through SAML 2.0 (Shibboleth, Entra's SAML, Okta, ADFS and others).

Only the web browser SSO profile, started by Sympraxi: the person is sent to the college's
identity provider with a request, and comes back with a signed response. The XML
signature and conditions are checked by python3-saml in strict mode (assertions must be
signed, deprecated algorithms are refused, the audience, destination, time window and
`InResponseTo` are all checked). On top of that:

- **Every response answers a request Sympraxi made.** The request's ID is kept for ten
  minutes and used up by the response that cites it, so a captured response can't be replayed
  and an identity-provider-initiated login isn't accepted. It is kept in the database, not the
  session, because the identity provider's post comes from another site and browsers don't
  send session cookies with it.
- **The identity provider is the one the host configured**, with the one signing certificate
  the host gave (`SYMPRAXI_SAML_IDP_CERT`). Nothing in a response chooses who to trust.
- **Matching a person** follows CAS: the email, from an attribute (or the NameID when it is an
  address), must be at one of SYMPRAXI_SIGNUP_EMAIL_DOMAINS; the first sign-in ties the NameID
  to the account with that email, and later sign-ins match on the NameID.
"""

import base64
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

from sympraxi.accounts.cas import CasError
from sympraxi.accounts.models import SamlLink, SamlRequest, User
from sympraxi.privacy.audit import AuditAction, record

REQUEST_LIFETIME = timedelta(minutes=10)
NAMEID_UNSPECIFIED = "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"


class SamlError(CasError):
    """The sign-in can't proceed. `reason` is a short code for the audit log."""


@dataclass
class SamlIdentity:
    name_id: str
    attributes: dict[str, str] = field(default_factory=dict)


def configured() -> bool:
    return bool(settings.SYMPRAXI_SAML_IDP_SSO_URL)


def pem_body(cert: str) -> str:
    """The certificate as bare base64, from either PEM text or the bare body."""
    lines = [ln.strip() for ln in cert.replace("\\n", "\n").splitlines()]
    return "".join(ln for ln in lines if ln and not ln.startswith("-----"))


def _base(request: HttpRequest) -> str:
    return f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"


def saml_settings(request: HttpRequest) -> dict[str, Any]:
    base = _base(request)
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": base + reverse("accounts:saml_metadata"),
            "assertionConsumerService": {
                "url": base + reverse("accounts:saml_acs"),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": NAMEID_UNSPECIFIED,
        },
        "idp": {
            "entityId": settings.SYMPRAXI_SAML_IDP_ENTITY_ID,
            "singleSignOnService": {
                "url": settings.SYMPRAXI_SAML_IDP_SSO_URL,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": pem_body(settings.SYMPRAXI_SAML_IDP_CERT),
        },
        "security": {
            "authnRequestsSigned": False,
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "wantNameId": True,
            "wantAttributeStatement": False,
            "rejectDeprecatedAlgorithm": True,
            "requestedAuthnContext": False,
            "allowRepeatAttributeName": True,
        },
    }


def request_data(request: HttpRequest) -> dict[str, Any]:
    return {
        "https": "on" if request.is_secure() else "off",
        "http_host": request.get_host(),
        "server_port": request.get_port(),
        "script_name": request.path,
        "get_data": request.GET.copy(),
        "post_data": request.POST.copy(),
    }


def auth_for(request: HttpRequest) -> OneLogin_Saml2_Auth:
    return OneLogin_Saml2_Auth(request_data(request), old_settings=saml_settings(request))


def metadata(request: HttpRequest) -> bytes:
    sp = OneLogin_Saml2_Settings(saml_settings(request), sp_validation_only=True)
    document = sp.get_sp_metadata()
    errors = sp.validate_metadata(document)
    if errors:
        raise ValueError(f"invalid SAML metadata: {errors}")
    return document if isinstance(document, bytes) else str(document).encode("utf-8")


def begin(request: HttpRequest, next_url: str) -> str:
    """The address to send the browser to, having kept the request's ID."""
    SamlRequest.objects.filter(created_at__lt=timezone.now() - REQUEST_LIFETIME).delete()
    auth = auth_for(request)
    url = str(auth.login())
    request_id = auth.get_last_request_id()
    if not request_id:
        raise SamlError("no_request", _("The college's sign-in couldn't be started."))
    SamlRequest.objects.create(request_id=request_id, next_url=next_url)
    return url


def _in_response_to(encoded: str) -> str:
    try:
        xml = base64.b64decode(encoded, validate=False)
        root = OneLogin_Saml2_XML.to_etree(xml)
    except Exception as error:  # a malformed document is one answer: refuse it
        raise SamlError(
            "bad_response", _("The college's sign-in service gave an unusable answer.")
        ) from error
    return str(root.get("InResponseTo") or "")


def consume(request: HttpRequest) -> tuple[SamlIdentity, str]:
    """Check the posted response. Returns who signed in and where to go next."""
    encoded = request.POST.get("SAMLResponse", "")
    if not encoded:
        raise SamlError("bad_response", _("The college's sign-in service gave an unusable answer."))
    wanted = _in_response_to(encoded)
    pending = SamlRequest.objects.filter(
        request_id=wanted, created_at__gte=timezone.now() - REQUEST_LIFETIME
    )
    row = pending.first() if wanted else None
    # Used up whether or not the response turns out to be good.
    if row is None or not pending.filter(pk=row.pk).delete()[0]:
        raise SamlError(
            "unknown_request",
            _("That sign-in wasn't one we started, or it took too long. Please try again."),
        )
    auth = auth_for(request)
    auth.process_response(request_id=wanted)
    if auth.get_errors() or not auth.is_authenticated():
        raise SamlError(
            "invalid", _("The college's sign-in service gave an answer we couldn't accept.")
        )
    name_id = str(auth.get_nameid() or "").strip()
    if not name_id:
        raise SamlError("bad_response", _("The college's sign-in service gave an unusable answer."))
    attributes = {
        key: str(values[0]).strip()
        for key, values in auth.get_attributes().items()
        if values and str(values[0]).strip()
    }
    return SamlIdentity(name_id, attributes), row.next_url


def email_of(identity: SamlIdentity) -> str:
    candidate = identity.attributes.get(settings.SYMPRAXI_SAML_EMAIL_ATTRIBUTE, "")
    if not candidate and "@" in identity.name_id:
        candidate = identity.name_id
    email = candidate.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SamlError(
            "no_email",
            _(
                "The college's sign-in service didn't give us your email address, "
                "so we can't sign you in."
            ),
        )
    allowed = settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS
    if not allowed or email.rsplit("@", 1)[-1] not in allowed:
        raise SamlError("wrong_email_domain", _("Please sign in with your college email address."))
    return email


def user_for(identity: SamlIdentity, request: HttpRequest) -> User:
    """The account this sign-in is for, linking or creating it as the rules allow."""
    off = _("That account is turned off. Ask your writing center.")
    link = SamlLink.objects.select_related("user").filter(name_id=identity.name_id).first()
    if link is not None:
        if not link.user.is_active:
            raise SamlError("account_off", off)
        return link.user
    email = email_of(identity)
    existing = User.objects.filter(email__iexact=email).first()
    if existing is not None:
        if SamlLink.objects.filter(user=existing).exists():
            raise SamlError(
                "already_linked",
                _(
                    "The account with this email address is already linked to a different "
                    "college sign-in. Ask your writing center to check it."
                ),
            )
        if not existing.is_active:
            raise SamlError("account_off", off)
        user = existing
    elif settings.SYMPRAXI_SIGNUP_OPEN:
        user = User.objects.create_user(
            email,
            password=None,
            full_name=identity.attributes.get(settings.SYMPRAXI_SAML_NAME_ATTRIBUTE, "")[:150],
            student_id=identity.attributes.get(settings.SYMPRAXI_SAML_STUDENT_ID_ATTRIBUTE) or None,
        )
        record(AuditAction.ACCOUNT_CREATED, request=request, target=user, via="saml")
    else:
        raise SamlError(
            "signup_closed",
            _("There's no account for that address yet. Ask your writing center to add you."),
        )
    SamlLink.objects.create(user=user, name_id=identity.name_id)
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    fill_student_id(user, identity)
    return user


def fill_student_id(user: User, identity: SamlIdentity) -> None:
    value = identity.attributes.get(settings.SYMPRAXI_SAML_STUDENT_ID_ATTRIBUTE, "")
    if not value or user.student_id:
        return
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", value):
        return
    if User.objects.filter(student_id=value).exclude(pk=user.pk).exists():
        return
    user.student_id = value
    user.save(update_fields=["student_id"])
