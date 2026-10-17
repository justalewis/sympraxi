"""Every page passes axe, and no page trips the Content Security Policy.

Run with: uv run pytest -m e2e  (after `npm --prefix frontend run build` and
`uv run playwright install chromium`). Add each new page to PAGES, or to
SSO_PAGES if it only looks different once single sign-on is set up.
"""

from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from axe_playwright_python.sync_playwright import Axe
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
from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa
from tests.forms_world import add_forms
from tests.records_world import add_records
from tests.reports_world import add_reports
from tests.scheduling_world import build_world

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
    world = add_reports(
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
    # The center's intake questions are on the page; two are required.
    page.get_by_label("What course is this for?").fill("ENG 101")
    # USWDS draws its own radio buttons and keeps the real input off-screen; click the label.
    page.get_by_text("Essay", exact=True).click()
    page.click("button:has-text('Book it')")
    page.wait_for_selector("text=You're booked")
    assert "/appointments/" in page.url
    page.wait_for_selector("text=Your answers for this appointment")
    context.close()


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

    # Open the "Ideas and argument" group and check "Thesis" with the space bar.
    page.focus("summary:has-text('Ideas and argument')")
    page.keyboard.press("Enter")
    page.focus("#topic-argument--thesis")
    page.keyboard.press("Space")
    assert page.is_checked("#topic-argument--thesis")

    # The suggestions follow the topic, and the status line says so.
    page.wait_for_selector("#resource-results >> text=Our thesis handout")
    assert "suggestion" in page.locator("#resource-status").inner_text()

    tab_to(page, lambda label: label.startswith("Attach Our thesis handout"))
    page.keyboard.press("Enter")
    page.wait_for_selector("#attached-resources >> text=Our thesis handout")
    assert "Attached Our thesis handout" in page.locator("#resource-status").inner_text()
    # htmx applies the swapped-in class after its settle delay (20 ms), so wait for it.
    page.wait_for_selector("#attached-empty", state="hidden")

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
    page.fill("#resource-search", "handout")
    page.wait_for_selector("#resource-results >> text=Matching “handout”")
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
    page.get_by_label("What course is this for?").fill("ENG 101")
    other = page.get_by_label("Something else")
    other.focus()
    page.keyboard.press("Space")
    page.wait_for_selector("text=What kind of writing, then?")
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
    tab_to(page, lambda label: label.startswith("Move down: What course is this for?"))
    focused = page.evaluate("document.activeElement.id")
    page.keyboard.press("Enter")
    page.wait_for_selector("#builder-status >> text=Moved “What course is this for?” down.")
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


def test_a_dead_survey_link_passes_axe(browser, live_server) -> None:
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    response = page.goto(live_server.url + "/survey/" + "a" * 43 + "/")
    assert response is not None and response.status == 404
    assert "doesn't open a survey" in page.locator("main").inner_text()
    results = Axe().run(page, options=AXE_OPTIONS)
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
