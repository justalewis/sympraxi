"""LTI 1.3 launch from a learning management system."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.urls import reverse

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.lti import launch as flow
from sympraxi.lti.models import LtiLink, LtiNonce, LtiPlatform
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ISSUER = "https://canvas.example.edu"
CLIENT = "10000000000042"
DEPLOYMENT = "1:abc123"
LOGIN = "https://canvas.example.edu/api/lti/authorize_redirect"
KEYS = "https://canvas.example.edu/api/lti/security/jwks"
CLAIM = flow.CLAIM


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_of(key, kid="k1"):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**public, "kid": kid, "alg": "RS256", "use": "sig"}]}


@pytest.fixture
def platform(db, settings, monkeypatch, key):
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    center = Center.objects.create(name="Writing Center", slug="wc")
    monkeypatch.setattr(flow, "fetch_keys", lambda url: jwks_of(key))
    return LtiPlatform.objects.create(
        center=center,
        name="Canvas",
        issuer=ISSUER,
        client_id=CLIENT,
        login_url=LOGIN,
        jwks_url=KEYS,
        deployment_ids=[DEPLOYMENT],
    )


def start(client, **extra):
    data = {"iss": ISSUER, "login_hint": "hint", "client_id": CLIENT, **extra}
    response = client.post(reverse("lti:login"), data)
    assert response.status_code == 302, response.content
    query = parse_qs(urlsplit(response["Location"]).query)
    return response, {k: v[0] for k, v in query.items()}


def token_for(key, query, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "canvas-user-1",
        "iat": now - 5,
        "exp": now + 300,
        "nonce": query["nonce"],
        "email": "sam@example.edu",
        "name": "Sam Rivera",
        CLAIM + "version": "1.3.0",
        CLAIM + "message_type": "LtiResourceLinkRequest",
        CLAIM + "deployment_id": DEPLOYMENT,
        CLAIM + "roles": ["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"],
    }
    claims.update(changes)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def post_launch(client, key, query, **changes):
    return client.post(
        reverse("lti:launch"),
        {"state": query["state"], "id_token": token_for(key, query, **changes)},
    )


def signed_in(client):
    pk = client.session.get("_auth_user_id")
    return User.objects.get(pk=pk) if pk else None


def refusal(reason):
    return AuditEvent.objects.filter(
        action=AuditAction.LOGIN_FAILED, details__reason=f"lti_{reason}"
    ).exists()


# --- Starting a login -----------------------------------------------------------------------


def test_the_login_start_sends_the_browser_to_the_platform(client, platform) -> None:
    response, query = start(client, lti_message_hint="m1")
    assert response["Location"].startswith(LOGIN + "?")
    assert query["client_id"] == CLIENT and query["login_hint"] == "hint"
    assert query["response_type"] == "id_token" and query["response_mode"] == "form_post"
    assert query["prompt"] == "none" and query["scope"] == "openid"
    assert query["lti_message_hint"] == "m1"
    assert query["redirect_uri"].endswith(reverse("lti:launch"))
    assert LtiNonce.objects.filter(pk=query["nonce"]).exists()


def test_the_login_start_also_works_as_a_get(client, platform) -> None:
    response = client.get(reverse("lti:login"), {"iss": ISSUER, "login_hint": "h"})
    assert response.status_code == 302


def test_an_unknown_platform_or_missing_hint_is_refused(client, platform) -> None:
    assert client.post(reverse("lti:login"), {"iss": ISSUER}).status_code == 400
    response = client.post(reverse("lti:login"), {"iss": "https://other.edu", "login_hint": "h"})
    assert response.status_code == 400 and refusal("unknown_platform")
    wrong_client = client.post(
        reverse("lti:login"), {"iss": ISSUER, "login_hint": "h", "client_id": "nope"}
    )
    assert wrong_client.status_code == 400


def test_a_platform_turned_off_cannot_start_a_login(client, platform) -> None:
    platform.is_active = False
    platform.save()
    assert client.post(reverse("lti:login"), {"iss": ISSUER, "login_hint": "h"}).status_code == 400


# --- The launch ---------------------------------------------------------------------------


def test_a_launch_signs_a_new_student_in_and_makes_them_a_student(client, platform, key) -> None:
    _, query = start(client)
    response = post_launch(client, key, query)
    assert response.status_code == 302 and response["Location"] == "/centers/wc/"
    user = signed_in(client)
    assert user.email == "sam@example.edu" and user.full_name == "Sam Rivera"
    membership = Membership.objects.get(user=user, center=platform.center)
    assert membership.role == Role.STUDENT  # the instructor role claim is ignored
    assert LtiLink.objects.get().subject == "canvas-user-1"


def test_an_existing_account_is_linked_by_email_and_keeps_its_roles(client, platform, key) -> None:
    tutor = UserFactory(email="sam@example.edu")
    Membership.objects.create(user=tutor, center=platform.center, role=Role.TUTOR)
    _, query = start(client)
    post_launch(client, key, query)
    assert Membership.objects.filter(user=tutor, center=platform.center).count() == 1
    assert LtiLink.objects.get().user == tutor


def test_later_launches_match_on_the_platforms_identifier(client, platform, key) -> None:
    user = UserFactory(email="old@example.edu")
    LtiLink.objects.create(platform=platform, user=user, subject="canvas-user-1")
    _, query = start(client)
    post_launch(client, key, query, email="new@example.edu")
    assert signed_in(client) == user


def test_an_address_belonging_to_an_account_linked_to_someone_else_is_refused(
    client, platform, key
) -> None:
    owner = UserFactory(email="sam@example.edu")
    LtiLink.objects.create(platform=platform, user=owner, subject="someone-else")
    _, query = start(client)
    response = post_launch(client, key, query)
    assert response.status_code == 400 and signed_in(client) is None
    assert refusal("already_linked")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"email": "sam@other.edu"}, "wrong_email_domain"),
        ({"email": None}, "no_email"),
        ({"email": "not an address"}, "no_email"),
        ({CLAIM + "deployment_id": "9:other"}, "bad_deployment"),
        ({CLAIM + "version": "1.1.0"}, "bad_message"),
        ({CLAIM + "message_type": "LtiDeepLinkingRequest"}, "bad_message"),
        ({"iss": "https://evil.example"}, "bad_token"),
        ({"aud": "someone-else"}, "bad_token"),
        ({"exp": 1000, "iat": 900}, "bad_token"),
        ({"nonce": "not-ours"}, "bad_nonce"),
        ({"aud": [CLIENT, "x"]}, "bad_audience"),
    ],
)
def test_launches_that_fail_a_check_sign_nobody_in(client, platform, key, changes, reason) -> None:
    _, query = start(client)
    response = post_launch(client, key, query, **changes)
    assert response.status_code == 400
    assert signed_in(client) is None and not User.objects.exists()
    assert refusal(reason)


def test_a_token_signed_with_a_different_key_is_refused(client, platform) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _, query = start(client)
    assert post_launch(client, attacker, query).status_code == 400
    assert refusal("bad_token") and signed_in(client) is None


def test_an_unsigned_token_is_refused(client, platform) -> None:
    _, query = start(client)
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": "x",
            "iat": now,
            "exp": now + 99,
            "nonce": query["nonce"],
        },
        key=None,
        algorithm="none",
        headers={"kid": "k1"},
    )
    response = client.post(reverse("lti:launch"), {"state": query["state"], "id_token": forged})
    assert response.status_code == 400 and signed_in(client) is None


def test_a_launch_works_only_once(client, platform, key) -> None:
    _, query = start(client)
    assert post_launch(client, key, query).status_code == 302
    client.logout()
    assert post_launch(client, key, query).status_code == 400
    assert refusal("replayed")


def test_a_tampered_or_old_state_is_refused(client, platform, key) -> None:
    _, query = start(client)
    response = client.post(
        reverse("lti:launch"),
        {"state": query["state"][:-3] + "abc", "id_token": token_for(key, query)},
    )
    assert response.status_code == 400 and refusal("bad_state")


def test_a_state_past_its_time_is_refused(client, platform, key, monkeypatch) -> None:
    _, query = start(client)
    monkeypatch.setattr(flow, "STATE_MAX_AGE", -1)
    assert post_launch(client, key, query).status_code == 400
    assert refusal("bad_state")


def test_a_launch_does_not_need_a_csrf_token_or_a_session(client, platform, key) -> None:
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    _, query = start(strict)
    assert post_launch(strict, key, query).status_code == 302


def test_a_get_to_the_launch_address_is_not_allowed(client, platform) -> None:
    assert client.get(reverse("lti:launch")).status_code == 405


def test_a_launch_for_a_turned_off_account_is_refused(client, platform, key) -> None:
    user = UserFactory(email="sam@example.edu", is_active=False)
    LtiLink.objects.create(platform=platform, user=user, subject="canvas-user-1")
    _, query = start(client)
    assert post_launch(client, key, query).status_code == 400 and refusal("account_off")


def test_when_signup_is_closed_only_existing_accounts_launch(
    client, platform, key, settings
) -> None:
    settings.SYMPRAXI_SIGNUP_OPEN = False
    _, query = start(client)
    assert post_launch(client, key, query).status_code == 400
    assert refusal("signup_closed")


def test_unreachable_keys_refuse_the_launch(client, platform, key, monkeypatch) -> None:
    def down(url):
        raise flow.LtiError("keys_unreachable", "down")

    _, query = start(client)
    monkeypatch.setattr(flow, "fetch_keys", down)
    assert post_launch(client, key, query).status_code == 400
    assert refusal("keys_unreachable")


def test_staff_launches_still_meet_two_factor(client, platform, key) -> None:
    from tests.factories import give_mfa

    staff = UserFactory(email="sam@example.edu")
    Membership.objects.create(user=staff, center=platform.center, role=Role.TUTOR)
    give_mfa(staff)
    _, query = start(client)
    response = post_launch(client, key, query)
    assert response.status_code == 302 and "2fa" in response["Location"]
    assert signed_in(client) is None


# --- The tool's description ---------------------------------------------------------------


def test_the_config_describes_the_tool_to_canvas(client, db) -> None:
    data = client.get(reverse("lti:config")).json()
    assert data["oidc_initiation_url"].endswith("/lti/login/")
    assert data["target_link_uri"].endswith("/lti/launch/")
    [extension] = data["extensions"]
    assert extension["settings"]["placements"][0]["placement"] == "course_navigation"
