"""Single sign-on: who gets in, which accounts get linked, and who still needs 2FA.

The identity provider is faked (tests/sso_idp.py). Everything on Sympraxi's side,
allauth's views and checks included, runs for real.
"""

import json
from urllib.parse import parse_qs, urlparse

import environ
import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from sympraxi.accounts import sso, sso_config
from sympraxi.accounts.models import User
from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests import sso_idp
from tests.factories import PASSWORD, MembershipFactory, UserFactory, give_mfa
from tests.sso_idp import OTHER_TENANT, TENANT, entra_claims, google_claims, sign_in

pytestmark = pytest.mark.django_db

ENV_NAMES = [
    "SYMPRAXI_ENTRA_TENANT_ID",
    "SYMPRAXI_ENTRA_CLIENT_ID",
    "SYMPRAXI_ENTRA_CLIENT_SECRET",
    "SYMPRAXI_GOOGLE_HOSTED_DOMAIN",
    "SYMPRAXI_GOOGLE_CLIENT_ID",
    "SYMPRAXI_GOOGLE_CLIENT_SECRET",
]


def signed_in_user(client) -> User | None:
    user_id = client.session.get("_auth_user_id")
    return User.objects.get(pk=user_id) if user_id else None


def audit(action: AuditAction) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


def redirect_query(response) -> dict[str, list[str]]:
    return parse_qs(urlparse(response["Location"]).query)


# --- Configuration ------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    def build(**values: str) -> list[dict]:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        return sso_config.providers_from_env(environ.Env(), trust_idp_mfa=False)

    return build


def test_no_variables_means_no_providers(env) -> None:
    assert env() == []


def test_entra_is_pinned_to_the_college_tenant(env) -> None:
    [app] = env(
        SYMPRAXI_ENTRA_TENANT_ID=TENANT.upper(),
        SYMPRAXI_ENTRA_CLIENT_ID="client",
        SYMPRAXI_ENTRA_CLIENT_SECRET="secret",
    )
    assert app["provider_id"] == "entra"
    assert app["secret"] == "secret"
    assert app["settings"]["tenant_id"] == TENANT
    assert app["settings"]["server_url"] == f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert app["settings"]["oauth_pkce_enabled"] is True
    assert app["settings"]["fetch_userinfo"] is False


@pytest.mark.parametrize(
    "tenant", ["common", "organizations", "consumers", "Common", "example.onmicrosoft.com"]
)
def test_shared_endpoints_and_domain_names_are_refused_as_tenants(env, tenant) -> None:
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_ENTRA_TENANT_ID"):
        env(
            SYMPRAXI_ENTRA_TENANT_ID=tenant,
            SYMPRAXI_ENTRA_CLIENT_ID="client",
            SYMPRAXI_ENTRA_CLIENT_SECRET="secret",
        )


def test_a_half_set_up_provider_stops_the_site_from_starting(env) -> None:
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_ENTRA_TENANT_ID"):
        env(SYMPRAXI_ENTRA_CLIENT_ID="client", SYMPRAXI_ENTRA_CLIENT_SECRET="secret")


def test_google_needs_a_hosted_domain(env) -> None:
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_GOOGLE_HOSTED_DOMAIN"):
        env(SYMPRAXI_GOOGLE_CLIENT_ID="client", SYMPRAXI_GOOGLE_CLIENT_SECRET="secret")


def test_google_is_set_up_for_the_hosted_domain(env) -> None:
    [app] = env(
        SYMPRAXI_GOOGLE_HOSTED_DOMAIN="@Example.edu",
        SYMPRAXI_GOOGLE_CLIENT_ID="client",
        SYMPRAXI_GOOGLE_CLIENT_SECRET="secret",
    )
    assert app["provider_id"] == "google"
    assert app["settings"]["hosted_domain"] == "example.edu"
    assert app["settings"]["auth_params"] == {"hd": "example.edu"}


def test_sso_only_needs_a_provider() -> None:
    with pytest.raises(ImproperlyConfigured, match="SYMPRAXI_SSO_ONLY"):
        sso_config.check_sso_only(True, [])
    sso_config.check_sso_only(False, [])


def test_a_provider_that_is_not_set_up_has_no_pages(client) -> None:
    assert client.get("/accounts/oidc/entra/login/").status_code == 404
    html = client.get(reverse("account_login")).content.decode()
    assert "sympraxi-sso" not in html
    assert "Sign in with Microsoft" not in html


def test_a_provider_saved_in_the_database_is_ignored(client) -> None:
    SocialApp.objects.create(
        provider="openid_connect",
        provider_id="entra",
        name="Saved in the database",
        client_id="client",
        secret="secret",
        settings={"server_url": "https://login.example.net"},
    )
    assert client.get("/accounts/oidc/entra/login/").status_code == 404


# --- The sign-in page and the redirect to the provider --------------------------


def test_sign_in_page_offers_sso_and_a_password(client, sso) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "Sign in with Microsoft" in html
    assert "Sign in with Google" in html
    assert 'name="password"' in html
    assert "Create an account" in html


def test_entra_sign_in_goes_to_the_college_tenant(client, sso) -> None:
    response = sso_idp.start(client, "entra")
    assert response.status_code == 302
    assert response["Location"].startswith(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?"
    )
    query = redirect_query(response)
    assert set(query["scope"][0].split()) == {"openid", "profile", "email"}
    assert query["code_challenge_method"] == ["S256"]
    assert "claims" not in query


def test_google_sign_in_asks_for_the_college_domain(client, sso) -> None:
    response = sso_idp.start(client, "google")
    assert response["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = redirect_query(response)
    assert query["hd"] == ["example.edu"]
    assert "claims" not in query


def test_opening_a_provider_link_does_not_start_sign_in(client, sso) -> None:
    # Starting takes a POST, so a link on another site can't start a sign-in.
    response = client.get("/accounts/oidc/entra/login/")
    assert response.status_code == 200
    assert "Continue" in response.content.decode()


# --- Who gets in ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_id", "claims"),
    [("entra", entra_claims()), ("google", google_claims())],
    ids=["entra", "google"],
)
def test_a_new_student_signs_in(client, sso, provider_id, claims) -> None:
    response = sign_in(client, sso, provider_id, claims)
    assert response.status_code == 302
    assert response["Location"] == "/"
    user = signed_in_user(client)
    assert user is not None
    assert (user.email, user.full_name) == ("sam@example.edu", "Sam Rivera")
    assert not user.has_usable_password()
    assert EmailAddress.objects.get(user=user).verified
    assert SocialAccount.objects.get(user=user).provider == provider_id
    [linked] = audit(AuditAction.SSO_LINKED)
    assert linked.actor_id == user.pk
    assert linked.details == {"provider": provider_id, "how": "new_account"}


# Identity tokens Sympraxi must refuse, and the reason the audit log records.
REFUSED = [
    ("entra", entra_claims(xms_edov=None), "email_not_verified"),
    ("entra", entra_claims(xms_edov=False), "email_not_verified"),
    ("entra", entra_claims(email=None), "email_not_verified"),
    ("google", google_claims(email_verified=False), "email_not_verified"),
    ("google", google_claims(email_verified=None), "email_not_verified"),
    ("entra", entra_claims(email="sam@other.edu"), "wrong_email_domain"),
    ("google", google_claims(email="sam@other.edu"), "wrong_email_domain"),
    # A token for the right issuer whose tenant claim disagrees.
    ("entra", entra_claims(tid=OTHER_TENANT), "wrong_organization"),
    ("entra", entra_claims(tid=None), "wrong_organization"),
    ("google", google_claims(hd="other.edu"), "wrong_organization"),
    # A personal Google account has no hosted domain.
    ("google", google_claims(hd=None, email="sam@gmail.com"), "wrong_organization"),
]
REFUSED_IDS = [
    "entra-no-xms_edov",
    "entra-xms_edov-false",
    "entra-no-email",
    "google-unverified",
    "google-no-email_verified",
    "entra-wrong-domain",
    "google-wrong-domain",
    "entra-wrong-tid",
    "entra-no-tid",
    "google-wrong-hd",
    "google-personal-account",
]


@pytest.mark.parametrize(("provider_id", "claims", "reason"), REFUSED, ids=REFUSED_IDS)
def test_sign_in_is_refused(client, sso, provider_id, claims, reason) -> None:
    response = sign_in(client, sso, provider_id, claims)
    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert signed_in_user(client) is None
    assert not User.objects.exists()
    assert not SocialAccount.objects.exists()
    [failed] = audit(AuditAction.LOGIN_FAILED)
    assert failed.details == {"provider": provider_id, "reason": reason}


def test_a_token_from_another_tenant_is_refused(client, sso) -> None:
    # allauth checks the issuer against the college tenant's discovery document.
    claims = entra_claims(
        iss=f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0", tid=OTHER_TENANT
    )
    response = sign_in(client, sso, "entra", claims)
    assert "Sign-in didn't work" in response.content.decode()
    assert signed_in_user(client) is None
    assert not User.objects.exists()
    [failed] = audit(AuditAction.LOGIN_FAILED)
    assert failed.details == {"provider": "entra", "reason": "provider_unknown"}


def test_a_token_for_another_app_is_refused(client, sso) -> None:
    sign_in(client, sso, "google", google_claims(aud="someone-elses-client"))
    assert signed_in_user(client) is None
    assert not User.objects.exists()


def test_closed_sign_up_closes_sso_sign_up_too(client, sso, settings) -> None:
    settings.SYMPRAXI_SIGNUP_OPEN = False
    sign_in(client, sso, "entra", entra_claims())
    assert not User.objects.exists()


# --- Linking to existing accounts ---------------------------------------------


def test_an_existing_account_is_linked_by_its_verified_address(client, sso) -> None:
    user = UserFactory(email="sam@example.edu")
    sign_in(client, sso, "entra", entra_claims())
    assert signed_in_user(client) == user
    assert User.objects.count() == 1
    assert SocialAccount.objects.get(provider="entra").user == user
    user.refresh_from_db()
    assert user.check_password(PASSWORD)  # their password still works
    [linked] = audit(AuditAction.SSO_LINKED)
    assert linked.actor_id == user.pk
    assert linked.details == {"provider": "entra", "how": "email_match"}


def test_later_sign_ins_follow_the_provider_id_not_the_address(client, sso) -> None:
    user = UserFactory(email="sam@example.edu")
    sign_in(client, sso, "entra", entra_claims())
    client.logout()
    sign_in(client, sso, "entra", entra_claims(email="samantha.rivera@example.edu"))
    assert signed_in_user(client) == user
    assert SocialAccount.objects.count() == 1


def test_linking_an_unverified_account_removes_its_password(client, sso) -> None:
    # Someone may have registered the address first, hoping to share the account.
    squatter = UserFactory(email="sam@example.edu", verified=False)
    sign_in(client, sso, "entra", entra_claims())
    squatter.refresh_from_db()
    assert not squatter.has_usable_password()
    assert SocialAccount.objects.get().user == squatter


def test_a_signed_up_squatter_loses_the_password_before_the_address_is_verified(
    client, sso
) -> None:
    """Signing up leaves an unverified address row. Single sign-on marks the address
    verified, but only after allauth has taken the squatter's password away."""
    squatter = UserFactory(email="sam@example.edu", verified=False)
    EmailAddress.objects.create(user=squatter, email=squatter.email, primary=True, verified=False)
    sign_in(client, sso, "entra", entra_claims())
    squatter.refresh_from_db()
    assert not squatter.has_usable_password()
    assert EmailAddress.objects.get(user=squatter).verified
    assert signed_in_user(client) == squatter


@pytest.mark.parametrize(("provider_id", "claims", "reason"), REFUSED, ids=REFUSED_IDS)
def test_a_refused_sign_in_never_links_an_account(client, sso, provider_id, claims, reason) -> None:
    user = UserFactory(email=claims.get("email", "sam@example.edu"))
    sign_in(client, sso, provider_id, claims)
    assert signed_in_user(client) is None
    assert not SocialAccount.objects.exists()
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert not audit(AuditAction.SSO_LINKED)


def test_an_address_linked_to_someone_else_is_not_linked_again(client, sso) -> None:
    # A college can hand an old address to a new person; they must not get the
    # previous holder's records.
    UserFactory(email="sam@example.edu")
    sign_in(client, sso, "entra", entra_claims(sub="first-holder"))
    client.logout()
    sign_in(client, sso, "entra", entra_claims(sub="second-holder"))
    assert signed_in_user(client) is None
    assert list(SocialAccount.objects.values_list("uid", flat=True)) == ["first-holder"]
    [failed] = audit(AuditAction.LOGIN_FAILED)
    assert failed.details == {"provider": "entra", "reason": "already_linked"}


def test_linking_from_the_account_page_is_recorded(client, sso) -> None:
    user = UserFactory(email="sam@example.edu")
    client.force_login(user)
    response = sign_in(client, sso, "google", google_claims(), process="connect")
    assert response["Location"] == reverse("socialaccount_connections")
    assert SocialAccount.objects.get().user == user
    [linked] = audit(AuditAction.SSO_LINKED)
    assert linked.details == {"provider": "google", "how": "connect"}


def test_linking_from_the_account_page_follows_the_same_rules(client, sso) -> None:
    client.force_login(UserFactory(email="sam@example.edu"))
    response = sign_in(client, sso, "google", google_claims(hd="other.edu"), process="connect")
    assert response["Location"] == reverse("socialaccount_connections")
    assert not SocialAccount.objects.exists()


def test_unlinking_is_recorded(client, sso) -> None:
    user = UserFactory(email="sam@example.edu")
    sign_in(client, sso, "entra", entra_claims())
    account = SocialAccount.objects.get()
    response = client.post(reverse("socialaccount_connections"), {"account": account.pk})
    assert response.status_code == 302
    assert not SocialAccount.objects.exists()
    [unlinked] = audit(AuditAction.SSO_UNLINKED)
    assert unlinked.actor_id == user.pk
    assert unlinked.details == {"provider": "entra"}


def test_an_account_without_a_password_keeps_its_last_link(client, sso) -> None:
    sign_in(client, sso, "entra", entra_claims())
    account = SocialAccount.objects.get()
    response = client.post(reverse("socialaccount_connections"), {"account": account.pk})
    assert response.status_code == 200
    assert SocialAccount.objects.exists()
    assert not audit(AuditAction.SSO_UNLINKED)


def test_linked_accounts_page_lists_the_link(client, sso) -> None:
    sign_in(client, sso, "entra", entra_claims())
    html = client.get(reverse("socialaccount_connections")).content.decode()
    assert "Linked accounts" in html
    assert "Microsoft" in html
    assert "Link your Google account" in html


# --- The staff two-factor rule -------------------------------------------------


@pytest.fixture
def tutor(center) -> User:
    user = UserFactory(email="tutor@example.edu")
    MembershipFactory(user=user, center=center, role=Role.TUTOR)
    return user


@pytest.fixture
def trust_idp_mfa(settings, sso):
    settings.SYMPRAXI_TRUST_IDP_MFA = True
    settings.SOCIALACCOUNT_PROVIDERS = sso_idp.providers(trust_idp_mfa=True)
    return sso


MFA_TOKENS = [
    ("entra", entra_claims(email="tutor@example.edu", amr=["pwd", "mfa"])),
    ("google", google_claims(email="tutor@example.edu", amr=["pwd", "mfa"])),
]


@pytest.mark.parametrize(("provider_id", "claims"), MFA_TOKENS, ids=["entra", "google"])
def test_by_default_sso_staff_still_enter_their_sympraxi_code(
    client, sso, tutor, provider_id, claims
) -> None:
    give_mfa(tutor)
    response = sign_in(client, sso, provider_id, claims)
    assert response["Location"] == reverse("mfa_authenticate")
    assert signed_in_user(client) is None


@pytest.mark.parametrize(("provider_id", "claims"), MFA_TOKENS, ids=["entra", "google"])
def test_by_default_sso_staff_without_2fa_must_set_it_up(
    client, sso, tutor, provider_id, claims
) -> None:
    sign_in(client, sso, provider_id, claims)
    assert signed_in_user(client) == tutor
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa_index"))


@pytest.mark.parametrize(("provider_id", "claims"), MFA_TOKENS, ids=["entra", "google"])
def test_trusted_provider_mfa_replaces_the_sympraxi_code(
    client, trust_idp_mfa, tutor, provider_id, claims
) -> None:
    give_mfa(tutor)
    response = sign_in(client, trust_idp_mfa, provider_id, claims)
    assert response["Location"] == "/"
    assert signed_in_user(client) == tutor
    assert client.get(reverse("core:home")).status_code == 200


@pytest.mark.parametrize(("provider_id", "claims"), MFA_TOKENS, ids=["entra", "google"])
def test_trusted_provider_mfa_satisfies_the_setup_rule(
    client, trust_idp_mfa, tutor, provider_id, claims
) -> None:
    sign_in(client, trust_idp_mfa, provider_id, claims)
    assert client.get(reverse("core:home")).status_code == 200


@pytest.mark.parametrize("amr", [None, ["pwd"], ["pwd", "swk"], "mfa"])
@pytest.mark.parametrize("provider_id", ["entra", "google"])
def test_a_token_that_does_not_prove_mfa_is_not_trusted(
    client, trust_idp_mfa, tutor, provider_id, amr
) -> None:
    make_claims = entra_claims if provider_id == "entra" else google_claims
    give_mfa(tutor)
    response = sign_in(
        client, trust_idp_mfa, provider_id, make_claims(email="tutor@example.edu", amr=amr)
    )
    assert response["Location"] == reverse("mfa_authenticate")
    assert signed_in_user(client) is None


def test_trust_ends_when_the_setting_is_turned_off(client, trust_idp_mfa, tutor, settings) -> None:
    sign_in(client, trust_idp_mfa, "entra", entra_claims(email="tutor@example.edu", amr=["mfa"]))
    assert client.get(reverse("core:home")).status_code == 200
    settings.SYMPRAXI_TRUST_IDP_MFA = False
    assert client.get(reverse("core:home")).status_code == 302


def test_a_password_sign_in_never_counts_as_provider_mfa(client, trust_idp_mfa, tutor) -> None:
    client.post(reverse("account_login"), {"login": "tutor@example.edu", "password": PASSWORD})
    assert signed_in_user(client) == tutor
    assert client.get(reverse("core:home")).status_code == 302


def test_google_is_asked_for_amr_only_when_trusted(client, trust_idp_mfa) -> None:
    query = redirect_query(sso_idp.start(client, "google"))
    assert json.loads(query["claims"][0]) == {"id_token": {"amr": {"essential": True}}}


# --- Hiding local sign-in -------------------------------------------------------


@pytest.fixture
def sso_only(settings, sso):
    settings.SYMPRAXI_SSO_ONLY = True
    return sso


def test_sso_only_hides_password_sign_in_and_sign_up(client, sso_only) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "Sign in with Microsoft" in html
    assert 'name="password"' not in html
    assert "passkey_login" not in html
    assert "Create an account" not in html


@pytest.mark.parametrize("url_name", ["account_login", "accounts:local_login"])
def test_sso_only_refuses_passwords_from_everyone_but_site_admins(
    client, sso_only, url_name
) -> None:
    UserFactory(email="sam@example.edu")
    response = client.post(reverse(url_name), {"login": "sam@example.edu", "password": PASSWORD})
    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert signed_in_user(client) is None
    [failed] = audit(AuditAction.LOGIN_FAILED)
    assert failed.details == {"reason": "local_login_off"}


@pytest.mark.parametrize("url_name", ["account_login", "accounts:local_login"])
def test_site_admins_keep_a_break_glass_password_sign_in(client, sso_only, url_name) -> None:
    admin = UserFactory(email="admin@example.edu", is_superuser=True, is_staff=True)
    give_mfa(admin)
    response = client.post(reverse(url_name), {"login": "admin@example.edu", "password": PASSWORD})
    # Two-factor login still applies.
    assert response["Location"] == reverse("mfa_authenticate")


def test_break_glass_page_shows_the_password_form(client, sso_only) -> None:
    html = client.get(reverse("accounts:local_login")).content.decode()
    assert "Administrator sign-in" in html
    assert 'name="password"' in html
    assert f'action="{reverse("accounts:local_login")}"' in html


def test_sso_only_closes_local_sign_up(client, sso_only) -> None:
    client.post(
        reverse("account_signup"),
        {
            "email": "new@example.edu",
            "full_name": "New Person",
            "password1": "a-long-and-unusual-passphrase",
            "password2": "a-long-and-unusual-passphrase",
        },
    )
    assert not User.objects.exists()


def test_sso_only_still_lets_new_people_in_through_sso(client, sso_only) -> None:
    sign_in(client, sso_only, "entra", entra_claims())
    assert signed_in_user(client) is not None


def test_sso_only_does_nothing_without_a_provider(client, settings) -> None:
    settings.SYMPRAXI_SSO_ONLY = True
    assert not sso.sso_only()
    assert 'name="password"' in client.get(reverse("account_login")).content.decode()
