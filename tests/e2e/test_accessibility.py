"""Every page passes axe, and no page trips the Content Security Policy.

Run with: uv run pytest -m e2e  (after `npm --prefix frontend run build` and
`uv run playwright install chromium`). Add each new page to PAGES.
"""

import pytest
from axe_playwright_python.sync_playwright import Axe

from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# (who is signed in, path). "{slug}" is filled with a test center's slug.
PAGES = [
    (None, "/"),
    (None, "/accounts/login/"),
    (None, "/accounts/signup/"),
    (None, "/accounts/password/reset/"),
    ("student", "/"),
    ("student", "/centers/{slug}/"),
    ("student", "/accounts/email/"),
    ("student", "/accounts/password/change/"),
    ("student", "/accounts/2fa/"),
    ("director", "/centers/{slug}/"),
    ("director", "/centers/{slug}/members/"),
    ("director", "/centers/{slug}/audit/"),
    ("director-without-2fa", "/accounts/2fa/"),
]

# WCAG 2.0, 2.1 and 2.2 at levels A and AA, plus axe's general best practices.
AXE_OPTIONS = {
    "resultTypes": ["violations"],
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"],
    },
}


def open_page(browser, live_server, sign_in, who, path, **context_args):
    center = CenterFactory()
    context = browser.new_context(**context_args)
    if who is not None:
        user = UserFactory()
        role = "student" if who == "student" else "director"
        MembershipFactory(user=user, center=center, role=role)
        MembershipFactory(user=UserFactory(), center=center)  # someone to list
        if who == "director":
            give_mfa(user)
        sign_in(context, user)
    page = context.new_page()
    return context, page, live_server.url + path.format(slug=center.slug)


@pytest.mark.parametrize(("who", "path"), PAGES, ids=[f"{w or 'anonymous'}:{p}" for w, p in PAGES])
def test_page_passes_axe(browser, live_server, sign_in, who, path) -> None:
    # axe is injected as a script, so this context must ignore the page's CSP.
    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    response = page.goto(url)
    assert response is not None and response.status == 200, f"{url} returned {response}"
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


@pytest.mark.parametrize(("who", "path"), PAGES, ids=[f"{w or 'anonymous'}:{p}" for w, p in PAGES])
def test_page_has_no_csp_violations(browser, live_server, sign_in, who, path) -> None:
    context, page, url = open_page(browser, live_server, sign_in, who, path)
    problems: list[str] = []
    page.on(
        "console",
        lambda msg: (
            problems.append(msg.text)
            if msg.type == "error"
            and ("Content Security Policy" in msg.text or "Refused" in msg.text)
            else None
        ),
    )
    page.on("pageerror", lambda err: problems.append(str(err)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    context.close()
    assert problems == []


def test_member_search_updates_results_without_reloading(browser, live_server, sign_in) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, "director", "/centers/{slug}/members/"
    )
    page.goto(url)
    page.fill("#member-search", "zzz-nobody")
    page.wait_for_selector("text=Nobody matches")
    assert page.url.endswith("?q=zzz-nobody")
    context.close()
