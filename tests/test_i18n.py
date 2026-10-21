"""Spanish translation and the language switch."""

import importlib.util
import re
from pathlib import Path

import pytest
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
PO = ROOT / "locale" / "es" / "LC_MESSAGES" / "django.po"


def _compiler():
    spec = importlib.util.spec_from_file_location(
        "compile_messages", ROOT / "scripts" / "compile_messages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def messages():
    return _compiler().parse(PO.read_text(encoding="utf-8"))


def test_the_compiled_file_matches_the_source() -> None:
    assert _compiler().main(["--check"]) == 0, "run: python scripts/compile_messages.py"


def test_every_translated_message_is_still_in_a_template(messages) -> None:
    used = set()
    pattern = re.compile(r'\{%\s*(?:translate|trans)\s+"((?:[^"\\]|\\.)*)"')
    for template in (ROOT / "sympraxi").rglob("*.html"):
        used.update(m.replace('\\"', '"') for m in pattern.findall(template.read_text("utf-8")))
    stale = [m for m in messages if m and m not in used]
    assert not stale, f"translated but no longer in any template: {stale}"


def test_translations_keep_their_placeholders(messages) -> None:
    placeholder = re.compile(r"%\([a-z_]+\)s|%s|%d|\{[a-z_]+\}")
    for source, translation in messages.items():
        assert sorted(placeholder.findall(source)) == sorted(placeholder.findall(translation))


def test_the_header_names_the_language(messages) -> None:
    assert "Language: es" in messages[""] and "charset=UTF-8" in messages[""]


@pytest.mark.django_db
def test_english_is_the_default(client) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert '<html lang="en">' in html and "Sign in" in html


@pytest.mark.django_db
def test_the_browsers_language_picks_spanish(client) -> None:
    html = client.get(reverse("account_login"), headers={"accept-language": "es"}).content.decode()
    assert '<html lang="es">' in html
    assert "Iniciar sesión" in html and "Get the source code" not in html


@pytest.mark.django_db
def test_the_switch_remembers_the_choice(client) -> None:
    login = reverse("account_login")
    response = client.get(reverse("core:language", args=["es"]), headers={"referer": login})
    assert response.status_code == 302 and response["Location"] == login
    assert "Obtener el código fuente" in client.get(login).content.decode()
    client.get(reverse("core:language", args=["en"]))
    assert "Get the source code" in client.get(login).content.decode()


@pytest.mark.django_db
def test_the_switch_does_not_leave_the_site(client) -> None:
    response = client.get(
        reverse("core:language", args=["es"]), headers={"referer": "https://evil.example/x"}
    )
    assert response["Location"] == "/"


@pytest.mark.django_db
def test_an_unsupported_language_is_ignored(client) -> None:
    client.get(reverse("core:language", args=["xx"]))
    assert "Get the source code" in client.get(reverse("account_login")).content.decode()


@pytest.mark.django_db
def test_pages_carry_no_language_form_or_token(client) -> None:
    html = client.get("/no-such-page-xyz/").content.decode()
    assert "csrfmiddlewaretoken" not in html and "no-such-page-xyz" not in html


@pytest.mark.django_db
def test_the_footer_offers_both_languages(client) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "/language/en/" in html and "/language/es/" in html and "Español" in html


@pytest.mark.django_db
def test_the_error_pages_are_translated(client) -> None:
    html = client.get("/nothing-here/", headers={"accept-language": "es"}).content.decode()
    assert "Página no encontrada" in html
