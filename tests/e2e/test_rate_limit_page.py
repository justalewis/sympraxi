"""The page shown when a sign-in rate limit is reached passes axe and keeps to the CSP.

It is reached by posting the sign-in form, so it can't be in PAGES in
test_accessibility.py, which opens each page with a GET.
"""

import pytest
from axe_playwright_python.sync_playwright import Axe
from django.core.cache import cache

from tests.e2e.test_accessibility import AXE_OPTIONS, watch_for_csp_problems

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def one_sign_in_a_minute(settings):
    settings.ACCOUNT_RATE_LIMITS = {**settings.ACCOUNT_RATE_LIMITS, "login": "1/m/ip"}
    cache.clear()
    yield
    cache.clear()


def reach_the_limit(page, live_server) -> None:
    """Sign in twice with a wrong password; the second try gets the 429 page."""
    for status in (200, 429):
        page.goto(live_server.url + "/accounts/login/")
        page.fill("input[name=login]", "someone@example.edu")
        page.fill("input[name=password]", "not-the-password")
        with page.expect_response(lambda r: r.request.method == "POST") as answer:
            page.click("button:has-text('Sign in')")
        assert answer.value.status == status
        page.wait_for_load_state("networkidle")
    assert page.get_by_role("heading", name="Too many tries in the last minute").is_visible()


def test_the_429_page_passes_axe(browser, live_server, one_sign_in_a_minute) -> None:
    # axe is injected as a script, so this context must ignore the page's CSP.
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    reach_the_limit(page, live_server)
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_the_429_page_has_no_csp_violations(browser, live_server, one_sign_in_a_minute) -> None:
    context = browser.new_context()
    page = context.new_page()
    problems = watch_for_csp_problems(page)
    reach_the_limit(page, live_server)
    context.close()
    assert problems == []
