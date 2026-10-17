"""Sign-in rate limits: each visitor's own address, the limits themselves, the 429 page."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from allauth.account import app_settings as account_settings
from allauth.account.adapter import get_adapter
from allauth.core.internal.ratelimit import parse_rates
from django.core.cache import cache
from django.urls import reverse

from sympraxi.privacy.audit import client_ip
from tests.factories import UserFactory

REPO = Path(__file__).resolve().parent.parent
CADDY = "172.18.0.5"  # the address every request arrives from behind Caddy

# allauth's actions that answer with the 429 page when their limit is reached.
ANSWERED_WITH_429 = [
    "login",
    "signup",
    "reset_password",
    "reset_password_from_key",
    "change_password",
    "manage_email",
    "reauthenticate",
    "change_phone",
]


@pytest.fixture(autouse=True)
def fresh_limits():
    """The test cache keeps allauth's counts between tests unless it is emptied."""
    cache.clear()
    yield
    cache.clear()


def limit(settings, **rates: str) -> None:
    settings.ACCOUNT_RATE_LIMITS = {**settings.ACCOUNT_RATE_LIMITS, **rates}


def behind_caddy(settings) -> None:
    """What settings.py sets in production, where compose.yaml trusts X-Forwarded-For."""
    settings.SYMPRAXI_TRUST_X_FORWARDED_FOR = True
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 1


def sign_in(client, forwarded_for: str | None = None, *, query: str = ""):
    headers = {"REMOTE_ADDR": CADDY}
    if forwarded_for is not None:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return client.post(
        reverse("account_login") + query,
        {"login": "someone@example.edu", "password": "not-the-password"},
        **headers,
    )


def settings_in_a_clean_process(tmp_path, names: list[str], **env: str) -> dict:
    code = (
        "import json, sympraxi.settings as s; "
        f"print(json.dumps({{k: getattr(s, k) for k in {names!r}}}))"
    )
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "SYMPRAXI_SECRET_KEY": "x",
        "DATABASE_URL": "postgres://u@localhost/db",
        "SYMPRAXI_ENV_FILE": str(tmp_path / "missing.env"),
        **env,
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=clean_env,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("trust", "count"), [({}, 0), ({"SYMPRAXI_TRUST_X_FORWARDED_FOR": "true"}, 1)]
)
def test_allauth_trusts_one_proxy_when_the_forwarded_address_is_trusted(
    tmp_path, trust, count
) -> None:
    loaded = settings_in_a_clean_process(tmp_path, ["ALLAUTH_TRUSTED_PROXY_COUNT"], **trust)
    assert loaded == {"ALLAUTH_TRUSTED_PROXY_COUNT": count}


def test_per_address_limits_allow_for_a_campus_network() -> None:
    limits = account_settings.RATE_LIMITS
    assert limits["login"] == "300/m/ip"
    assert limits["signup"] == "60/m/ip"
    # 60 failed sign-ins a minute from one address; 5 in 5 minutes for one account.
    assert limits["login_failed"] == "60/m/ip,5/300s/key"


def test_every_limit_behind_the_429_page_counts_over_a_minute() -> None:
    """The page and its Retry-After header tell people to wait a minute."""
    actions = list(ANSWERED_WITH_429)
    if account_settings.EMAIL_VERIFICATION_BY_CODE_ENABLED:
        actions.append("confirm_email")
    for action in actions:
        for rate in parse_rates(account_settings.RATE_LIMITS.get(action)):
            assert rate.duration <= 60, f"{action}: {rate}"


@pytest.mark.django_db
def test_each_forwarded_address_gets_its_own_bucket(client, settings) -> None:
    behind_caddy(settings)
    limit(settings, login="2/m/ip")
    assert sign_in(client, "203.0.113.1").status_code == 200
    assert sign_in(client, "203.0.113.1").status_code == 200
    assert sign_in(client, "203.0.113.1").status_code == 429
    assert sign_in(client, "203.0.113.2").status_code == 200


@pytest.mark.django_db
def test_only_the_address_caddy_wrote_counts(client, settings) -> None:
    """Earlier X-Forwarded-For entries come from the client, so they can't pick a bucket."""
    behind_caddy(settings)
    limit(settings, login="2/m/ip")
    assert sign_in(client, "198.51.100.1, 203.0.113.1").status_code == 200
    assert sign_in(client, "198.51.100.2, 203.0.113.1").status_code == 200
    assert sign_in(client, "198.51.100.3, 203.0.113.1").status_code == 429


@pytest.mark.django_db
def test_forwarded_address_is_ignored_unless_trusted(client, settings) -> None:
    settings.SYMPRAXI_TRUST_X_FORWARDED_FOR = False
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 0
    limit(settings, login="2/m/ip")
    assert sign_in(client, "203.0.113.1").status_code == 200
    assert sign_in(client, "203.0.113.2").status_code == 200
    assert sign_in(client, "203.0.113.3").status_code == 429


@pytest.mark.parametrize(("trusted", "address"), [(True, "203.0.113.1"), (False, CADDY)])
def test_rate_limits_and_the_audit_log_see_the_same_address(rf, settings, trusted, address) -> None:
    settings.SYMPRAXI_TRUST_X_FORWARDED_FOR = trusted
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 1 if trusted else 0
    request = rf.post("/", HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.1", REMOTE_ADDR=CADDY)
    assert get_adapter().get_client_ip(request) == address
    assert client_ip(request) == address


@pytest.mark.django_db
def test_the_429_page_says_what_happened_and_when_to_try_again(client, settings) -> None:
    limit(settings, login="1/m/ip")
    sign_in(client)
    response = sign_in(client, query="?next=/centers/")
    assert response.status_code == 429
    assert response["Retry-After"] == "60"
    html = response.content.decode()
    assert "Too many tries in the last minute" in html
    assert "from one internet address" in html
    assert "Wait a minute, then try again." in html
    assert 'href="/accounts/login/?next=/centers/"' in html
    # It is a full page under the usual Content Security Policy, with no inline code.
    csp = response["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts
    assert all("src=" in tag for tag in scripts)
    assert 'class="usa-button"' in html


@pytest.mark.django_db
def test_the_429_page_for_someone_signed_in_talks_about_their_account(client, settings) -> None:
    limit(settings, change_password="1/m/user")
    client.force_login(UserFactory())
    url = reverse("account_change_password")
    client.post(url, {"oldpassword": "wrong", "password1": "x", "password2": "x"})
    response = client.post(url, {"oldpassword": "wrong", "password1": "x", "password2": "x"})
    assert response.status_code == 429
    html = response.content.decode()
    assert "To protect your account" in html
    assert "internet address" not in html
