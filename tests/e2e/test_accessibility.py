"""Every page passes axe, and no page trips the Content Security Policy.

Run with: uv run pytest -m e2e  (after `npm --prefix frontend run build` and
`uv run playwright install chromium`). Add each new page to PAGES.
"""

from datetime import date

import pytest
from axe_playwright_python.sync_playwright import Axe

from sympraxi.centers.models import Role
from sympraxi.scheduling.timeutils import local_date
from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa
from tests.scheduling_world import build_world

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# (who is signed in, path). Placeholders are filled from a test center and its
# scheduling world: {slug}, {schedule}, {appointment}, {assignment}, {exception},
# {term}, {closure}, {room}, {exemption}, {slot} (a free time) and {tomorrow}.
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
    ("student", "/centers/{slug}/schedules/"),
    ("student", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("student", "/centers/{slug}/schedules/{schedule}/book/?{slot}"),
    ("student", "/centers/{slug}/appointments/"),
    ("student", "/centers/{slug}/appointments/{appointment}/"),
    ("student", "/centers/{slug}/appointments/{appointment}/cancel/"),
    ("student", "/centers/{slug}/appointments/{appointment}/change/?date={tomorrow}"),
    ("student", "/centers/{slug}/appointments/{appointment}/change/?{slot}"),
    ("tutor", "/centers/{slug}/tutoring/"),
    ("tutor", "/centers/{slug}/appointments/{appointment}/"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/book-for/?{slot}"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/day/?date={tomorrow}"),
    ("front_desk", "/centers/{slug}/appointments/{appointment}/"),
    ("front_desk", "/centers/{slug}/appointments/{appointment}/confirm/"),
    ("director", "/centers/{slug}/"),
    ("director", "/centers/{slug}/members/"),
    ("director", "/centers/{slug}/audit/"),
    ("director", "/centers/{slug}/schedules/"),
    ("director", "/centers/{slug}/schedules/new/"),
    ("director", "/centers/{slug}/schedules/{schedule}/manage/"),
    ("director", "/centers/{slug}/schedules/{schedule}/edit/"),
    ("director", "/centers/{slug}/schedules/{schedule}/tutors/new/"),
    ("director", "/centers/{slug}/tutors/{assignment}/edit/"),
    ("director", "/centers/{slug}/tutors/{assignment}/hours/"),
    ("director", "/centers/{slug}/tutors/{assignment}/changes/new/"),
    ("director", "/centers/{slug}/hours-changes/{exception}/delete/"),
    ("director", "/centers/{slug}/terms/"),
    ("director", "/centers/{slug}/terms/new/"),
    ("director", "/centers/{slug}/terms/{term}/edit/"),
    ("director", "/centers/{slug}/terms/{term}/closures/new/"),
    ("director", "/centers/{slug}/closures/{closure}/delete/"),
    ("director", "/centers/{slug}/rooms/"),
    ("director", "/centers/{slug}/rooms/new/"),
    ("director", "/centers/{slug}/rooms/{room}/edit/"),
    ("director", "/centers/{slug}/exemptions/"),
    ("director", "/centers/{slug}/exemptions/new/"),
    ("director", "/centers/{slug}/exemptions/{exemption}/end/"),
    ("director-without-2fa", "/accounts/2fa/"),
]

ROLES = {"student": Role.STUDENT, "tutor": Role.TUTOR, "front_desk": Role.FRONT_DESK}

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
    user = None
    if who is not None:
        user = UserFactory()
        MembershipFactory(user=user, center=center, role=ROLES.get(who, Role.DIRECTOR))
        MembershipFactory(user=UserFactory(), center=center)  # someone to list
        if who in ("director", "tutor", "front_desk"):
            give_mfa(user)
        sign_in(context, user)
    world = build_world(
        center,
        student=user if who == "student" else None,
        tutor=user if who == "tutor" else None,
    )
    tomorrow = local_date(world.appointment.starts_at, world.schedule.tz)
    url = live_server.url + path.format(
        slug=center.slug,
        schedule=world.schedule.pk,
        appointment=world.appointment.pk,
        assignment=world.assignment.pk,
        exception=world.exception.pk,
        term=world.term.pk,
        closure=world.closure.pk,
        room=world.room.pk,
        exemption=world.exemption.pk,
        slot=world.slot_query(),
        tomorrow=tomorrow.isoformat(),
    )
    page = context.new_page()
    return context, page, url


IDS = [f"{w or 'anonymous'}:{p}" for w, p in PAGES]


@pytest.mark.parametrize(("who", "path"), PAGES, ids=IDS)
def test_page_passes_axe(browser, live_server, sign_in, who, path) -> None:
    # axe is injected as a script, so this context must ignore the page's CSP.
    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    response = page.goto(url)
    assert response is not None and response.status == 200, f"{url} returned {response}"
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


@pytest.mark.parametrize(("who", "path"), PAGES, ids=IDS)
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


def test_open_times_update_without_reloading(browser, live_server, sign_in) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, "student", "/centers/{slug}/schedules/{schedule}/"
    )
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    later = page.locator("#id_date").get_attribute("max")
    assert later
    day = date.fromisoformat(later)
    heading = f"{day:%A}, {day:%B} {day.day}"
    page.fill("#id_date", later)
    page.dispatch_event("#id_date", "change")
    page.wait_for_selector(f"#open-times h2:has-text('{heading}')")
    page.wait_for_function(f"location.search.includes('date={later}')")
    # The live region outside the list announces the new day's count.
    assert heading in page.locator("#open-times-status").inner_text()
    assert page.evaluate("window.notReloaded") is True
    context.close()


def test_a_student_books_a_time(browser, live_server, sign_in) -> None:
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
    page.click("button:has-text('Book it')")
    page.wait_for_selector("text=You're booked")
    assert "/appointments/" in page.url
    context.close()
