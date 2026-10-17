"""Booking in a real browser: what sympraxi.js does on the booking form.

Run with the other browser tests: uv run pytest -m e2e
"""

import time

import pytest

from sympraxi.scheduling import services
from tests.e2e.test_accessibility import open_page

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_a_double_click_sends_one_booking(browser, live_server, sign_in, monkeypatch) -> None:
    """sympraxi.js ignores a second click while the booking is on its way."""
    # Slow the booking down so the second click comes before the answer, as it
    # can for a person. (Playwright's dblclick() is so quick that Chromium sends
    # only one request even without the script.)
    book = services.book

    def slowly(**kwargs):
        time.sleep(0.5)
        return book(**kwargs)

    monkeypatch.setattr(services, "book", slowly)
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "student",
        "/centers/{slug}/schedules/{schedule}/?date={tomorrow}",
    )
    page.goto(url)
    page.locator(".sympraxi-time__action a").first.click()
    page.wait_for_selector("text=Book this appointment?")
    page.get_by_label("What course is this for?").fill("ENG 101")
    page.get_by_text("Essay", exact=True).click()
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    # The second click comes 150 ms after the first, from the page itself:
    # Playwright would wait for the first click's page to load.
    page.locator("button:has-text('Book it')").evaluate(
        "button => { button.click(); setTimeout(() => button.click(), 150); }"
    )
    page.wait_for_selector("text=You're booked")
    assert len(posts) == 1
    context.close()
