"""Single sign-on through SAML 2.0."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from sympraxi.accounts import sso_config
from sympraxi.accounts.models import SamlLink, SamlRequest, User
from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests import saml_idp
from tests.factories import CenterFactory, UserFactory, give_mfa
from tests.saml_idp import Idp, request_id_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(settings):
    from django.test import Client

    # python3-saml checks addresses are real-looking, which "testserver" isn't.
    settings.ALLOWED_HOSTS = [saml_idp.HOST]
    return Client(HTTP_HOST=saml_idp.HOST)


@pytest.fixture(scope="module")
def idp():
    return Idp()


@pytest.fixture
def saml(settings, idp):
    settings.SYMPRAXI_SAML_IDP_ENTITY_ID = saml_idp.ENTITY_ID
    settings.SYMPRAXI_SAML_IDP_SSO_URL = saml_idp.SSO_URL
    settings.SYMPRAXI_SAML_IDP_CERT = idp.cert_pem
    settings.SYMPRAXI_SAML_EMAIL_ATTRIBUTE = "mail"
    settings.SYMPRAXI_SAML_NAME_ATTRIBUTE = "displayName"
    settings.SYMPRAXI_SAML_STUDENT_ID_ATTRIBUTE = "employeeNumber"
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    return idp


def signed_in(client):
    pk = client.session.get("_auth_user_id")
    return User.objects.get(pk=pk) if pk else None


def start(client, next_url="/centers/"):
    response = client.get(reverse("accounts:saml_login"), {"next": next_url})
    assert response.status_code == 302 and response["Location"].startswith(saml_idp.SSO_URL)
    return request_id_of(response["Location"])


def post(client, encoded):
    return client.post(reverse("accounts:saml_acs"), {"SAMLResponse": encoded})


def refused_for(reason):
    return AuditEvent.objects.filter(
        action=AuditAction.LOGIN_FAILED, details__reason=f"saml_{reason}"
    ).exists()


def attrs(**changes):
    base = {
        "mail": "sam@example.edu",
        "displayName": "Sam Rivera",
        "employeeNumber": "S7654321",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


# --- Off by default ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["saml_login", "saml_metadata"])
def test_saml_is_off_unless_configured(client, name) -> None:
    assert client.get(reverse(f"accounts:{name}")).status_code == 404


def test_the_acs_is_off_unless_configured(client) -> None:
    assert client.post(reverse("accounts:saml_acs"), {"SAMLResponse": "x"}).status_code == 404


# --- Setting it up ------------------------------------------------------------------------------


def test_the_settings_must_be_complete_secure_and_have_a_domain(idp) -> None:
    check = sso_config.check_saml
    check("", "", "", [])
    check(saml_idp.ENTITY_ID, saml_idp.SSO_URL, idp.cert_pem, ["example.edu"])
    with pytest.raises(ImproperlyConfigured, match="all set"):
        check(saml_idp.ENTITY_ID, "", idp.cert_pem, ["example.edu"])
    with pytest.raises(ImproperlyConfigured, match="https"):
        check(saml_idp.ENTITY_ID, "http://idp.example.edu/sso", idp.cert_pem, ["example.edu"])
    with pytest.raises(ImproperlyConfigured, match="certificate"):
        check(saml_idp.ENTITY_ID, saml_idp.SSO_URL, "not a certificate", ["example.edu"])
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_SIGNUP_EMAIL_DOMAINS"):
        check(saml_idp.ENTITY_ID, saml_idp.SSO_URL, idp.cert_pem, [])


def test_a_certificate_with_escaped_newlines_or_a_bare_body_is_accepted(idp) -> None:
    escaped = idp.cert_pem.replace("\n", "\\n")
    bare = "".join(ln for ln in idp.cert_pem.splitlines() if not ln.startswith("-----"))
    for cert in (escaped, bare):
        sso_config.check_saml(saml_idp.ENTITY_ID, saml_idp.SSO_URL, cert, ["example.edu"])


def test_the_metadata_describes_this_service(client, saml) -> None:
    response = client.get(reverse("accounts:saml_metadata"))
    body = response.content.decode()
    assert response.status_code == 200 and "samlmetadata" in response["Content-Type"]
    assert saml_idp.AUDIENCE in body and saml_idp.ACS in body


def test_the_sign_in_page_offers_saml(client, saml) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert reverse("accounts:saml_login") in html


# --- Starting ---------------------------------------------------------------------------------


def test_starting_keeps_the_requests_id_and_where_to_go(client, saml) -> None:
    request_id = start(client, "/centers/")
    assert SamlRequest.objects.get(request_id=request_id).next_url == "/centers/"


def test_a_next_address_off_the_site_is_dropped(client, saml) -> None:
    request_id = start(client, "https://evil.example/steal")
    assert SamlRequest.objects.get(request_id=request_id).next_url == "/"


# --- A good sign-in ---------------------------------------------------------------------------


def test_a_new_student_signs_in(client, saml) -> None:
    request_id = start(client)
    response = post(client, saml.response(request_id, attributes=attrs()))
    assert response.status_code == 302 and response["Location"] == "/centers/"
    user = signed_in(client)
    assert (user.email, user.full_name, user.student_id) == (
        "sam@example.edu",
        "Sam Rivera",
        "S7654321",
    )
    assert SamlLink.objects.get().name_id == "sam@example.edu"
    assert not SamlRequest.objects.exists()


def test_an_existing_account_is_linked_by_email_then_matched_by_name_id(client, saml) -> None:
    existing = UserFactory(email="sam@example.edu", student_id="KEEP")
    post(client, saml.response(start(client), name_id="pid-1", attributes=attrs()))
    assert signed_in(client) == existing and existing.saml_link.name_id == "pid-1"
    existing.refresh_from_db()
    assert existing.student_id == "KEEP"
    client.logout()
    existing.email = "renamed@example.edu"
    existing.save()
    post(client, saml.response(start(client), name_id="pid-1", attributes=attrs()))
    assert signed_in(client) == existing


def test_the_name_id_can_be_the_address(client, saml) -> None:
    post(client, saml.response(start(client), name_id="kim@example.edu", attributes={}))
    assert signed_in(client).email == "kim@example.edu"


def test_an_address_handed_to_a_new_person_does_not_hand_over_the_account(client, saml) -> None:
    owner = UserFactory(email="sam@example.edu")
    SamlLink.objects.create(user=owner, name_id="someone-else")
    post(client, saml.response(start(client), name_id="pid-2", attributes=attrs()))
    assert signed_in(client) is None and refused_for("already_linked")


def test_staff_through_saml_still_meet_two_factor(client, saml) -> None:
    staff = UserFactory(email="sam@example.edu")
    Membership.objects.create(user=staff, center=CenterFactory(), role=Role.TUTOR)
    give_mfa(staff)
    response = post(client, saml.response(start(client), attributes=attrs()))
    assert response.status_code == 302 and "2fa" in response["Location"]
    assert signed_in(client) is None


def test_sso_only_lets_saml_in_but_not_a_password(client, saml, settings) -> None:
    settings.SYMPRAXI_SSO_ONLY = True
    user = UserFactory(email="sam@example.edu")
    post(client, saml.response(start(client), attributes=attrs()))
    assert signed_in(client) == user
    client.logout()
    other = UserFactory(email="other@example.edu")
    client.post(reverse("account_login"), {"login": other.email, "password": "whatever-password"})
    assert signed_in(client) is None


def test_signup_closed_means_only_accounts_a_director_added(client, saml, settings) -> None:
    settings.SYMPRAXI_SIGNUP_OPEN = False
    post(client, saml.response(start(client), attributes=attrs()))
    assert signed_in(client) is None and refused_for("signup_closed")


def test_a_turned_off_account_cannot_sign_in(client, saml) -> None:
    user = UserFactory(email="sam@example.edu", is_active=False)
    SamlLink.objects.create(user=user, name_id="sam@example.edu")
    post(client, saml.response(start(client), attributes=attrs()))
    assert signed_in(client) is None and refused_for("account_off")


# --- Things that must not sign anyone in --------------------------------------------------------


def refuse(client, saml, reason, **changes):
    request_id = start(client)
    post(client, saml.response(request_id, **changes))
    assert signed_in(client) is None and not User.objects.exists()
    assert refused_for(reason)


@pytest.mark.parametrize(
    "changes",
    [
        {"sign": False},
        {"audience": "https://someone-else.example/"},
        {"destination": "https://elsewhere.example/acs/"},
        {"recipient": "https://elsewhere.example/acs/"},
        {"issuer": "https://rogue.example/idp"},
        {"lifetime": -600},
        {"status": "urn:oasis:names:tc:SAML:2.0:status:Responder"},
        {"sign_response_not_assertion": True},
    ],
    ids=[
        "unsigned",
        "wrong audience",
        "wrong destination",
        "wrong recipient",
        "wrong issuer",
        "expired",
        "failed status",
        "signature on the response only",
    ],
)
def test_responses_that_fail_a_check_sign_nobody_in(client, saml, changes) -> None:
    refuse(client, saml, "invalid", **changes)


def test_a_response_signed_with_another_key_is_refused(client, saml) -> None:
    refuse(client, saml, "invalid", signer=Idp())


def test_a_response_answering_a_different_request_is_refused(client, saml) -> None:
    request_id = start(client)
    post(client, saml.response(request_id, in_response_to="_not_ours"))
    assert signed_in(client) is None and refused_for("unknown_request")
    assert SamlRequest.objects.filter(request_id=request_id).exists()  # still waiting


def test_a_response_works_once(client, saml) -> None:
    encoded = saml.response(start(client), attributes=attrs())
    post(client, encoded)
    assert signed_in(client) is not None
    client.logout()
    post(client, encoded)
    assert signed_in(client) is None and refused_for("unknown_request")


def test_a_response_nobody_asked_for_is_refused(client, saml) -> None:
    post(client, saml.response("_made_up_by_the_idp", attributes=attrs()))
    assert signed_in(client) is None and refused_for("unknown_request")


def test_a_response_after_the_time_allowed_is_refused(client, saml) -> None:
    from datetime import timedelta

    from django.utils import timezone

    request_id = start(client)
    SamlRequest.objects.update(created_at=timezone.now() - timedelta(minutes=30))
    post(client, saml.response(request_id, attributes=attrs()))
    assert signed_in(client) is None and refused_for("unknown_request")


@pytest.mark.parametrize("body", ["", "not base64 xml", "PHJvb3Q+"])
def test_garbage_is_refused(client, saml, body) -> None:
    post(client, body)
    assert signed_in(client) is None
    assert AuditEvent.objects.filter(action=AuditAction.LOGIN_FAILED).exists()


def test_an_address_at_another_college_is_refused(client, saml) -> None:
    refuse(
        client,
        saml,
        "wrong_email_domain",
        name_id="pid-9",
        attributes=attrs(mail="sam@other.edu"),
    )


def test_no_address_anywhere_is_refused(client, saml) -> None:
    refuse(client, saml, "no_email", name_id="pid-9", attributes=attrs(mail=None))


def test_the_acs_takes_only_a_post(client, saml) -> None:
    assert client.get(reverse("accounts:saml_acs")).status_code == 405


def test_the_acs_needs_no_csrf_token(saml, client) -> None:
    from django.test import Client

    strict = Client(enforce_csrf_checks=True, HTTP_HOST=saml_idp.HOST)
    request_id = start(strict)
    assert post(strict, saml.response(request_id, attributes=attrs())).status_code == 302
