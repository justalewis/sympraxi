"""Every page passes axe, and no page trips the Content Security Policy.

Run with: uv run pytest -m e2e  (after `npm --prefix frontend run build` and
`uv run playwright install chromium`). Add each new page to PAGES, or to
SSO_PAGES if it only looks different once single sign-on is set up. allauth's
own pages are in ACCOUNT_PAGES, forms sent back with errors in FORMS_WITH_ERRORS,
and error pages in ERROR_PAGES. Some pages are also checked on a phone with the
menu open (PHONE_PAGES, STAFF_PHONE_PAGES), at 320 CSS pixels wide (REFLOW_PAGES),
for a focus ring on each control (FOCUS_PAGES), after htmx swaps content in
(HTMX_SWAPS), and with forced colors and reduced motion emulated (EMULATED_PAGES).
"""

from datetime import date, timedelta

import pytest
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.account.utils import user_pk_to_url_str
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import (
    TOTP,
    format_hotp_value,
    hotp_value,
    yield_hotp_counters_from_time,
)
from allauth.mfa.webauthn.internal.auth import WebAuthn
from allauth.socialaccount.models import SocialAccount
from axe_playwright_python.sync_playwright import Axe
from django.contrib.auth.tokens import default_token_generator
from django.utils import timezone
from django.utils.csp import CSP

from sympraxi.accounts import sso_config
from sympraxi.centers.models import Membership, Role
from sympraxi.forms.models import Answer, Question, Response
from sympraxi.records.models import RecordStatus, SessionRecord
from sympraxi.reports.models import CenterExport
from sympraxi.scheduling.models import Attendance, Block, Participant
from sympraxi.scheduling.timeutils import local_date
from tests import sso_idp
from tests.attendance_world import add_attendance
from tests.content_world import make_content_item
from tests.disclosure_world import add_disclosures
from tests.factories import (
    TOTP_SECRET,
    CenterFactory,
    MembershipFactory,
    UserFactory,
    give_mfa,
)
from tests.feedback_world import add_queue
from tests.files_world import PDF, add_files
from tests.forms_world import add_forms
from tests.news_world import add_news
from tests.records_world import add_records
from tests.reports_world import add_reports
from tests.scheduling_world import build_world
from tests.staff_world import add_time_clock

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# (who is signed in, path). Placeholders are filled from a test center and its
# scheduling world: {slug}, {schedule}, {appointment}, {assignment}, {exception},
# {term}, {closure}, {room}, {exemption}, {slot} (a free time) and {tomorrow};
# and its records (tests/records_world.py): {past_appointment}, {record} (final,
# amended), {draft}, {unwritten} (past, no record), {project}, {resource} and
# {student_membership}; and its attendance (tests/attendance_world.py): {missed}
# (a no-show by {blocked_student}) and {block}; and its forms (tests/forms_world.py):
# {form} (the default intake form, with a draft) and {question} (in that draft),
# {report_form} and {report_question} (the same for the session-report questions),
# {answered_record} (a final record with report answers), {survey_form} (with
# five anonymous responses, so its results show) and {survey_token} (a working
# survey link). "blocked_student" signs in as a student whose online booking is
# paused, and "new_student" as one who hasn't answered the registration
# questions yet.
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
    ("director", "/centers/{slug}/switch-online/"),
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
    # Session records
    ("student", "/centers/{slug}/my-writing/"),
    ("student", "/centers/{slug}/my-writing/{record}/"),
    ("student", "/centers/{slug}/appointments/{past_appointment}/"),
    ("tutor", "/centers/{slug}/records/to-write/"),
    ("tutor", "/centers/{slug}/appointments/{unwritten}/record/"),
    ("tutor", "/centers/{slug}/appointments/{past_appointment}/"),
    ("tutor", "/centers/{slug}/records/{draft}/edit/"),
    ("tutor", "/centers/{slug}/records/{record}/"),
    ("tutor", "/centers/{slug}/records/{record}/amend/"),
    ("tutor", "/centers/{slug}/records/new/"),
    ("tutor", "/centers/{slug}/students/{student_membership}/writing/"),
    ("tutor", "/centers/{slug}/projects/{project}/edit/"),
    ("front_desk", "/centers/{slug}/records/to-write/"),
    ("front_desk", "/centers/{slug}/appointments/{past_appointment}/"),
    ("director", "/centers/{slug}/students/{student_membership}/export/"),
    ("director", "/centers/{slug}/resources/"),
    ("director", "/centers/{slug}/resources/new/"),
    ("director", "/centers/{slug}/resources/{resource}/edit/"),
    ("director", "/centers/{slug}/resources/links/"),
    ("director", "/centers/{slug}/resources/{resource}/check/"),
    ("tutor", "/centers/{slug}/resources/favorites/"),
    ("tutor", "/centers/{slug}/resources/{resource}/favorite/"),
    ("director", "/centers/{slug}/reports/resources/"),
    ("director", "/centers/{slug}/reports/by-course/"),
    ("director", "/centers/{slug}/reports/research/"),
    ("student", "/library/{content_item}/"),
    ("director", "/centers/{slug}/reports/questions/"),
    ("student", "/centers/{slug}/my-writing/goals/"),
    ("student", "/centers/{slug}/my-writing/timeline/"),
    ("tutor", "/centers/{slug}/students/{student_membership}/goals/"),
    ("tutor", "/centers/{slug}/students/{student_membership}/timeline/"),
    ("director", "/centers/{slug}/records/settings/"),
    # Attendance, booking blocks and reminders
    ("tutor", "/centers/{slug}/appointments/{unwritten}/attendance/"),
    ("tutor", "/centers/{slug}/appointments/{past_appointment}/attendance/"),
    ("tutor", "/centers/{slug}/attendance/"),
    ("front_desk", "/centers/{slug}/attendance/"),
    ("front_desk", "/centers/{slug}/appointments/{unwritten}/"),
    ("front_desk", "/centers/{slug}/appointments/{appointment}/cancel/"),
    ("front_desk", "/centers/{slug}/appointments/{missed}/waive/?student={blocked_student}"),
    ("director", "/centers/{slug}/blocks/"),
    ("director", "/centers/{slug}/blocks/settings/"),
    ("director", "/centers/{slug}/blocks/{block}/lift/"),
    ("student", "/accounts/reminders/"),
    ("student", "/accounts/profile/"),
    ("student", "/accounts/calendar/"),
    ("tutor", "/accounts/calendar/"),
    ("tutor", "/accounts/reminders/"),
    ("blocked_student", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("blocked_student", "/centers/{slug}/appointments/"),
    ("blocked_student", "/centers/{slug}/appointments/{missed}/"),
    # Forms
    ("director", "/centers/{slug}/forms/"),
    ("director", "/centers/{slug}/forms/new/"),
    ("director", "/centers/{slug}/forms/{form}/"),
    ("director", "/centers/{slug}/forms/{form}/questions/new/"),
    ("director", "/centers/{slug}/form-questions/{question}/edit/"),
    ("director", "/centers/{slug}/form-questions/{question}/remove/"),
    ("director", "/centers/{slug}/forms/{form}/preview/"),
    ("director", "/centers/{slug}/forms/{form}/publish/"),
    ("director", "/centers/{slug}/appointments/{appointment}/"),
    ("director", "/centers/{slug}/students/{student_membership}/registration/"),
    ("tutor", "/centers/{slug}/students/{student_membership}/registration/"),
    ("student", "/centers/{slug}/registration/"),
    ("student", "/centers/{slug}/appointments/{appointment}/intake/"),
    ("new_student", "/centers/{slug}/schedules/{schedule}/book/?{slot}"),
    # Session-report questions and surveys
    ("director", "/centers/{slug}/forms/{report_form}/"),
    ("director", "/centers/{slug}/form-questions/{report_question}/edit/"),
    ("director", "/centers/{slug}/forms/{survey_form}/"),
    ("director", "/centers/{slug}/surveys/settings/"),
    ("director", "/centers/{slug}/surveys/results/"),
    ("director", "/centers/{slug}/records/{answered_record}/"),
    ("tutor", "/centers/{slug}/records/{answered_record}/"),
    ("tutor", "/centers/{slug}/records/{answered_record}/amend/"),
    ("student", "/centers/{slug}/my-writing/{answered_record}/"),
    (None, "/survey/{survey_token}/"),
    (None, "/survey/thanks/"),
    # Reports
    ("director", "/centers/{slug}/reports/statistics/"),
    ("director", "/centers/{slug}/reports/statistics/?first=2030-01-02&last=2030-01-01"),
    ("director", "/centers/{slug}/reports/hours/"),
    ("director", "/centers/{slug}/reports/export/"),
    ("front_desk", "/centers/{slug}/"),
    ("front_desk", "/centers/{slug}/reports/appointments/?date={tomorrow}"),
    ("front_desk", "/centers/{slug}/reports/appointments/?date={tomorrow}&span=week&canceled=on"),
    ("front_desk", "/centers/{slug}/reports/students/"),
    # People
    ("director", "/centers/{slug}/members/add/"),
    ("director", "/centers/{slug}/members/import/"),
    ("director", "/centers/{slug}/members/turn-off/"),
    ("director", "/centers/{slug}/members/{student_membership}/"),
    # Time clock
    ("tutor", "/centers/{slug}/time-clock/"),
    ("front_desk", "/centers/{slug}/time-clock/"),
    ("director", "/centers/{slug}/time-clock/entries/"),
    ("director", "/centers/{slug}/time-clock/approvals/"),
    ("director", "/centers/{slug}/time-clock/settings/"),
    ("director", "/centers/{slug}/time-clock/entries/new/"),
    ("director", "/centers/{slug}/time-clock/entries/{time_entry}/"),
    # News. Every world has a current announcement, so the student's center and
    # schedules pages above show one.
    ("director", "/centers/{slug}/announcements/"),
    ("director", "/centers/{slug}/announcements/new/"),
    ("director", "/centers/{slug}/announcements/{announcement}/"),
    ("director", "/centers/{slug}/email/"),
    ("director", "/centers/{slug}/email/sent/"),
    ("director", "/centers/{slug}/email/sent/{mass_email}/"),
    # Record of disclosures (each world's student has one entry)
    ("director", "/centers/{slug}/students/{student_membership}/disclosures/"),
    ("student", "/centers/{slug}/my-writing/disclosures/"),
    # Files (each world's appointment has the student's draft)
    ("student", "/centers/{slug}/appointments/{appointment}/files/new/"),
    ("tutor", "/centers/{slug}/appointments/{appointment}/files/new/"),
    ("student", "/centers/{slug}/files/{appointment_file}/remove/"),
    # The written-feedback queue (a waiting submission, and one the tutor has)
    ("student", "/centers/{slug}/feedback/send/"),
    ("student", "/centers/{slug}/feedback/mine/"),
    ("student", "/centers/{slug}/feedback/{submission}/"),
    ("student", "/centers/{slug}/feedback/{submission}/files/new/"),
    ("student", "/centers/{slug}/feedback/{submission}/cancel/"),
    ("student", "/centers/{slug}/feedback/files/{submission_file}/remove/"),
    ("tutor", "/centers/{slug}/feedback/"),
    ("tutor", "/centers/{slug}/feedback/{submission}/claim/"),
    ("tutor", "/centers/{slug}/feedback/{claimed}/"),
    ("tutor", "/centers/{slug}/feedback/{claimed}/files/new/"),
    ("director", "/centers/{slug}/feedback/"),
    ("director", "/centers/{slug}/feedback/{submission}/"),
    ("director", "/centers/{slug}/feedback/{submission}/assign/"),
    ("director", "/centers/{slug}/feedback/settings/"),
    ("director", "/centers/{slug}/feedback/turnaround/"),
    # The comment viewer (a waiting draft for the student and a director, a claimed one
    # for the tutor)
    ("student", "/centers/{slug}/feedback/files/{submission_file}/view/"),
    ("director", "/centers/{slug}/feedback/files/{submission_file}/view/"),
    ("tutor", "/centers/{slug}/feedback/files/{claimed_file}/view/"),
    # Video rooms (the world's appointment is in person, so the room page says so)
    ("student", "/centers/{slug}/appointments/{appointment}/join/"),
    ("tutor", "/centers/{slug}/appointments/{appointment}/join/"),
    ("director", "/centers/{slug}/video/"),
    # The shared document (off in these worlds, so the pages say so; tests/e2e/test_room.py
    # runs the real editor)
    ("student", "/centers/{slug}/appointments/{appointment}/room/"),
    ("tutor", "/centers/{slug}/appointments/{appointment}/room/versions/"),
]

# Pages that change once single sign-on is set up: (who, path, variant). "sso" has
# Entra ID and Google set up; "sso-only" also hides password sign-in.
SSO_PAGES = [
    (None, "/accounts/login/", "sso"),
    (None, "/accounts/signup/", "sso"),
    (None, "/accounts/login/", "sso-only"),
    (None, "/accounts/login/local/", "sso-only"),
    (None, "/accounts/oidc/entra/login/", "sso"),
    (None, "/accounts/3rdparty/login/cancelled/", "sso"),
    ("student", "/accounts/3rdparty/", "sso"),
]
ALL_PAGES = [(who, path, None) for who, path in PAGES] + SSO_PAGES

ROLES = {
    "student": Role.STUDENT,
    "blocked_student": Role.STUDENT,
    "new_student": Role.STUDENT,
    "tutor": Role.TUTOR,
    "front_desk": Role.FRONT_DESK,
}

# WCAG 2.0, 2.1 and 2.2 at levels A and AA, plus axe's general best practices.
AXE_OPTIONS = {
    "resultTypes": ["violations"],
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"],
    },
}


def set_up_sso(settings, variant: str | None) -> None:
    """Configure the providers as settings.py would, including the CSP's form-action."""
    if variant is None:
        return
    providers = sso_idp.providers()
    settings.SOCIALACCOUNT_PROVIDERS = providers
    settings.SYMPRAXI_SSO_ONLY = variant == "sso-only"
    origins = sso_config.login_origins(providers["openid_connect"]["APPS"])
    settings.SECURE_CSP = {**settings.SECURE_CSP, "form-action": [CSP.SELF, *origins]}


def open_page(browser, live_server, sign_in, who, path, variant=None, **context_args):
    center = CenterFactory()
    context = browser.new_context(**context_args)
    user = None
    if who is not None:
        user = UserFactory()
        MembershipFactory(user=user, center=center, role=ROLES.get(who, Role.DIRECTOR))
        MembershipFactory(user=UserFactory(), center=center)  # someone to list
        if who in ("director", "tutor", "front_desk"):
            give_mfa(user)
        if variant is not None:
            SocialAccount.objects.create(
                user=user,
                provider="entra",
                uid="e2e-subject",
                extra_data={"id_token": sso_idp.entra_claims(email=user.email)},
            )
        sign_in(context, user)
    world = add_files(
        add_disclosures(
            add_news(
                add_time_clock(
                    add_reports(
                        add_forms(
                            add_attendance(
                                add_records(
                                    build_world(
                                        center,
                                        student=user if who == "student" else None,
                                        tutor=user if who == "tutor" else None,
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    world = add_queue(world)
    if who == "blocked_student":
        Membership.objects.filter(user=world.blocked_student).delete()
        Participant.objects.filter(student=world.blocked_student).update(student=user)
        Block.objects.filter(student=world.blocked_student).update(student=user)
        world.blocked_student = user
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
        past_appointment=world.past_appointment.pk,
        record=world.record.pk,
        draft=world.draft.pk,
        unwritten=world.unwritten.pk,
        project=world.project.pk,
        resource=world.resource.pk,
        student_membership=world.student_membership.pk,
        missed=world.missed.pk,
        block=world.block.pk,
        blocked_student=world.blocked_student.pk,
        form=world.form.pk,
        question=world.question.pk,
        report_form=world.report_form.pk,
        report_question=world.report_question.pk,
        answered_record=world.answered_record.pk,
        survey_form=world.survey_form.pk,
        survey_token=world.survey_token,
        center_export=world.center_export.pk,
        time_entry=world.time_entry.pk,
        announcement=world.announcement.pk,
        mass_email=world.mass_email.pk,
        appointment_file=world.appointment_file.pk,
        submission=world.submission.pk,
        content_item=make_content_item().pk,
        claimed=world.claimed.pk,
        submission_file=world.submission_file.pk,
        claimed_file=world.claimed_file.pk,
    )
    page = context.new_page()
    return context, page, url


IDS = [f"{w or 'anonymous'}{'+' + v if v else ''}:{p}" for w, p, v in ALL_PAGES]


@pytest.mark.parametrize(("who", "path", "variant"), ALL_PAGES, ids=IDS)
def test_page_passes_axe(browser, live_server, sign_in, settings, who, path, variant) -> None:
    set_up_sso(settings, variant)
    # axe is injected as a script, so this context must ignore the page's CSP.
    context, page, url = open_page(
        browser, live_server, sign_in, who, path, variant, bypass_csp=True
    )
    response = page.goto(url)
    assert response is not None and response.status == 200, f"{url} returned {response}"
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_an_expired_export_link_passes_axe(browser, live_server, sign_in) -> None:
    """The page an expired download link answers with, status 410."""
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "director",
        "/centers/{slug}/reports/export/{center_export}/download/",
        bypass_csp=True,
    )
    CenterExport.objects.update(expires_at=timezone.now() - timedelta(hours=1))
    response = page.goto(url)
    assert response is not None and response.status == 410
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def watch_for_csp_problems(page) -> list[str]:
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
    return problems


@pytest.mark.parametrize(("who", "path", "variant"), ALL_PAGES, ids=IDS)
def test_page_has_no_csp_violations(
    browser, live_server, sign_in, settings, who, path, variant
) -> None:
    set_up_sso(settings, variant)
    context, page, url = open_page(browser, live_server, sign_in, who, path, variant)
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    context.close()
    assert problems == []


@pytest.mark.parametrize(
    ("label", "provider_url"),
    [
        ("Sign in with Microsoft", f"https://login.microsoftonline.com/{sso_idp.TENANT}/"),
        ("Sign in with Google", "https://accounts.google.com/"),
    ],
)
def test_sso_button_works_from_the_keyboard_under_the_csp(
    browser, live_server, settings, idp, label, provider_url
) -> None:
    """Tab to the button, press Enter, and the browser heads for the provider.

    The button is a form whose answer redirects off-site, and browsers apply the
    CSP's form-action to that redirect. If the policy blocked it, the browser would
    never request the provider's page. The test browser can't resolve outside
    hosts (see conftest.py), so the request goes nowhere.
    """
    set_up_sso(settings, "sso")
    context = browser.new_context()
    page = context.new_page()
    problems = watch_for_csp_problems(page)
    page.goto(live_server.url + "/accounts/login/")
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.textContent.trim()") == label:
            break
    else:
        pytest.fail(f"The Tab key never reached the {label!r} button")
    with page.expect_request(lambda request: request.url.startswith(provider_url)) as went:
        page.keyboard.press("Enter")
    context.close()
    assert "response_type=code" in went.value.url
    assert problems == []


# Steps that make htmx swap content into a page. The tests below that drive the
# same parts by keyboard use them, and so does test_swapped_in_content_passes_axe.


def show_the_last_open_day(page) -> str:
    """Change the date to the last one open; htmx swaps in that day's times.

    Returns the day as the date field's value.
    """
    later = page.locator("#id_date").get_attribute("max")
    assert later
    day = date.fromisoformat(later)
    page.fill("#id_date", later)
    page.dispatch_event("#id_date", "change")
    page.wait_for_selector(f"#open-times h2:has-text('{day_heading(day)}')")
    return later


def day_heading(day: date) -> str:
    return f"{day:%A}, {day:%B} {day.day}"


def choose_something_else(page) -> None:
    """Choose "Something else" with the space bar; htmx swaps in its follow-up question."""
    page.get_by_label("What course is this for?").fill("ENG 101")
    page.get_by_label("Something else").focus()
    page.keyboard.press("Space")
    page.wait_for_selector("text=What kind of writing, then?")


def tag_thesis(page) -> None:
    """Open "Ideas and argument" and check "Thesis" by keyboard; htmx swaps in suggestions."""
    page.focus("summary:has-text('Ideas and argument')")
    page.keyboard.press("Enter")
    page.focus("#topic-argument--thesis")
    page.keyboard.press("Space")
    page.wait_for_selector("#resource-results >> text=Our thesis handout")


def attach_the_thesis_handout(page) -> None:
    """Tab to the suggestion's Attach button and press Enter; htmx adds the attached row."""
    tab_to(page, lambda label: label.startswith("Attach Our thesis handout"))
    page.keyboard.press("Enter")
    page.wait_for_selector("#attached-resources >> text=Our thesis handout")
    # htmx applies the swapped-in class after its settle delay (20 ms), so wait for it.
    page.wait_for_selector("#attached-empty", state="hidden")


def search_resources(page) -> None:
    page.fill("#resource-search", "handout")
    page.wait_for_selector("#resource-results >> text=Matching “handout”")


def search_members(page) -> None:
    page.fill("#member-search", "zzz-nobody")
    page.wait_for_selector("text=Nobody matches")


def move_a_question_down(page) -> str:
    """Tab to "Move down" on the first question and press Enter; htmx swaps in the new order.

    Returns the button's ID.
    """
    tab_to(page, lambda label: label.startswith("Move down: What course is this for?"))
    button = page.evaluate("document.activeElement.id")
    page.keyboard.press("Enter")
    page.wait_for_selector("#builder-status >> text=Moved “What course is this for?” down.")
    return button


def wait_for_htmx(page) -> None:
    """Wait until no request is out and the last swap has settled."""
    page.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling, .htmx-added')"
    )


def tab_to(page, matches, *, limit: int = 60) -> None:
    """Press Tab until the focused element's text or value satisfies `matches`."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        label = page.evaluate(
            "(document.activeElement.textContent || '').trim() + ' ' + "
            "(document.activeElement.value || '')"
        )
        if matches(label):
            return
    pytest.fail("The Tab key never reached the control")


def test_member_search_updates_results_without_reloading(browser, live_server, sign_in) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, "director", "/centers/{slug}/members/"
    )
    page.goto(url)
    search_members(page)
    assert page.url.endswith("?q=zzz-nobody")
    context.close()


def test_open_times_update_without_reloading(browser, live_server, sign_in) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, "student", "/centers/{slug}/schedules/{schedule}/"
    )
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    later = show_the_last_open_day(page)
    heading = day_heading(date.fromisoformat(later))
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
    # The center's intake questions are on the page; two are required.
    page.get_by_label("What course is this for?").fill("ENG 101")
    # USWDS draws its own radio buttons and keeps the real input off-screen; click the label.
    page.get_by_text("Essay", exact=True).click()
    page.click("button:has-text('Book it')")
    page.wait_for_selector("text=You're booked")
    assert "/appointments/" in page.url
    page.wait_for_selector("text=Your answers for this appointment")
    context.close()


def test_a_tutor_writes_and_finalizes_a_record_by_keyboard(browser, live_server, sign_in) -> None:
    """Tag a topic, see a suggestion, attach it and finalize, without a mouse or a reload."""
    context, page, url = open_page(
        browser, live_server, sign_in, "tutor", "/centers/{slug}/appointments/{unwritten}/record/"
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.evaluate("window.notReloaded = true")

    page.focus("#id_summary")
    page.keyboard.type("We moved your thesis to the first paragraph.")

    # The center's required report question, answered with the space bar.
    page.get_by_label("Yes", exact=True).focus()
    page.keyboard.press("Space")

    # Check "Thesis" with the space bar. The suggestions follow the topic, and the
    # status line says so.
    tag_thesis(page)
    assert page.is_checked("#topic-argument--thesis")
    assert "suggestion" in page.locator("#resource-status").inner_text()

    attach_the_thesis_handout(page)
    assert "Attached Our thesis handout" in page.locator("#resource-status").inner_text()

    # The new row's note box is the next thing to type in.
    page.focus("#attached-resources textarea")
    page.keyboard.type("Read the second half.")
    assert page.evaluate("window.notReloaded") is True

    page.focus("button:has-text('Save and finalize')")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Finalized.")
    # The page is now the record itself: /centers/<slug>/records/<id>/.
    rec = SessionRecord.objects.get(pk=page.url.rstrip("/").split("/")[-1])
    assert rec.status == RecordStatus.FINAL
    assert [(a.resource.title, a.note) for a in rec.attachments.all()] == [
        ("Our thesis handout", "Read the second half.")
    ]
    context.close()
    assert problems == []


def test_resource_search_updates_without_reloading(browser, live_server, sign_in) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, "tutor", "/centers/{slug}/records/{draft}/edit/"
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    search_resources(page)
    assert "Our thesis handout" in page.locator("#resource-results").inner_text()
    assert "1 resource matches your search." in page.locator("#resource-status").inner_text()
    assert page.evaluate("window.notReloaded") is True
    context.close()
    assert problems == []


def test_front_desk_confirms_a_possible_no_show_by_keyboard(browser, live_server, sign_in) -> None:
    """Tab to "No-show" on the list of possible no-shows, press Enter, and it's recorded."""
    context, page, url = open_page(
        browser, live_server, sign_in, "front_desk", "/centers/{slug}/attendance/"
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    tab_to(page, lambda label: label.startswith("No-show:"))
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Attendance saved.")
    assert "Nothing to check right now." in page.locator("main").inner_text()
    flagged = Participant.objects.get(flagged_at__isnull=False)
    assert flagged.attendance == Attendance.NO_SHOW
    context.close()
    assert problems == []


def test_follow_up_questions_appear_in_place_by_keyboard(browser, live_server, sign_in) -> None:
    """Choosing "Something else" with the space bar shows its follow-up without a reload."""
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "student",
        "/centers/{slug}/schedules/{schedule}/book/?{slot}",
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    assert page.get_by_label("What kind of writing, then?").count() == 0
    choose_something_else(page)
    other = page.get_by_label("Something else")
    assert "1 follow-up question appeared." in page.locator("#form-status").inner_text()
    # Focus stays on the choice that was made, and what was typed is kept.
    assert page.evaluate("document.activeElement.value") == other.get_attribute("value")
    assert page.get_by_label("What course is this for?").input_value() == "ENG 101"
    page.get_by_label("What kind of writing, then?").fill("A zine")
    assert page.evaluate("window.notReloaded") is True
    page.click("button:has-text('Book it')")
    page.wait_for_selector("text=You're booked")
    saved = Answer.objects.get(question__label="What kind of writing, then?")
    assert saved.text == "A zine"
    context.close()
    assert problems == []


def test_a_director_reorders_questions_by_keyboard(browser, live_server, sign_in) -> None:
    """Tab to "Move down", press Enter: the list changes in place and focus stays on the button."""
    context, page, url = open_page(
        browser, live_server, sign_in, "director", "/centers/{slug}/forms/{form}/"
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    focused = move_a_question_down(page)
    assert page.evaluate("document.activeElement.id") == focused
    assert page.evaluate("window.notReloaded") is True
    first = page.locator("#questions li").first.inner_text()
    assert first.startswith("What kind of writing is it?")
    draft = Question.objects.get(pk=focused.removeprefix("down-")).version
    assert [q.label for q in draft.ordered_questions()][:2] == [
        "What kind of writing is it?",
        "What course is this for?",
    ]
    context.close()
    assert problems == []


# allauth's pages outside PAGES: (state, path). Each opens for a student without
# a center, in one of these states:
#   None           not signed in
#   "student"      signed in, without two-factor login
#   "no-password"  signed in, with an account made by single sign-on
#   "fresh"        just signed in with a password, so pages that want a recent
#                  sign-in open
#   "fresh+2fa"    the same, with an authenticator app, recovery codes and a
#                  passkey ({passkey}), signed in with a code from the app
#   "2fa-step"     has an authenticator app and has entered only the password
# {reset_key} is a working password-reset link's key and {confirm_key} a working
# email-confirmation link's key.
ACCOUNT_PAGES = [
    (None, "/accounts/password/reset/done/"),
    (None, "/accounts/password/reset/key/{reset_key}/"),
    (None, "/accounts/password/reset/key/done/"),
    (None, "/accounts/confirm-email/"),
    (None, "/accounts/confirm-email/{confirm_key}/"),
    (None, "/accounts/inactive/"),
    (None, "/accounts/3rdparty/login/error/"),
    ("2fa-step", "/accounts/2fa/authenticate/"),
    ("student", "/accounts/logout/"),
    ("student", "/accounts/reauthenticate/"),
    ("no-password", "/accounts/password/set/"),
    ("fresh", "/accounts/2fa/totp/activate/"),
    ("fresh", "/accounts/2fa/webauthn/add/"),
    ("fresh+2fa", "/accounts/2fa/"),
    ("fresh+2fa", "/accounts/2fa/reauthenticate/"),
    ("fresh+2fa", "/accounts/2fa/totp/deactivate/"),
    ("fresh+2fa", "/accounts/2fa/recovery-codes/"),
    ("fresh+2fa", "/accounts/2fa/recovery-codes/generate/"),
    ("fresh+2fa", "/accounts/2fa/webauthn/"),
    ("fresh+2fa", "/accounts/2fa/webauthn/keys/{passkey}/edit/"),
    ("fresh+2fa", "/accounts/2fa/webauthn/keys/{passkey}/remove/"),
]
ACCOUNT_IDS = [f"{state or 'anonymous'}:{path}" for state, path in ACCOUNT_PAGES]
# allauth answers a failed single sign-on with 401.
ACCOUNT_STATUS = {"/accounts/3rdparty/login/error/": 401}


def current_code(secret: str) -> str:
    counters = list(yield_hotp_counters_from_time())
    return format_hotp_value(hotp_value(secret, counters[len(counters) // 2]))


def open_account_page(browser, sign_in, sign_in_with_password, live_server, state, path, **args):
    user = UserFactory()
    if state == "no-password":
        user.set_unusable_password()
        user.save()
    passkey = None
    if state in ("fresh+2fa", "2fa-step"):
        TOTP.activate(user, TOTP_SECRET)
    if state == "fresh+2fa":
        RecoveryCodes.activate(user)
        passkey = WebAuthn.add(user, "Work laptop", {}).instance
        Authenticator.objects.filter(pk=passkey.pk).update(
            data={**passkey.data, "passwordless": True}
        )
    unconfirmed = EmailAddress.objects.create(user=UserFactory(verified=False), email="new@x.edu")
    context = browser.new_context(**args)
    if state in ("student", "no-password"):
        sign_in(context, user)
    elif state == "fresh":
        sign_in_with_password(context, user)
    elif state == "fresh+2fa":
        sign_in_with_password(context, user, code=current_code(TOTP_SECRET))
    elif state == "2fa-step":
        sign_in_with_password(context, user, code=None)
    url = live_server.url + path.format(
        reset_key=f"{user_pk_to_url_str(user)}-{default_token_generator.make_token(user)}",
        confirm_key=EmailConfirmationHMAC(unconfirmed).key,
        passkey=passkey.pk if passkey else None,
    )
    return context, context.new_page(), url


@pytest.mark.parametrize(("state", "path"), ACCOUNT_PAGES, ids=ACCOUNT_IDS)
def test_an_account_page_passes_axe(
    browser, live_server, sign_in, sign_in_with_password, state, path
) -> None:
    context, page, url = open_account_page(
        browser, sign_in, sign_in_with_password, live_server, state, path, bypass_csp=True
    )
    response = page.goto(url)
    status = ACCOUNT_STATUS.get(path, 200)
    assert response is not None and response.status == status, f"{url} returned {response}"
    # Nothing sent the person elsewhere, such as back to sign in again. (A reset
    # link moves its key into the session and goes on to the form.)
    if "{reset_key}" not in path:
        assert page.url == url
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


@pytest.mark.parametrize(("state", "path"), ACCOUNT_PAGES, ids=ACCOUNT_IDS)
def test_an_account_page_has_no_csp_violations(
    browser, live_server, sign_in, sign_in_with_password, state, path
) -> None:
    context, page, url = open_account_page(
        browser, sign_in, sign_in_with_password, live_server, state, path
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    context.close()
    assert problems == []


# Forms sent back with errors: (who, path, steps, the button that sends it).
# A step is ("fill", selector, text) or ("check", a checkbox's ID). Each form
# passes the browser's own checks (required fields), so the server answers.
FORMS_WITH_ERRORS = [
    (
        None,
        "/accounts/signup/",
        [
            ("fill", "#id_email", "new@example.edu"),
            ("fill", "#id_full_name", "New Person"),
            ("fill", "#id_password1", "short"),
            ("fill", "#id_password2", "short"),
        ],
        "main button[type=submit]",
    ),
    (
        None,
        "/accounts/login/",
        [("fill", "#id_login", "nobody@example.edu"), ("fill", "#id_password", "not-it")],
        "main button[type=submit]:text-is('Sign in')",
    ),
    (
        "student",
        "/centers/{slug}/schedules/{schedule}/book/?{slot}",
        [],
        "button:has-text('Book it')",
    ),
    (
        "director",
        "/centers/{slug}/form-questions/{question}/edit/",
        [("check", "id_required"), ("check", "id_is_demographic")],
        "button:has-text('Save the question')",
    ),
    (
        "student",
        "/accounts/profile/",
        [("fill", "#id_full_name", "  ")],
        "main button:text-is('Save')",
    ),
]


@pytest.mark.parametrize(
    ("who", "path", "steps", "button"),
    FORMS_WITH_ERRORS,
    ids=[f"{w or 'anonymous'}:{p}" for w, p, _, _ in FORMS_WITH_ERRORS],
)
def test_a_form_with_errors_passes_axe(
    browser, live_server, sign_in, who, path, steps, button
) -> None:
    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    page.goto(url)
    for step in steps:
        if step[0] == "fill":
            page.fill(step[1], step[2])
        elif not page.is_checked(f"#{step[1]}"):
            # USWDS keeps the real box off-screen and draws its own; click the label.
            page.click(f"label[for='{step[1]}']")
    page.click(button)
    page.wait_for_selector(".usa-error-message, .usa-alert--error")
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


# Error pages: (who, path, status). tests/error_urls.py adds /fail/, which raises.
ERROR_PAGES = [
    (None, "/no-such-page/", 404),
    ("student", "/centers/{slug}/appointments/00000000-0000-0000-0000-000000000000/", 404),
    ("student", "/centers/{slug}/members/", 403),
    (None, "/fail/", 500),
    ("student", "/fail/", 500),
]


@pytest.mark.urls("tests.error_urls")
@pytest.mark.parametrize(("who", "path", "status"), ERROR_PAGES)
def test_an_error_page_passes_axe_and_the_csp(browser, live_server, sign_in, who, path, status):
    context, page, url = open_page(browser, live_server, sign_in, who, path)
    problems = watch_for_csp_problems(page)
    response = page.goto(url)
    assert response is not None and response.status == status
    page.wait_for_load_state("networkidle")
    context.close()
    assert problems == []

    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    page.goto(url)
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_the_expired_form_page_passes_axe(browser, live_server) -> None:
    """A sign-in form posted without its CSRF cookie gets the "form has expired" page."""
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    page.goto(live_server.url + "/accounts/login/")
    context.clear_cookies()
    page.fill("#id_login", "someone@example.edu")
    page.fill("#id_password", "not-the-password")
    with page.expect_response(lambda r: r.request.method == "POST") as posted:
        page.click("main button[type=submit]:text-is('Sign in')")
    assert posted.value.status == 403
    page.wait_for_selector("text=This form has expired")
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_dead_survey_link_passes_axe(browser, live_server) -> None:
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    response = page.goto(live_server.url + "/survey/" + "a" * 43 + "/")
    assert response is not None and response.status == 404
    assert "doesn't open a survey" in page.locator("main").inner_text()
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


# Pages on a phone: (who, path). axe checks each as it loads and again with the
# USWDS menu open. Key student pages, then the tutor's record form and the front
# desk's pages, which staff use on phones and tablets too.
PHONE_SIZE = {"width": 375, "height": 812}
PHONE = {"viewport": PHONE_SIZE, "is_mobile": True, "has_touch": True}
PHONE_PAGES = [
    (None, "/accounts/login/"),
    ("student", "/"),
    ("student", "/centers/{slug}/"),
    ("student", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("student", "/centers/{slug}/schedules/{schedule}/book/?{slot}"),
    ("student", "/centers/{slug}/appointments/"),
    ("student", "/centers/{slug}/appointments/{appointment}/"),
    ("student", "/centers/{slug}/my-writing/{record}/"),
]
STAFF_PHONE_PAGES = [
    ("tutor", "/centers/{slug}/appointments/{unwritten}/record/"),
    ("tutor", "/centers/{slug}/records/{draft}/edit/"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/book-for/?{slot}"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/day/?date={tomorrow}"),
    ("front_desk", "/centers/{slug}/attendance/"),
]
ALL_PHONE_PAGES = PHONE_PAGES + STAFF_PHONE_PAGES


@pytest.mark.parametrize(
    ("who", "path"),
    ALL_PHONE_PAGES,
    ids=[f"{w or 'anonymous'}:{p}" for w, p in ALL_PHONE_PAGES],
)
def test_a_page_on_a_phone_passes_axe_with_the_menu_open(
    browser, live_server, sign_in, who, path
) -> None:
    context, page, url = open_page(
        browser, live_server, sign_in, who, path, bypass_csp=True, **PHONE
    )
    page.goto(url)
    # Nothing makes the page scroll sideways at this width.
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE_SIZE["width"]
    # The open menu's overlay covers the page, so axe can't check the contrast of
    # anything under it; this first run checks the page itself at this width.
    closed = Axe().run(page, options=AXE_OPTIONS)
    open_the_menu(page)
    opened = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert closed.violations_count == 0, closed.generate_report()
    assert opened.violations_count == 0, opened.generate_report()


def open_the_menu(page) -> None:
    """Tap Menu and wait for the menu to finish sliding in."""
    page.tap(".usa-menu-btn")
    page.wait_for_selector(".usa-nav.is-visible")
    page.wait_for_function("document.getAnimations().length === 0")


def test_the_phone_menu_keeps_focus_inside_until_closed(browser, live_server, sign_in) -> None:
    """With the menu open, Tab and Shift+Tab stay in it, and Escape closes it.

    USWDS hides the rest of the page from screen readers with aria-hidden while
    the menu is open, and its links stay focusable. axe can't tell whether Tab
    still reaches them, so it marks its aria-hidden-focus rule "needs review"
    rather than passing or failing it. USWDS's focus trap is what keeps them out
    of reach; this checks it.
    """
    context, page, url = open_page(
        browser, live_server, sign_in, "student", "/centers/{slug}/", **PHONE
    )
    problems = watch_for_csp_problems(page)
    page.goto(url)
    hidden_before = page.locator("[aria-hidden]").count()
    tab_to(page, lambda label: label.strip() == "Menu")
    page.keyboard.press("Enter")
    page.wait_for_selector(".usa-nav.is-visible")
    in_menu = "!!document.activeElement.closest('.usa-nav')"
    assert page.evaluate("document.activeElement.className") == "usa-nav__close"
    reached = set()
    for key in ["Tab"] * 8 + ["Shift+Tab"] * 8:
        page.keyboard.press(key)
        assert page.evaluate(in_menu), f"{key} left the open menu"
        reached.add(page.evaluate("document.activeElement.textContent.trim()"))
    assert {"My centers", "Account", "Sign out"} <= reached
    page.keyboard.press("Escape")
    page.wait_for_selector(".usa-nav", state="hidden")
    assert page.evaluate("document.activeElement.className") == "usa-menu-btn"
    assert page.locator("[aria-hidden]").count() == hidden_before
    context.close()
    assert problems == []


# WCAG's reflow criterion (1.4.10) asks that a page work 320 CSS pixels wide
# without scrolling sideways: a 1280-pixel window zoomed to 400%. Data tables may
# scroll on their own, but Sympraxi's stack into one column below 480 pixels
# (USWDS's usa-table--stacked), so pages with wide tables must fit too. These are
# the phone pages and the pages whose tables are widest.
REFLOW_SIZE = {"width": 320, "height": 256}
REFLOW_PAGES = [
    *ALL_PHONE_PAGES,
    ("director", "/centers/{slug}/members/"),
    ("director", "/centers/{slug}/forms/"),
    ("director", "/centers/{slug}/forms/{form}/"),
    ("director", "/centers/{slug}/surveys/results/"),
    ("director", "/centers/{slug}/students/{student_membership}/export/"),
    ("director", "/centers/{slug}/members/{student_membership}/"),
    ("director", "/centers/{slug}/time-clock/entries/"),
    ("tutor", "/centers/{slug}/time-clock/"),
    ("director", "/centers/{slug}/announcements/"),
    ("director", "/centers/{slug}/email/sent/"),
    ("director", "/centers/{slug}/students/{student_membership}/disclosures/"),
]
# The outermost elements that reach past the window's right edge.
STICKING_OUT = """() => {
  const edge = document.documentElement.clientWidth + 0.5;
  const out = (el) => el.getBoundingClientRect().right > edge;
  return [...document.querySelectorAll('body *')]
    .filter((el) => out(el) && !out(el.parentElement))
    .map((el) => [el.tagName.toLowerCase(), ...el.classList].join('.'));
}"""


@pytest.mark.parametrize(
    ("who", "path"), REFLOW_PAGES, ids=[f"{w or 'anonymous'}:{p}" for w, p in REFLOW_PAGES]
)
def test_a_page_fits_320_css_pixels_without_scrolling_sideways(
    browser, live_server, sign_in, who, path
) -> None:
    context, page, url = open_page(browser, live_server, sign_in, who, path, viewport=REFLOW_SIZE)
    page.goto(url)
    # USWDS hides the body's sideways overflow, so too wide a page is cut off
    # rather than scrolled. scrollWidth still counts what was cut.
    width = page.evaluate("document.documentElement.scrollWidth")
    sticking_out = page.evaluate(STICKING_OUT)
    context.close()
    assert width <= REFLOW_SIZE["width"], f"{width} CSS pixels wide, because of {sticking_out}"


# Pages whose controls between them include every kind Sympraxi uses: links,
# buttons, text, date and search fields, selects, text areas, radio buttons,
# checkboxes, disclosure summaries, the side navigation, and buttons marked
# aria-disabled.
FOCUS_PAGES = [
    (None, "/accounts/login/"),
    ("student", "/centers/{slug}/"),
    ("student", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("student", "/centers/{slug}/schedules/{schedule}/book/?{slot}"),
    ("tutor", "/centers/{slug}/appointments/{unwritten}/record/"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/day/?date={tomorrow}"),
    ("director", "/centers/{slug}/forms/{form}/"),
    ("director", "/centers/{slug}/members/"),
]
# How a control's focus ring is drawn: its outline, or its box shadow. USWDS keeps
# the real checkbox or radio button off-screen and rings the box it draws in the
# label's ::before instead, so for those the label is what counts.
RING = """(el) => {
  const drawn = el.matches('.usa-checkbox__input, .usa-radio__input') && el.labels[0] || el;
  const style = getComputedStyle(drawn, drawn === el ? null : '::before');
  const box = drawn.getBoundingClientRect();
  const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
  return {
    outline: outline ? `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}` : null,
    shadow: style.boxShadow,
    inView: box.width > 0 && box.height > 0 && box.right > 0 && box.bottom > 0
      && box.left < innerWidth && box.top < innerHeight,
  };
}"""
REMEMBER_UNFOCUSED_RINGS = f"""() => {{
  window.ring = {RING};
  window.unfocused = new Map();
  const controls = 'a[href], button, input, select, textarea, summary, [tabindex]';
  for (const el of document.querySelectorAll(controls)) window.unfocused.set(el, ring(el));
}}"""
FOCUSED = """() => {
  const el = document.activeElement;
  if (el === document.body) return null;
  if (!window.unfocused.has(el)) window.unfocused.set(el, null);
  const name = (el.textContent || '').trim() || el.value || el.name || el.id;
  return {
    index: [...window.unfocused.keys()].indexOf(el),
    name: `${el.tagName.toLowerCase()} "${name.slice(0, 40)}"`,
    unfocused: window.unfocused.get(el),
    focused: ring(el),
  };
}"""


def tab_through(page) -> tuple[int, list[str]]:
    """Tab from wherever focus is until it comes back around or leaves the page.

    Returns how many controls Tab reached and what was wrong with any of their
    focus rings. Run REMEMBER_UNFOCUSED_RINGS first.
    """
    reached: list[int] = []
    problems = []
    for _ in range(200):
        page.keyboard.press("Tab")
        # The skip link slides into view when it has focus.
        page.wait_for_function("document.getAnimations().length === 0")
        stop = page.evaluate(FOCUSED)
        if stop is None or (reached and stop["index"] == reached[0]):
            return len(reached), problems
        if reached and stop["index"] == reached[-1]:
            continue  # Tab moves between the parts of a date field
        reached.append(stop["index"])
        before, now = stop["unfocused"], stop["focused"]
        changed = before is None or (now["outline"], now["shadow"]) != (
            before["outline"],
            before["shadow"],
        )
        if not now["inView"]:
            problems.append(f"{stop['name']} is out of sight")
        elif not changed or (now["outline"] is None and now["shadow"] == "none"):
            problems.append(f"{stop['name']} looks the same with focus as without")
        elif now["outline"] and now["outline"].startswith("auto "):
            problems.append(f"{stop['name']} has only the browser's own focus ring")
    pytest.fail("Tab never came back around")


@pytest.mark.parametrize(
    ("who", "path"), FOCUS_PAGES, ids=[f"{w or 'anonymous'}:{p}" for w, p in FOCUS_PAGES]
)
def test_each_control_shows_a_focus_ring(browser, live_server, sign_in, who, path) -> None:
    """Tab through the page; each control's outline or shadow changes when it has focus.

    axe has no rule for visible focus (2.4.7). USWDS draws a 4-pixel ring, and
    a control the test can't see a ring on is one where something has removed
    it or put it out of sight, or where the browser's own ring stands in for it.
    """
    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    page.goto(url)
    page.evaluate(REMEMBER_UNFOCUSED_RINGS)
    reached, problems = tab_through(page)
    context.close()
    assert reached > 3
    assert problems == []


def test_each_control_shows_a_focus_ring_on_a_phone(browser, live_server, sign_in) -> None:
    """The same on a phone, on the page and then in the open menu."""
    context, page, url = open_page(
        browser, live_server, sign_in, "student", "/centers/{slug}/", bypass_csp=True, **PHONE
    )
    page.goto(url)
    page.evaluate(REMEMBER_UNFOCUSED_RINGS)
    on_the_page, problems = tab_through(page)
    open_the_menu(page)
    in_the_menu, more = tab_through(page)
    context.close()
    assert on_the_page > 3
    assert in_the_menu >= 4  # the close button, two links and Sign out
    assert problems + more == []


# Content htmx swaps into a page: (who, path, steps). Each step is one of the
# functions above that the keyboard tests use. axe runs after each step, once the
# swap has settled.
HTMX_SWAPS = [
    ("student", "/centers/{slug}/schedules/{schedule}/", [show_the_last_open_day]),
    ("student", "/centers/{slug}/appointments/{appointment}/change/", [show_the_last_open_day]),
    ("student", "/centers/{slug}/schedules/{schedule}/book/?{slot}", [choose_something_else]),
    (
        "tutor",
        "/centers/{slug}/appointments/{unwritten}/record/",
        [tag_thesis, attach_the_thesis_handout],
    ),
    ("tutor", "/centers/{slug}/records/{draft}/edit/", [search_resources]),
    ("director", "/centers/{slug}/members/", [search_members]),
    ("director", "/centers/{slug}/forms/{form}/", [move_a_question_down]),
]


@pytest.mark.parametrize(
    ("who", "path", "steps"),
    HTMX_SWAPS,
    ids=[f"{w}:{p}:{'+'.join(s.__name__ for s in steps)}" for w, p, steps in HTMX_SWAPS],
)
def test_swapped_in_content_passes_axe(browser, live_server, sign_in, who, path, steps) -> None:
    context, page, url = open_page(browser, live_server, sign_in, who, path, bypass_csp=True)
    page.goto(url)
    page.evaluate("window.notReloaded = true")
    reports = []
    for step in steps:
        step(page)
        wait_for_htmx(page)
        results = Axe().run(page, options=AXE_OPTIONS)
        if results.violations_count:
            reports.append(f"After {step.__name__}:\n{results.generate_report()}")
    # What axe saw came from htmx, not from the page loading again.
    assert page.evaluate("window.notReloaded") is True
    context.close()
    assert reports == [], "\n".join(reports)


# Pages that between them use most of the USWDS components Sympraxi does: side
# navigation, breadcrumbs, alerts, tags, a table, radio buttons, checkboxes,
# disclosure groups and buttons marked aria-disabled. Each is checked with the
# browser emulating a forced-colors theme (Windows' contrast themes), and again
# with the reduce-motion setting.
EMULATED_PAGES = [
    (None, "/accounts/login/"),
    ("student", "/centers/{slug}/"),
    ("student", "/centers/{slug}/schedules/{schedule}/?date={tomorrow}"),
    ("student", "/centers/{slug}/schedules/{schedule}/book/?{slot}"),
    ("student", "/centers/{slug}/appointments/{appointment}/"),
    ("tutor", "/centers/{slug}/appointments/{unwritten}/record/"),
    ("front_desk", "/centers/{slug}/schedules/{schedule}/day/?date={tomorrow}"),
    ("director", "/centers/{slug}/forms/{form}/"),
]
# With forced colors, axe compares the page's own text colors with the forced
# background, not the colors the browser draws, so its two color rules report
# nonsense: dark text on black under a dark theme, a pass by luck under a light one.
# Everything else (names, roles, structure) still applies, and changes when
# USWDS's forced-colors styles hide or replace parts of a component.
FORCED_COLORS_AXE_OPTIONS = {
    **AXE_OPTIONS,
    "rules": {"color-contrast": {"enabled": False}, "link-in-text-block": {"enabled": False}},
}
# Playwright's context option, the media query it should then match, and axe's options.
EMULATIONS = {
    "forced-colors": (
        {"forced_colors": "active"},
        "(forced-colors: active)",
        FORCED_COLORS_AXE_OPTIONS,
    ),
    "reduced-motion": (
        {"reduced_motion": "reduce"},
        "(prefers-reduced-motion: reduce)",
        AXE_OPTIONS,
    ),
}


@pytest.mark.parametrize("emulation", EMULATIONS)
@pytest.mark.parametrize(
    ("who", "path"), EMULATED_PAGES, ids=[f"{w or 'anonymous'}:{p}" for w, p in EMULATED_PAGES]
)
def test_a_page_passes_axe_with_emulated_settings(
    browser, live_server, sign_in, who, path, emulation
) -> None:
    options, query, axe_options = EMULATIONS[emulation]
    context, page, url = open_page(
        browser, live_server, sign_in, who, path, bypass_csp=True, **options
    )
    page.goto(url)
    assert page.evaluate(f"matchMedia('{query}').matches") is True
    results = Axe().run(page, options=axe_options)
    context.close()
    assert results.violations_count == 0, results.generate_report()


@pytest.mark.parametrize("emulation", EMULATIONS)
def test_the_phone_menu_passes_axe_with_emulated_settings(
    browser, live_server, sign_in, emulation
) -> None:
    """The open menu is where these settings change the most.

    Under forced colors USWDS hides the close button's image and draws the X
    itself, so the button needs a name that isn't the image's alt text. Under
    reduced motion the menu appears without sliding in.
    """
    options, query, axe_options = EMULATIONS[emulation]
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "student",
        "/centers/{slug}/",
        bypass_csp=True,
        **PHONE,
        **options,
    )
    page.goto(url)
    assert page.evaluate(f"matchMedia('{query}').matches") is True
    page.tap(".usa-menu-btn")
    page.wait_for_selector(".usa-nav.is-visible")
    if emulation == "reduced-motion":
        assert page.evaluate("document.getAnimations().length") == 0
    page.wait_for_function("document.getAnimations().length === 0")
    results = Axe().run(page, options=axe_options)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_student_answers_a_survey_by_keyboard_without_signing_in(
    browser, live_server, sign_in
) -> None:
    """Open the emailed link, choose with the space bar, send with Enter. The link then dies."""
    context, page, url = open_page(browser, live_server, sign_in, None, "/survey/{survey_token}/")
    problems = watch_for_csp_problems(page)
    page.goto(url)
    before = Response.objects.filter(form__kind="survey").count()
    page.get_by_label("Somewhat helpful").focus()
    page.keyboard.press("Space")
    page.focus("#answers-survey textarea")
    page.keyboard.type("Longer sessions.")
    tab_to(page, lambda label: label.startswith("Send your answers"))
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Your answers were sent to the center")
    assert Response.objects.filter(form__kind="survey").count() == before + 1
    assert Answer.objects.filter(text="Longer sessions.", response__student=None).exists()
    assert page.goto(url).status == 404
    context.close()
    assert problems == []


def test_a_director_adds_people_from_a_file_by_keyboard(browser, live_server, sign_in) -> None:
    """Choose a file, check the preview, add the people; then review turning a role off.
    The preview and the review are answers to a form, so axe checks them here."""
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "director",
        "/centers/{slug}/members/import/",
        bypass_csp=True,
    )
    page.goto(url)
    page.set_input_files(
        "#id_file",
        {
            "name": "people.csv",
            "mimeType": "text/csv",
            "buffer": b"Email,First name,Last name,Role\n"
            b"ana.ruiz@example.edu,Ana,Ruiz,student\n"
            b"not-an-address,Broken,Line,student\n",
        },
    )
    page.get_by_role("button", name="Check the file").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("h1:has-text('Check the people before adding them')")
    assert "1 line has a problem and will be skipped" in page.content()
    results = Axe().run(page, options=AXE_OPTIONS)
    assert results.violations_count == 0, results.generate_report()

    page.get_by_role("button", name="Add these people").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Added 1 role.")
    assert Membership.objects.filter(user__email="ana.ruiz@example.edu").exists()

    page.goto(url.replace("/import/", "/turn-off/"))
    page.select_option("#id_role", "student")
    page.get_by_role("button", name="See who this changes").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("h1:has-text('Turn off a role for everyone')")
    assert "student roles" in page.content()
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_turning_on_a_calendar_feed_by_keyboard(browser, live_server, sign_in) -> None:
    """The page shows a new feed's address once, after a form, so axe checks it here."""
    context, page, url = open_page(
        browser, live_server, sign_in, "student", "/accounts/calendar/", bypass_csp=True
    )
    page.goto(url)
    page.get_by_role("button", name="Turn on a calendar feed").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#feed-address")
    assert page.input_value("#feed-address").endswith(".ics")
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_director_checks_a_mass_email_by_keyboard(browser, live_server, sign_in) -> None:
    """The review page answers a form, so axe checks it here."""
    context, page, url = open_page(
        browser, live_server, sign_in, "director", "/centers/{slug}/email/", bypass_csp=True
    )
    page.goto(url)
    page.fill("#id_subject", "Workshop on Thursday")
    page.fill("#id_body", "Join us at 3.\nBring a draft.")
    page.get_by_role("button", name="Check it before sending").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("h1:has-text('Check the email before it goes')")
    assert "Nothing has been sent yet." in page.content()
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_director_reviews_moving_a_day_online_by_keyboard(browser, live_server, sign_in) -> None:
    """The review page answers a form, so axe checks it here."""
    context, page, url = open_page(
        browser, live_server, sign_in, "director", "/centers/{slug}/switch-online/", bypass_csp=True
    )
    page.goto(url)
    tomorrow = date.fromordinal(date.today().toordinal() + 1).isoformat()
    page.fill("#id_day", tomorrow)
    page.get_by_role("button", name="See what moves").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("h1:has-text('Check what moves online')")
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_student_attaches_a_draft_by_keyboard(browser, live_server, sign_in) -> None:
    """Choose a file, attach it, and find it on the appointment page, which axe checks."""
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "student",
        "/centers/{slug}/appointments/{appointment}/files/new/",
        bypass_csp=True,
    )
    page.goto(url)
    page.set_input_files(
        "#id_file",
        {"name": "second draft.pdf", "mimeType": "application/pdf", "buffer": PDF},
    )
    page.get_by_role("button", name="Attach it").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("text=second draft.pdf is attached.")
    assert page.get_by_role("link", name="second draft.pdf", exact=True).count() == 1
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


def test_a_student_sends_a_draft_for_feedback_by_keyboard(browser, live_server, sign_in) -> None:
    """Fill in the form, choose a file, send it with Enter, and land on the submission."""
    context, page, url = open_page(
        browser,
        live_server,
        sign_in,
        "student",
        "/centers/{slug}/feedback/send/",
        bypass_csp=True,
    )
    page.goto(url)
    page.get_by_label("Title").fill("Essay 3")
    page.get_by_label("What feedback do you want?").fill("Is my conclusion strong enough?")
    page.set_input_files(
        "#id_file",
        {"name": "essay three.pdf", "mimeType": "application/pdf", "buffer": PDF},
    )
    page.get_by_role("button", name="Send it").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Your draft is in the queue.")
    assert page.get_by_role("link", name="essay three.pdf", exact=True).count() == 1
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()


VIEWER = "/centers/{slug}/feedback/files/{claimed_file}/view/"


def comment_on_the_first_words(page) -> None:
    """Select the first line of text on the first page and comment on it, from the keyboard."""
    page.wait_for_selector(".textLayer span", timeout=30000)
    page.evaluate(
        """() => {
            const span = document.querySelector('.textLayer span');
            const range = document.createRange();
            range.selectNodeContents(span);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.wait_for_function("!document.getElementById('comment-selection').disabled")
    page.locator("#comment-selection").focus()
    page.keyboard.press("Enter")
    page.get_by_label("Your comment").fill("Say where this comes from.")
    page.get_by_role("button", name="Save the comment").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#comment-list li")


def test_a_tutor_comments_in_the_viewer_under_the_csp(browser, live_server, sign_in) -> None:
    """PDF.js draws the page, its worker and fonts load under the real policy, a
    selected line takes a comment, and the comment is still there after a reload."""
    context, page, url = open_page(browser, live_server, sign_in, "tutor", VIEWER)
    problems = watch_for_csp_problems(page)
    page.goto(url)
    comment_on_the_first_words(page)
    assert "Say where this comes from." in page.locator("#comment-list").inner_text()
    assert page.locator(".viewer-highlight").count() >= 1
    page.reload()
    page.wait_for_selector(".textLayer span", timeout=30000)
    assert page.locator("#comment-list li").count() == 1
    page.wait_for_selector(".viewer-highlight")
    context.close()
    assert problems == []


def test_the_viewer_with_a_comment_passes_axe(browser, live_server, sign_in) -> None:
    context, page, url = open_page(browser, live_server, sign_in, "tutor", VIEWER, bypass_csp=True)
    page.goto(url)
    comment_on_the_first_words(page)
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()
