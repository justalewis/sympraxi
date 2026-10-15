import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.core import mail
from django.core.mail import send_mail
from django.test import override_settings
from django.urls import reverse

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "sympraxi" / "templates"


@pytest.mark.django_db
def test_security_headers(client) -> None:
    response = client.get(reverse("core:home"))
    csp = response["Content-Security-Policy"]
    for directive in (
        "default-src 'self'",
        "style-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'none'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"


def test_production_defaults_are_strict(tmp_path) -> None:
    """Load settings in a clean process with only the two required variables set."""
    code = (
        "import json, sympraxi.settings as s; print(json.dumps({k: getattr(s, k) for k in ["
        "'DEBUG', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_SSL_REDIRECT', "
        "'SECURE_HSTS_SECONDS', 'SYMPRAXI_REQUIRE_STAFF_MFA', 'ACCOUNT_EMAIL_VERIFICATION', "
        "'ALLOWED_HOSTS']}))"
    )
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "SYMPRAXI_SECRET_KEY": "x",
        "DATABASE_URL": "postgres://u@localhost/db",
        "SYMPRAXI_ENV_FILE": str(tmp_path / "missing.env"),
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=clean_env,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    production = json.loads(result.stdout)
    assert production == {
        "DEBUG": False,
        "SESSION_COOKIE_SECURE": True,
        "CSRF_COOKIE_SECURE": True,
        "SECURE_SSL_REDIRECT": True,
        "SECURE_HSTS_SECONDS": 31_536_000,
        "SYMPRAXI_REQUIRE_STAFF_MFA": True,
        "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
        "ALLOWED_HOSTS": [],
    }


@pytest.mark.parametrize("path", sorted(TEMPLATES.rglob("*.html")), ids=lambda p: p.name)
def test_templates_have_no_inline_styles_or_scripts(path: Path) -> None:
    """The Content Security Policy blocks inline code, so it must never be written."""
    html = path.read_text(encoding="utf-8")
    assert not re.search(r"\sstyle=", html), "Use a CSS class instead of a style attribute"
    assert not re.search(r"\son[a-z]+=", html), "Use a script file instead of an event attribute"
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, f"Inline script: {tag}"


@pytest.mark.django_db
def test_health_check(client) -> None:
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_source_link_points_at_the_configured_repository(client, settings) -> None:
    settings.SYMPRAXI_SOURCE_CODE_URL = "https://git.example.edu/sympraxi"
    response = client.get(reverse("core:source"))
    assert response.status_code == 302
    assert response["Location"] == "https://git.example.edu/sympraxi"


@override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    }
)
def test_email_goes_through_the_task_queue() -> None:
    send_mail("Your appointment", "See you Tuesday.", "wc@example.edu", ["student@example.edu"])
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "Your appointment"
    assert mail.outbox[0].to == ["student@example.edu"]
