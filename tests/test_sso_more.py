"""A generic OpenID Connect provider (Okta and others), the student ID claim, and CAS."""

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from sympraxi.accounts import cas, sso_config
from sympraxi.accounts.models import CasLink, User
from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests import sso_idp
from tests.factories import MembershipFactory, UserFactory
from tests.sso_idp import _token_times, sign_in

pytestmark = pytest.mark.django_db

ISSUER = "https://college.okta.com/oauth2/default"
OIDC_CLIENT = "okta-test-client"


def okta_claims(**changes):
    claims = {
        "iss": ISSUER,
        "aud": OIDC_CLIENT,
        "sub": "okta-subject-1",
        "email": "sam@example.edu",
        "email_verified": True,
        "name": "Sam Rivera",
        "employeeNumber": "S1234567",
        **_token_times(),
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


@pytest.fixture
def okta(settings, idp, monkeypatch):
    """A generic OIDC provider for example.edu, behind the fake provider."""
    apps = sso_idp.providers(entra=False, google=False)
    apps["openid_connect"]["APPS"].append(
        sso_config.oidc_app(
            issuer=ISSUER,
            client_id=OIDC_CLIENT,
            secret="okta-secret",
            name="College sign-in",
            email_domains=["example.edu"],
        )
    )
    settings.SOCIALACCOUNT_PROVIDERS = apps
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    token = "https://college.okta.com/oauth2/default/v1/token"
    discovery = {
        "issuer": ISSUER,
        "authorization_endpoint": "https://college.okta.com/oauth2/default/v1/authorize",
        "token_endpoint": token,
        "jwks_uri": "https://college.okta.com/oauth2/default/v1/keys",
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
    }
    monkeypatch.setitem(sso_idp.DISCOVERY, f"{ISSUER}/.well-known/openid-configuration", discovery)
    monkeypatch.setattr(sso_idp, "TOKEN_ENDPOINTS", {*sso_idp.TOKEN_ENDPOINTS, token})
    return idp


def signed_in(client):
    user_id = client.session.get("_auth_user_id")
    return User.objects.get(pk=user_id) if user_id else None


# --- Setting it up ---


@pytest.fixture
def env(monkeypatch):
    for name in (
        "SYMPRAXI_OIDC_ISSUER",
        "SYMPRAXI_OIDC_CLIENT_ID",
        "SYMPRAXI_OIDC_CLIENT_SECRET",
        "SYMPRAXI_OIDC_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    def build(domains=("example.edu",), **values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        return sso_config.providers_from_env(
            environ.Env(), trust_idp_mfa=False, email_domains=list(domains)
        )

    return build


def test_an_oidc_provider_is_built_from_the_environment(env) -> None:
    [app] = env(
        SYMPRAXI_OIDC_ISSUER=ISSUER + "/",
        SYMPRAXI_OIDC_CLIENT_ID="id",
        SYMPRAXI_OIDC_CLIENT_SECRET="secret",
        SYMPRAXI_OIDC_NAME="Okta",
    )
    assert app["provider_id"] == "oidc" and app["name"] == "Okta"
    assert app["settings"]["server_url"] == ISSUER and app["settings"]["oauth_pkce_enabled"]


def test_the_label_defaults_and_a_half_set_up_provider_stops_the_site(env, monkeypatch) -> None:
    [app] = env(
        SYMPRAXI_OIDC_ISSUER=ISSUER, SYMPRAXI_OIDC_CLIENT_ID="id", SYMPRAXI_OIDC_CLIENT_SECRET="s"
    )
    assert app["name"] == "Single sign-on"
    monkeypatch.delenv("SYMPRAXI_OIDC_CLIENT_ID")
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_OIDC_CLIENT_ID"):
        env()


def test_a_generic_provider_needs_the_colleges_email_domain(env) -> None:
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_SIGNUP_EMAIL_DOMAINS"):
        env(
            domains=(),
            SYMPRAXI_OIDC_ISSUER=ISSUER,
            SYMPRAXI_OIDC_CLIENT_ID="id",
            SYMPRAXI_OIDC_CLIENT_SECRET="s",
        )


@pytest.mark.parametrize(
    "issuer", ["http://college.okta.com", "college.okta.com", "https://x.com/?a=b"]
)
def test_the_issuer_must_be_https(env, issuer) -> None:
    with pytest.raises(ImproperlyConfigured, match="https"):
        env(
            SYMPRAXI_OIDC_ISSUER=issuer,
            SYMPRAXI_OIDC_CLIENT_ID="id",
            SYMPRAXI_OIDC_CLIENT_SECRET="s",
        )


def test_the_providers_origin_is_allowed_for_the_sign_in_redirect(env) -> None:
    apps = env(
        SYMPRAXI_OIDC_ISSUER=ISSUER, SYMPRAXI_OIDC_CLIENT_ID="id", SYMPRAXI_OIDC_CLIENT_SECRET="s"
    )
    assert sso_config.login_origins(apps) == ["https://college.okta.com"]


# --- Signing in ---


def test_a_student_signs_in_through_the_generic_provider(client, okta) -> None:
    response = sign_in(client, okta, "oidc", okta_claims())
    assert response.status_code == 302 and response["Location"] == "/"
    user = signed_in(client)
    assert user is not None and user.email == "sam@example.edu"
    assert user.full_name == "Sam Rivera"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"email_verified": False}, "email_not_verified"),
        ({"email_verified": None}, "email_not_verified"),
        ({"email": "sam@other.edu"}, "wrong_email_domain"),
        ({"email": None}, "email_not_verified"),
    ],
)
def test_the_generic_provider_must_vouch_for_a_college_address(
    client, okta, changes, reason
) -> None:
    response = sign_in(client, okta, "oidc", okta_claims(**changes))
    assert response["Location"] == reverse("account_login")
    assert signed_in(client) is None and not User.objects.exists()
    failed = AuditEvent.objects.get(action=AuditAction.LOGIN_FAILED)
    assert failed.details["reason"] == reason


def test_the_button_appears_on_the_sign_in_page(client, okta) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "College sign-in" in html


# --- The student ID claim ---


def test_a_new_account_takes_the_student_id_from_the_claim(client, okta, settings) -> None:
    settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM = "employeeNumber"
    sign_in(client, okta, "oidc", okta_claims())
    assert signed_in(client).student_id == "S1234567"


def test_a_missing_student_id_is_filled_in_and_a_set_one_is_never_overwritten(
    client, okta, settings
) -> None:
    settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM = "employeeNumber"
    blank = UserFactory(email="sam@example.edu", student_id=None)
    sign_in(client, okta, "oidc", okta_claims())
    blank.refresh_from_db()
    assert blank.student_id == "S1234567"
    assert AuditEvent.objects.filter(action=AuditAction.SSO_STUDENT_ID_SET).count() == 1
    client.logout()
    blank.student_id = "DIFFERENT"
    blank.save()
    sign_in(client, okta, "oidc", okta_claims())
    blank.refresh_from_db()
    assert blank.student_id == "DIFFERENT"
    assert AuditEvent.objects.filter(action=AuditAction.SSO_STUDENT_ID_MISMATCH).count() == 1


def test_a_student_id_already_on_another_account_is_left_alone(client, okta, settings) -> None:
    settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM = "employeeNumber"
    UserFactory(student_id="S1234567")
    mine = UserFactory(email="sam@example.edu", student_id=None)
    sign_in(client, okta, "oidc", okta_claims())
    mine.refresh_from_db()
    assert mine.student_id is None
    event = AuditEvent.objects.get(action=AuditAction.SSO_STUDENT_ID_MISMATCH)
    assert event.details["conflict"] is True


@pytest.mark.parametrize("value", ["", "has space", "a" * 80, "bad;chars", None])
def test_an_unusable_student_id_claim_is_ignored(client, okta, settings, value) -> None:
    settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM = "employeeNumber"
    sign_in(client, okta, "oidc", okta_claims(employeeNumber=value))
    assert signed_in(client).student_id is None


def test_with_no_claim_named_nothing_is_taken(client, okta, settings) -> None:
    settings.SYMPRAXI_SSO_STUDENT_ID_CLAIM = ""
    sign_in(client, okta, "oidc", okta_claims())
    assert signed_in(client).student_id is None


# --- CAS ---


SUCCESS = """<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
  <cas:authenticationSuccess>
    <cas:user>sam123</cas:user>
    <cas:attributes>
      <cas:mail>Sam@Example.edu</cas:mail>
      <cas:displayName>Sam Rivera</cas:displayName>
      <cas:studentNumber>S7654321</cas:studentNumber>
    </cas:attributes>
  </cas:authenticationSuccess>
</cas:serviceResponse>"""
FAILURE = """<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
  <cas:authenticationFailure code="INVALID_TICKET">Ticket not recognized</cas:authenticationFailure>
</cas:serviceResponse>"""


@pytest.fixture
def cas_on(settings, monkeypatch):
    settings.SYMPRAXI_CAS_SERVER_URL = "https://cas.example.edu/cas"
    settings.SYMPRAXI_CAS_VERSION = 3
    settings.SYMPRAXI_CAS_EMAIL_ATTRIBUTE = "mail"
    settings.SYMPRAXI_CAS_NAME_ATTRIBUTE = "displayName"
    settings.SYMPRAXI_CAS_STUDENT_ID_ATTRIBUTE = "studentNumber"
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    asked: list[str] = []
    answers = {"body": SUCCESS}

    def fake(url: str) -> bytes:
        asked.append(url)
        return answers["body"].encode()

    monkeypatch.setattr(cas, "fetch", fake)
    return asked, answers


def come_back(client, ticket="ST-1-abc"):
    return client.get(reverse("accounts:cas_callback"), {"ticket": ticket})


def test_cas_is_off_unless_configured(client) -> None:
    assert client.get(reverse("accounts:cas_login")).status_code == 404
    assert client.get(reverse("accounts:cas_callback")).status_code == 404


def test_cas_needs_https_and_a_college_domain() -> None:
    with pytest.raises(ImproperlyConfigured, match="https"):
        sso_config.check_cas("http://cas.example.edu/cas", ["example.edu"])
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_SIGNUP_EMAIL_DOMAINS"):
        sso_config.check_cas("https://cas.example.edu/cas", [])
    sso_config.check_cas("", [])
    sso_config.check_cas("https://cas.example.edu/cas", ["example.edu"])


def test_the_sign_in_page_offers_cas_and_the_button_goes_to_the_cas_server(client, cas_on) -> None:
    assert (
        "Sign in with your college account" in client.get(reverse("account_login")).content.decode()
    )
    response = client.get(reverse("accounts:cas_login"), {"next": "/centers/"})
    location = response["Location"]
    assert location.startswith("https://cas.example.edu/cas/login?service=")
    assert "%2Faccounts%2Fcas%2Freturn%2F" in location
    assert client.session["sympraxi_cas_next"] == "/centers/"


def test_a_next_address_off_the_site_is_dropped(client, cas_on) -> None:
    client.get(reverse("accounts:cas_login"), {"next": "https://evil.example/steal"})
    assert client.session["sympraxi_cas_next"] == "/"


def test_a_new_student_signs_in_through_cas(client, cas_on) -> None:
    asked, _ = cas_on
    client.get(reverse("accounts:cas_login"), {"next": "/centers/"})
    response = come_back(client)
    assert response.status_code == 302 and response["Location"] == "/centers/"
    user = signed_in(client)
    assert (user.email, user.full_name, user.student_id) == (
        "sam@example.edu",
        "Sam Rivera",
        "S7654321",
    )
    assert CasLink.objects.get().cas_user == "sam123"
    assert asked[0].startswith("https://cas.example.edu/cas/p3/serviceValidate?")
    assert "ticket=ST-1-abc" in asked[0] and "service=" in asked[0]


def test_an_existing_account_is_linked_by_email_then_matched_by_cas_name(client, cas_on) -> None:
    existing = UserFactory(email="sam@example.edu", student_id="KEEP")
    come_back(client)
    assert signed_in(client) == existing and existing.cas_link.cas_user == "sam123"
    existing.refresh_from_db()
    assert existing.student_id == "KEEP"
    client.logout()
    existing.email = "renamed@example.edu"
    existing.save()
    come_back(client)
    assert signed_in(client) == existing  # matched on the CAS name, not the address


def test_an_address_handed_to_a_new_person_does_not_hand_over_the_account(client, cas_on) -> None:
    owner = UserFactory(email="sam@example.edu")
    CasLink.objects.create(user=owner, cas_user="someone-else")
    response = come_back(client)
    assert response["Location"] == reverse("account_login")
    assert signed_in(client) is None
    failed = AuditEvent.objects.get(action=AuditAction.LOGIN_FAILED)
    assert failed.details["reason"] == "cas_already_linked"


def test_a_refused_ticket_a_bad_ticket_and_an_unreachable_server_sign_nobody_in(
    client, cas_on
) -> None:
    _, answers = cas_on
    answers["body"] = FAILURE
    assert come_back(client)["Location"] == reverse("account_login")
    assert come_back(client, ticket="has spaces & symbols")["Location"] == reverse("account_login")
    assert client.get(reverse("accounts:cas_callback"))["Location"] == reverse("account_login")
    assert signed_in(client) is None and not User.objects.exists()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (SUCCESS.replace("Sam@Example.edu", "sam@other.edu"), "wrong_email_domain"),
        (SUCCESS.replace("<cas:mail>Sam@Example.edu</cas:mail>", ""), "no_email"),
        ("<not xml", "bad_response"),
        ('<!DOCTYPE x [<!ENTITY a "b">]>' + SUCCESS, "bad_response"),
        (SUCCESS.replace("<cas:user>sam123</cas:user>", "<cas:user> </cas:user>"), "bad_response"),
    ],
)
def test_unusable_cas_answers_are_refused_for_the_right_reason(
    client, cas_on, body, reason
) -> None:
    _, answers = cas_on
    answers["body"] = body
    come_back(client)
    assert signed_in(client) is None
    assert (
        AuditEvent.objects.get(action=AuditAction.LOGIN_FAILED).details["reason"] == f"cas_{reason}"
    )


def test_the_cas_name_can_be_an_address(client, cas_on) -> None:
    _, answers = cas_on
    answers["body"] = (
        '<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas"><cas:authenticationSuccess>'
        "<cas:user>kim@example.edu</cas:user></cas:authenticationSuccess></cas:serviceResponse>"
    )
    come_back(client)
    assert signed_in(client).email == "kim@example.edu"


def test_signup_closed_means_only_accounts_a_director_added(client, cas_on, settings) -> None:
    settings.SYMPRAXI_SIGNUP_OPEN = False
    come_back(client)
    assert signed_in(client) is None and not User.objects.exists()


def test_a_turned_off_account_cannot_sign_in_through_cas(client, cas_on) -> None:
    user = UserFactory(email="sam@example.edu", is_active=False)
    CasLink.objects.create(user=user, cas_user="sam123")
    come_back(client)
    assert signed_in(client) is None


def test_staff_through_cas_still_meet_two_factor(client, cas_on) -> None:
    from tests.factories import give_mfa

    staff = UserFactory(email="sam@example.edu")
    MembershipFactory(
        user=staff,
        center=__import__("tests.factories", fromlist=["CenterFactory"]).CenterFactory(),
        role=Role.TUTOR,
    )
    give_mfa(staff)
    response = come_back(client)
    assert response.status_code == 302 and "2fa" in response["Location"]
    assert signed_in(client) is None


def test_sso_only_lets_cas_sign_in_but_not_a_password(client, cas_on, settings) -> None:
    settings.SYMPRAXI_SSO_ONLY = True
    user = UserFactory(email="sam@example.edu")
    assert come_back(client).status_code == 302 and signed_in(client) == user
    client.logout()
    other = UserFactory(email="other@example.edu")
    response = client.post(
        reverse("account_login"), {"login": other.email, "password": "whatever-password"}
    )
    assert signed_in(client) is None and response.status_code in (200, 302)
