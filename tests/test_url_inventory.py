"""Every Sympraxi URL must declare who may use it.

Adding a view without classifying it here fails `test_every_url_is_classified`.
That is the point: no page that touches student data ships by accident.
Third-party URLs (allauth, the Django admin) are excluded; the admin is
restricted to site administrators and sits behind the same 2FA gate. allauth's
single sign-on URLs (/accounts/oidc/<provider>/...) are covered in test_sso.py.

Token pages open without signing in: a secret token in the URL is what opens
them (survey links now, calendar feeds later). Each says how to make a working
token and dead ones; every dead token, and an unknown one, must get exactly the
same answer, so tokens can't be probed.
"""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from sympraxi.core import policy
from sympraxi.core.policy import Action
from sympraxi.forms import surveys
from sympraxi.forms.models import SurveyInvitation
from sympraxi.notify import feeds
from tests.alerts_world import add_alerts
from tests.attendance_world import add_attendance
from tests.content_world import make_content_item
from tests.disclosure_world import add_disclosures
from tests.factories import CenterFactory
from tests.feedback_world import add_queue
from tests.files_world import add_files
from tests.forms_world import SURVEY_ANSWERS, add_forms, values
from tests.news_world import add_news
from tests.records_world import add_records
from tests.reports_world import add_reports
from tests.scheduling_world import World, build_world
from tests.staff_world import add_time_clock
from tests.training_world import add_onboarding, add_training
from tests.workshops_world import add_workshops

# accounts:local_login is the break-glass password sign-in. It is public like the
# regular sign-in page; AccountAdapter.pre_login() decides who may use it.
# survey:thanks says only "thank you", to anyone.
PUBLIC = {
    "core:home",
    "core:healthz",
    "core:source",
    "accounts:local_login",
    "survey:thanks",
    "lti:config",
    "core:language",  # switches the page language and redirects back; needs a code
}

# Pages about the signed-in person's own account, outside any center. Anyone
# signed in may use them; they show and change only that person's settings.
# library:item is an open-content page any signed-in person may read; it needs an item id.
LOGIN_REQUIRED = {
    "accounts:profile",
    "accounts:reminders",
    "notify:calendar",
    "library:item",
    "sms:settings",
}

# Center-scoped pages, the action each one requires, and the object the action is
# checked against ("center", or an attribute of the test World).
CENTER_PAGES: dict[str, tuple[Action, str]] = {
    "centers:detail": (Action.VIEW_CENTER, "center"),
    "centers:members": (Action.VIEW_MEMBERS, "center"),
    "centers:person_add": (Action.MANAGE_MEMBERS, "center"),
    "centers:import": (Action.MANAGE_MEMBERS, "center"),
    "centers:roles_off": (Action.MANAGE_MEMBERS, "center"),
    "centers:person": (Action.MANAGE_MEMBERS, "student_membership"),
    "privacy:audit_log": (Action.VIEW_AUDIT_LOG, "center"),
    "privacy:disclosures": (Action.RECORD_DISCLOSURES, "student_membership"),
    "privacy:my_disclosures": (Action.VIEW_OWN_WRITING, "center"),
    # Files on appointments
    "files:upload": (Action.ATTACH_FILES, "appointment"),
    "files:download": (Action.VIEW_FILES, "appointment_file"),
    "files:remove": (Action.VIEW_FILES, "appointment_file"),
    "feedback:submit": (Action.SUBMIT_WORK, "center"),
    "feedback:mine": (Action.SUBMIT_WORK, "center"),
    "feedback:detail": (Action.VIEW_SUBMISSION, "submission"),
    "feedback:cancel": (Action.VIEW_SUBMISSION, "submission"),
    "feedback:add_file": (Action.VIEW_SUBMISSION, "submission"),
    "feedback:download": (Action.VIEW_SUBMISSION, "submission_file"),
    "feedback:remove_file": (Action.VIEW_SUBMISSION, "submission_file"),
    "feedback:queue": (Action.WORK_QUEUE, "center"),
    "feedback:claim": (Action.WORK_QUEUE, "submission"),
    "feedback:assign": (Action.MANAGE_QUEUE, "submission"),
    "feedback:settings": (Action.MANAGE_QUEUE, "center"),
    "feedback:turnaround": (Action.MANAGE_QUEUE, "center"),
    "feedback:viewer": (Action.VIEW_SUBMISSION, "submission_file"),
    "feedback:pdf": (Action.VIEW_SUBMISSION, "submission_file"),
    "feedback:comments": (Action.VIEW_SUBMISSION, "submission_file"),
    "online:join": (Action.JOIN_VIDEO, "appointment"),
    "online:room": (Action.USE_ROOM, "appointment"),
    "online:room_versions": (Action.USE_ROOM, "appointment"),
    "online:settings": (Action.MANAGE_SCHEDULES, "center"),
    # Booking
    "scheduling:schedules": (Action.VIEW_SCHEDULES, "center"),
    "scheduling:open_times": (Action.VIEW_SCHEDULES, "schedule"),
    "scheduling:book": (Action.BOOK_OWN, "schedule"),
    "scheduling:staff_book": (Action.BOOK_FOR_OTHERS, "schedule"),
    "scheduling:day_sheet": (Action.VIEW_ALL_APPOINTMENTS, "schedule"),
    "scheduling:my_appointments": (Action.BOOK_OWN, "center"),
    "scheduling:my_tutoring": (Action.VIEW_OWN_TUTORING, "center"),
    "scheduling:waiting_list": (Action.BOOK_OWN, "center"),
    "scheduling:waiting_list_manage": (Action.BOOK_FOR_OTHERS, "center"),
    "scheduling:appointment": (Action.VIEW_APPOINTMENT, "appointment"),
    "scheduling:appointment_ics": (Action.VIEW_APPOINTMENT, "appointment"),
    "scheduling:appointment_cancel": (Action.CANCEL_APPOINTMENT, "appointment"),
    "scheduling:appointment_change": (Action.CHANGE_APPOINTMENT, "appointment"),
    "scheduling:appointment_confirm": (Action.BOOK_FOR_OTHERS, "appointment"),
    # Setup
    "scheduling:schedule_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:switch_online": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:manage": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:schedule_edit": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:assignment_create": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:assignment_edit": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:hours": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:exception_create": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:exception_delete": (Action.MANAGE_SCHEDULES, "exception"),
    "scheduling:terms": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:term_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:term_edit": (Action.MANAGE_SCHEDULES, "term"),
    "scheduling:closure_create": (Action.MANAGE_SCHEDULES, "term"),
    "scheduling:closure_delete": (Action.MANAGE_SCHEDULES, "closure"),
    "scheduling:rooms": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:room_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:room_edit": (Action.MANAGE_SCHEDULES, "room"),
    "scheduling:exemptions": (Action.MANAGE_EXEMPTIONS, "center"),
    "scheduling:exemption_create": (Action.MANAGE_EXEMPTIONS, "center"),
    "scheduling:exemption_revoke": (Action.MANAGE_EXEMPTIONS, "exemption"),
    # Attendance and booking blocks
    "scheduling:attendance": (Action.MARK_ATTENDANCE, "appointment"),
    "scheduling:attendance_list": (Action.MARK_ATTENDANCE, "center"),
    "scheduling:waive": (Action.WAIVE_ATTENDANCE, "appointment"),
    "scheduling:blocks": (Action.MANAGE_BLOCKS, "center"),
    "scheduling:block_settings": (Action.MANAGE_BLOCKS, "center"),
    "scheduling:block_lift": (Action.MANAGE_BLOCKS, "block"),
    # Session records. "record" is final, "draft" is a draft.
    "records:start": (Action.WRITE_RECORDS, "appointment"),
    "records:to_write": (Action.VIEW_RECORD_STATUS, "center"),
    "records:new": (Action.WRITE_RECORDS, "center"),
    "records:detail": (Action.VIEW_RECORDS, "record"),
    "records:edit": (Action.WRITE_RECORDS, "draft"),
    "records:amend": (Action.WRITE_RECORDS, "record"),
    "records:resource_panel": (Action.WRITE_RECORDS, "center"),
    "records:attach_row": (Action.WRITE_RECORDS, "center"),
    "records:student_history": (Action.VIEW_RECORDS, "student_membership"),
    "records:export": (Action.EXPORT_STUDENT_RECORD, "student_membership"),
    "records:export_json": (Action.EXPORT_STUDENT_RECORD, "student_membership"),
    "records:project_edit": (Action.WRITE_RECORDS, "project"),
    "records:my_writing": (Action.VIEW_OWN_WRITING, "center"),
    "records:my_record": (Action.VIEW_OWN_WRITING, "record"),
    "records:settings": (Action.MANAGE_RECORDS, "center"),
    "records:resources": (Action.MANAGE_RECORDS, "center"),
    "records:resource_create": (Action.MANAGE_RECORDS, "center"),
    "records:resource_edit": (Action.MANAGE_RECORDS, "resource"),
    "records:resource_links": (Action.MANAGE_RECORDS, "center"),
    "records:resource_check": (Action.MANAGE_RECORDS, "resource"),
    "records:favorites": (Action.WRITE_RECORDS, "center"),
    "records:favorite_toggle": (Action.WRITE_RECORDS, "resource"),
    "reports:resources": (Action.VIEW_STATISTICS, "center"),
    "reports:by_course": (Action.VIEW_STATISTICS, "center"),
    "reports:research": (Action.EXPORT_RESEARCH, "center"),
    "reports:report_questions": (Action.MANAGE_FORMS, "center"),
    "records:my_goals": (Action.VIEW_OWN_WRITING, "center"),
    "records:my_timeline": (Action.VIEW_OWN_WRITING, "center"),
    "records:student_goals": (Action.SET_GOALS, "student_membership"),
    "records:student_timeline": (Action.VIEW_RECORDS, "student_membership"),
    # Forms. "form" is the default intake form, with a draft; "question" is in the draft.
    "forms:list": (Action.MANAGE_FORMS, "center"),
    "forms:create": (Action.MANAGE_FORMS, "center"),
    "forms:detail": (Action.MANAGE_FORMS, "form"),
    "forms:question_create": (Action.MANAGE_FORMS, "form"),
    "forms:preview": (Action.MANAGE_FORMS, "form"),
    "forms:publish": (Action.MANAGE_FORMS, "form"),
    "forms:question_edit": (Action.MANAGE_FORMS, "question"),
    "forms:question_remove": (Action.MANAGE_FORMS, "question"),
    "forms:my_registration": (Action.ANSWER_OWN_FORMS, "center"),
    "forms:intake": (Action.ANSWER_OWN_FORMS, "appointment"),
    "forms:student_registration": (Action.VIEW_REGISTRATION, "student_membership"),
    "forms:survey_settings": (Action.MANAGE_FORMS, "center"),
    "forms:survey_results": (Action.VIEW_SURVEY_RESULTS, "center"),
    # Reports. Each downloads from its own address with ?format=.
    "reports:statistics": (Action.VIEW_STATISTICS, "center"),
    "reports:utilization": (Action.VIEW_STATISTICS, "center"),
    "reports:appointment_list": (Action.VIEW_MASTER_LISTS, "center"),
    "reports:student_list": (Action.VIEW_MASTER_LISTS, "center"),
    "reports:center_export": (Action.EXPORT_CENTER, "center"),
    "reports:center_export_download": (Action.EXPORT_CENTER, "center_export"),
    # Time clock
    "staff:clock": (Action.CLOCK_OWN, "center"),
    "staff:entries": (Action.MANAGE_TIME, "center"),
    "staff:approvals": (Action.MANAGE_TIME, "center"),
    "staff:settings": (Action.MANAGE_TIME, "center"),
    "staff:kiosk": (Action.RUN_KIOSK, "center"),
    "staff:waiting_room": (Action.VIEW_WAITING_ROOM, "center"),
    # Workshops
    "workshops:list": (Action.VIEW_WORKSHOPS, "center"),
    "workshops:create": (Action.MANAGE_WORKSHOPS, "center"),
    "workshops:detail": (Action.VIEW_WORKSHOPS, "workshop"),
    "workshops:edit": (Action.MANAGE_WORKSHOPS, "workshop"),
    "workshops:register": (Action.REGISTER_WORKSHOP, "workshop"),
    "workshops:roster": (Action.TAKE_WORKSHOP_ATTENDANCE, "workshop"),
    # Tutor training
    "training:home": (Action.TAKE_TRAINING, "center"),
    "training:module": (Action.TAKE_TRAINING, "training_module"),
    "training:item": (Action.TAKE_TRAINING, "training_item"),
    "training:manage": (Action.MANAGE_TRAINING, "center"),
    "training:module_create": (Action.MANAGE_TRAINING, "center"),
    "training:module_edit": (Action.MANAGE_TRAINING, "training_module"),
    "training:item_edit": (Action.MANAGE_TRAINING, "training_item"),
    "training:onboarding": (Action.MANAGE_TRAINING, "center"),
    "training:cohorts": (Action.MANAGE_TRAINING, "center"),
    "training:cohort": (Action.MANAGE_TRAINING, "cohort"),
    "training:templates": (Action.MANAGE_TRAINING, "center"),
    "training:template": (Action.MANAGE_TRAINING, "checklist"),
    "training:mentees": (Action.MENTOR_TUTORS, "center"),
    "training:mentee": (Action.MENTOR_TUTORS, "mentee"),
    "training:observation": (Action.OBSERVE, "observation"),
    # Early alerts and shared visits
    "alerts:list": (Action.HANDLE_ALERTS, "center"),
    "alerts:mine": (Action.RAISE_ALERT, "center"),
    "alerts:detail": (Action.VIEW_ALERT, "alert"),
    "alerts:raise": (Action.RAISE_ALERT, "student_membership"),
    "alerts:share": (Action.SHARE_VISIT, "appointment"),
    "alerts:shares": (Action.SHARE_VISIT, "center"),
    # API tokens and webhooks
    "integrations:home": (Action.MANAGE_INTEGRATIONS, "center"),
    "staff:entry_add": (Action.MANAGE_TIME, "center"),
    "staff:entry_edit": (Action.MANAGE_TIME, "time_entry"),
    # News
    "news:announcements": (Action.MANAGE_ANNOUNCEMENTS, "center"),
    "news:announcement_create": (Action.MANAGE_ANNOUNCEMENTS, "center"),
    "news:announcement_edit": (Action.MANAGE_ANNOUNCEMENTS, "announcement"),
    "news:mass_email": (Action.SEND_MASS_EMAIL, "center"),
    "news:mass_email_log": (Action.SEND_MASS_EMAIL, "center"),
    "news:mass_email_detail": (Action.SEND_MASS_EMAIL, "mass_email"),
}


@dataclass
class TokenPage:
    """How to test one token page: a working token, and the kinds of dead ones."""

    # The world's working token.
    valid: Callable[[World], str]
    # Name -> make a token that once worked and doesn't now (used, expired, ...).
    dead: dict[str, Callable[[World], str]]


def _used_survey_token(world: World) -> str:
    token = world.survey_token
    survey = surveys.open_survey(token)
    assert survey is not None
    assert surveys.submit(survey, token, values(survey.version, SURVEY_ANSWERS))
    return token


def _expired_survey_token(world: World) -> str:
    SurveyInvitation.objects.filter(student=world.student).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    return world.survey_token


def _stopped_survey_token(world: World) -> str:
    world.survey_form.is_active = False
    world.survey_form.save(update_fields=["is_active"])
    return world.survey_token


def _replaced_feed_token(world: World) -> str:
    old = feeds.start_feed(world.student)
    feeds.start_feed(world.student)
    return old


def _turned_off_feed_token(world: World) -> str:
    token = feeds.start_feed(world.student)
    feeds.stop_feed(world.student)
    return token


def _feed_of_turned_off_account(world: World) -> str:
    token = feeds.start_feed(world.student)
    world.student.is_active = False
    world.student.save(update_fields=["is_active"])
    return token


def _revoked_share(world: World) -> str:
    world.share.revoked_at = timezone.now()
    world.share.save(update_fields=["revoked_at"])
    return world.share_token


def _expired_share(world: World) -> str:
    world.share.expires_at = timezone.now() - timedelta(minutes=1)
    world.share.save(update_fields=["expires_at"])
    return world.share_token


def _share_of_turned_off_account(world: World) -> str:
    world.student.is_active = False
    world.student.save(update_fields=["is_active"])
    return world.share_token


TOKEN_PAGES: dict[str, TokenPage] = {
    "visit:confirmation": TokenPage(
        valid=lambda world: world.share_token,
        dead={
            "revoked": _revoked_share,
            "expired": _expired_share,
            "account turned off": _share_of_turned_off_account,
        },
    ),
    "survey:answer": TokenPage(
        valid=lambda world: world.survey_token,
        dead={
            "used": _used_survey_token,
            "expired": _expired_survey_token,
            "survey stopped": _stopped_survey_token,
        },
    ),
    "notify:feed": TokenPage(
        valid=lambda world: feeds.start_feed(world.student),
        dead={
            "replaced": _replaced_feed_token,
            "turned off": _turned_off_feed_token,
            "account turned off": _feed_of_turned_off_account,
        },
    ),
}

# The URL keyword for each kind of object.
URL_KWARGS = {
    "schedule": "schedule_id",
    "appointment": "appointment_id",
    "assignment": "assignment_id",
    "exception": "exception_id",
    "term": "term_id",
    "closure": "closure_id",
    "room": "room_id",
    "exemption": "exemption_id",
    "record": "record_id",
    "draft": "record_id",
    "student_membership": "membership_id",
    "project": "project_id",
    "resource": "resource_id",
    "block": "block_id",
    "form": "form_id",
    "question": "question_id",
    "center_export": "export_id",
    "time_entry": "entry_id",
    "announcement": "announcement_id",
    "mass_email": "mass_email_id",
    "appointment_file": "file_id",
    "submission": "submission_id",
    "submission_file": "file_id",
    "workshop": "workshop_id",
    "training_module": "module_id",
    "training_item": "item_id",
    "cohort": "cohort_id",
    "alert": "alert_id",
    "checklist": "template_id",
    "mentee": "member_id",
    "observation": "observation_id",
}

# Pages that need a chosen open time in the query string.
NEEDS_SLOT = {"scheduling:book", "scheduling:staff_book"}
# Pages that name the student in the query string.
NEEDS_STUDENT = {"records:start", "scheduling:waive"}

ROLES = ["none", "student", "tutor", "front_desk", "director", "site_admin"]


def sympraxi_url_names() -> set[str]:
    names: set[str] = set()

    def walk(patterns: list[URLPattern | URLResolver], namespaces: list[str]) -> None:
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, [*namespaces, p.namespace] if p.namespace else namespaces)
            elif p.callback.__module__.startswith("sympraxi."):
                names.add(":".join([*namespaces, p.name or "<unnamed>"]))

    walk(get_resolver().url_patterns, [])
    return names


def url_for(name: str, world: World) -> str:
    kind = CENTER_PAGES[name][1]
    kwargs = {"slug": world.center.slug}
    if kind != "center":
        kwargs[URL_KWARGS[kind]] = str(getattr(world, kind).pk)
    url = reverse(name, kwargs=kwargs)
    if name in NEEDS_STUDENT:
        return f"{url}?student={world.student.pk}"
    return f"{url}?{world.slot_query()}" if name in NEEDS_SLOT else url


def object_for(name: str, world: World) -> object:
    kind = CENTER_PAGES[name][1]
    return world.center if kind == "center" else getattr(world, kind)


@pytest.fixture
def world(center) -> World:
    built = build_world(center)
    for add in (
        add_records,
        add_attendance,
        add_forms,
        add_reports,
        add_time_clock,
        add_news,
        add_disclosures,
        add_files,
        add_queue,
        add_workshops,
        add_training,
        add_onboarding,
        add_alerts,
    ):
        built = add(built)
    return built


# The editor service's address: no session, only the shared secret (checked in
# tests/test_room.py), and answered 404 by Caddy from outside.
SECRET_ENDPOINTS = {"collab:load", "collab:store"}

# The text message provider's webhook for replies. Open to the internet, answers only a
# request signed by the provider (tests/test_sms.py).
SIGNED_ENDPOINTS = {"sms:inbound"}

# The CAS sign-in pages. Public, and answer 404 until the host sets a CAS server
# (tests/test_sso_more.py checks the flow).
CAS_ENDPOINTS = {"accounts:cas_login", "accounts:cas_callback"}

# The learning system launch. Open to the internet; answers only a signed launch from a
# registered platform (tests/test_lti.py).
LTI_ENDPOINTS = {"lti:login", "lti:launch"}

# The JSON API. It answers to a bearer token, not a session; tests/test_integrations.py
# checks what each does. Here: none answers without one.
API_ENDPOINTS = {
    "api:schedules",
    "api:appointments",
    "api:cancel",
    "api:students",
    "api:workshops",
}


def test_every_url_is_classified() -> None:
    classified = (
        PUBLIC
        | LOGIN_REQUIRED
        | set(CENTER_PAGES)
        | set(TOKEN_PAGES)
        | SECRET_ENDPOINTS
        | API_ENDPOINTS
        | SIGNED_ENDPOINTS
        | CAS_ENDPOINTS
        | LTI_ENDPOINTS
    )
    assert sympraxi_url_names() == classified


@pytest.mark.parametrize("name", sorted(API_ENDPOINTS))
def test_api_endpoints_refuse_anyone_without_a_token(client, world, name) -> None:
    kwargs = {"slug": world.center.slug}
    if name == "api:cancel":
        kwargs["appointment_id"] = str(world.appointment.pk)
    url = reverse(name, kwargs=kwargs)
    client.force_login(world.student)  # a session is not a token
    for method in (client.get, client.post):
        response = method(url)
        assert response.status_code in (401, 405)
    assert client.get(url, HTTP_AUTHORIZATION="Bearer sym_nope_nope").status_code in (401, 405)


def _token_url(name: str, token: str) -> str:
    return reverse(name, kwargs={"token": token})


def _same_page(body: bytes) -> bytes:
    """The page without its per-request CSP nonces, which differ every time."""
    return re.sub(rb'nonce="[^"]*"', b'nonce=""', body)


@pytest.mark.parametrize("name", sorted(TOKEN_PAGES))
def test_token_pages_open_without_signing_in(client, world, name) -> None:
    response = client.get(_token_url(name, TOKEN_PAGES[name].valid(world)))
    assert response.status_code == 200


@pytest.mark.parametrize("name", sorted(TOKEN_PAGES))
def test_token_pages_ignore_who_is_signed_in(client, person_with_role, world, name) -> None:
    """Signed in or not, as anyone, the working token opens the page. Staff without
    two-factor login aren't sent to set it up, which would put the token in a URL."""
    for role in ("student", "director", "site_admin"):
        client.force_login(person_with_role(role, world.center, mfa=False))
        response = client.get(_token_url(name, TOKEN_PAGES[name].valid(world)))
        assert response.status_code == 200, role


@pytest.mark.parametrize("name", sorted(TOKEN_PAGES))
def test_dead_and_unknown_tokens_look_alike(client, db, name) -> None:
    """Unknown, used, expired: the same status and the same page, so tokens can't be probed."""
    page = TOKEN_PAGES[name]
    answers = {}
    for kind, token in [
        ("unknown", secrets.token_urlsafe(32)),
        ("not a token", "x"),
        ("far too long", "a" * 500),
    ]:
        response = client.get(_token_url(name, token))
        answers[kind] = (response.status_code, _same_page(response.content))
    for kind, make in page.dead.items():
        world = add_alerts(add_forms(add_attendance(add_records(build_world(CenterFactory())))))
        token = make(world)
        for response in (client.get(_token_url(name, token)), client.post(_token_url(name, token))):
            answers[f"{kind} ({response.request['REQUEST_METHOD']})"] = (
                response.status_code,
                _same_page(response.content),
            )
    assert answers["unknown"][0] == 404
    assert len(set(answers.values())) == 1, sorted(answers)


@pytest.mark.parametrize("name", sorted(PUBLIC - {"core:source", "core:language"}))
def test_public_pages_load_anonymously(client, db, name) -> None:
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", sorted(CAS_ENDPOINTS))
def test_cas_pages_are_off_without_a_cas_server(client, db, name) -> None:
    assert client.get(reverse(name)).status_code == 404


def _login_url(name: str) -> str:
    if name == "library:item":
        return reverse(name, kwargs={"item_id": make_content_item().pk})
    return reverse(name)


@pytest.mark.parametrize("name", sorted(LOGIN_REQUIRED))
def test_account_pages_need_sign_in(client, db, name) -> None:
    response = client.get(_login_url(name))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("name", sorted(LOGIN_REQUIRED))
def test_account_pages_open_for_everyone_signed_in(
    client, person_with_role, center, name, role
) -> None:
    client.force_login(person_with_role(role, center))
    assert client.get(_login_url(name)).status_code == 200


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_anonymous_visitors_are_sent_to_sign_in(client, world, name) -> None:
    response = client.get(url_for(name, world))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_outsiders_get_404(client, person_with_role, world, other_center, name) -> None:
    outsider = person_with_role("director", other_center)
    client.force_login(outsider)
    assert client.get(url_for(name, world)).status_code == 404


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_each_role_gets_what_the_policy_allows(
    client, person_with_role, center, world, name, role
) -> None:
    """The view enforces the action it declares. The matrix test says who has it."""
    user = person_with_role(role, center)
    client.force_login(user)
    action, _ = CENTER_PAGES[name]
    obj = object_for(name, world)
    if policy.can(user, action, obj):
        expected = 200
    elif policy.can(user, action, center) or not (
        policy.roles_in(user, center) or user.is_superuser
    ):
        expected = 404  # theirs to see in general, but not this one; or not a member
    else:
        expected = 403
    assert client.get(url_for(name, world)).status_code == expected


def test_students_open_their_own_appointment(client, center) -> None:
    world = build_world(center)
    client.force_login(world.student)
    for name in ("scheduling:appointment", "scheduling:appointment_ics"):
        assert client.get(url_for(name, world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_cancel", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_change", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_confirm", world)).status_code == 403


def test_tutors_open_appointments_they_teach_but_cannot_cancel_them(client, center) -> None:
    world = build_world(center)
    client.force_login(world.tutor)
    assert client.get(url_for("scheduling:appointment", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_cancel", world)).status_code == 403


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_directors_can_open_every_center_page_they_are_granted(
    client, person_with_role, world, name
) -> None:
    director = person_with_role("director", world.center)
    client.force_login(director)
    action, _ = CENTER_PAGES[name]
    expected = 200 if policy.can(director, action, object_for(name, world)) else 403
    assert client.get(url_for(name, world)).status_code == expected
