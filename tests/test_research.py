"""The de-identified research export: when it may be made, and what it leaves out."""

import csv
import io
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.forms.models import FormKind
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.reports import demographics, research
from sympraxi.reports.forms import Period
from sympraxi.reports.models import ResearchSettings
from sympraxi.scheduling.models import Modality
from tests.forms_world import answer, make_form
from tests.records_world import appointment_for, make_topics, write_record
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

STANDING = "What is your class standing?"
COURSE = "What course is this for?"
KEY = b"k" * 32


@pytest.fixture
def world(center):
    make_topics()
    world = build_world(center)
    world.registration = make_form(
        center,
        FormKind.REGISTRATION,
        questions=[
            {"label": STANDING, "kind": "one_choice", "options": ["First-year", "Graduate"]}
        ],
    )
    world.intake = make_form(center, questions=[{"label": COURSE}])
    settings = demographics.settings_for(center)
    settings.standing_key = world.registration.published().questions.get(label=STANDING).key
    settings.course_key = world.intake.published().questions.get(label=COURSE).key
    settings.save()
    return world


def session(
    world, standing, course, *, day, tags=("argument:thesis", "revising"), student=None, **kw
):
    student = student or make_member(world.center, Role.STUDENT, full_name=f"Student {day}")
    answer(world.registration, student, {STANDING: standing})
    appointment = appointment_for(world, student, days=-day, **kw)
    answer(world.intake, student, {COURSE: course}, appointment=appointment)
    write_record(appointment, student, tags=tags, summary="Private summary words.")
    return student


def period(world) -> Period:
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    return Period(today - timedelta(days=30), today, None, None)


def build(world, key=KEY):
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    return research.build(world.center, period(world), today=today, key=key)


def data(report):
    return report.tables[0]


def about(report) -> dict:
    return {row[0]: row[1] for row in report.tables[1].rows}


def approve(world, days=30) -> None:
    settings = research.settings_for(world.center)
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    settings.protocol_number = "IRB-2026-114"
    settings.investigator = "Dr. Rivera"
    settings.approved_on = today - timedelta(days=10)
    settings.expires_on = today + timedelta(days=days)
    settings.save()


# --- When it may be made -----------------------------------------------------------------------


def test_nothing_is_allowed_without_a_protocol_and_current_dates(world) -> None:
    settings = research.settings_for(world.center)
    today = timezone.now().date()
    assert "protocol number" in research.allowed(settings, today)
    settings.protocol_number = "IRB-1"
    assert "dates" in research.allowed(settings, today)
    settings.approved_on, settings.expires_on = today + timedelta(days=1), today + timedelta(days=9)
    assert "later day" in research.allowed(settings, today)
    settings.approved_on, settings.expires_on = today - timedelta(days=9), today - timedelta(days=1)
    assert "has ended" in research.allowed(settings, today)
    settings.expires_on = today
    assert research.allowed(settings, today) == ""


# --- What the file holds -------------------------------------------------------------------------


def test_the_file_holds_no_names_ids_text_or_dates(world) -> None:
    students = [session(world, "First-year", "ENG 101", day=n + 1) for n in range(6)]
    report = build(world)
    text = repr([[c for i, c in enumerate(r) if i != 1] for r in data(report).rows])
    for student in students:
        assert student.display_name not in text and student.email not in text
        assert str(student.pk) not in text
    assert "Private summary" not in text and "Rivera" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
    assert [c.key for c in data(report).columns] == [
        "student", "term", "week", "session", "length", "how",
        "standing", "course", "concerns", "stages", "actions", "resources",
    ]  # fmt: skip


def test_a_student_has_one_code_in_a_file_and_another_in_the_next(world) -> None:
    student = session(world, "First-year", "ENG 101", day=1)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 2)
    extra = appointment_for(world, student, days=-8)
    write_record(extra, student)
    first = build(world, key=b"a" * 32)
    codes = [r[0] for r in data(first).rows]
    assert all(re.fullmatch(r"[0-9a-f]{12}", c) for c in codes)
    repeated = [c for c in set(codes) if codes.count(c) == 2]
    assert len(repeated) == 1  # the student with two sessions, one code
    again = build(world, key=b"a" * 32)
    assert [r[0] for r in data(again).rows] == codes  # same key, same codes
    other = build(world, key=b"b" * 32)
    assert not set(codes) & {r[0] for r in data(other).rows}  # a new key, new codes
    assert research.pseudonym(b"a" * 32, student.pk) != research.pseudonym(b"a" * 32, "x")


def test_every_export_makes_its_own_key(world) -> None:
    session(world, "First-year", "ENG 101", day=1)
    today = timezone.now().date()
    one = research.build(world.center, period(world), today=today)
    two = research.build(world.center, period(world), today=today)
    assert data(one).rows[0][0] != data(two).rows[0][0]


def test_sessions_are_placed_by_week_order_length_and_how_held(world) -> None:
    student = session(world, "First-year", "ENG 101", day=1, minutes=45)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 2)
    later = appointment_for(world, student, days=-9, minutes=30, modality=Modality.ONLINE)
    write_record(later, student)
    rows = [r for r in data(build(world)).rows if r[0] == research.pseudonym(KEY, student.pk)]
    assert [r[3] for r in rows] == [1, 2]  # session number, in time order
    assert rows[0][1] == world.term.name and isinstance(rows[0][2], int) and rows[0][2] >= 1
    assert {r[4] for r in rows} == {"46-60", "16-30"} or {r[4] for r in rows} <= set(
        [b for _m, b in research.BANDS] + [research.OVER]
    )
    assert {r[5] for r in rows} == {"online", "in_person"}


@pytest.mark.parametrize(
    ("minutes", "label"),
    [(1, "1-15"), (15, "1-15"), (16, "16-30"), (60, "46-60"), (90, "61-90"), (91, "91 or more")],
)
def test_lengths_are_banded(minutes, label) -> None:
    assert research.band(minutes) == label


# --- Small groups ----------------------------------------


def test_a_standing_or_course_shared_by_fewer_than_five_students_becomes_other(world) -> None:
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    session(world, "Graduate", "ENG 101", day=6)
    session(world, "First-year", "BIO 220", day=7)
    report = build(world)
    standing = {(r[6], r[7]) for r in data(report).rows}
    assert standing == {("First-year", "ENG 101"), ("Other", "Other"), ("First-year", "Other")}
    facts = about(report)
    assert facts["Class standings generalized to Other"] == 1
    assert facts["Courses generalized to Other"] >= 2


def test_a_typed_sentence_never_gets_through_as_a_course(world) -> None:
    for n in range(6):
        session(world, "First-year", "My professor Dr. Smith's class", day=n + 1)
    courses = {r[7] for r in data(build(world)).rows}
    assert courses == {"Other"}


def test_a_rare_topic_becomes_its_parent_or_goes(world) -> None:
    for n in range(6):
        tags = ("citation:apa", "revising") if n < 5 else ("citation:mla", "modeled")
        session(world, "First-year", "ENG 101", day=n + 1, tags=tags)
    report = build(world)
    concerns = {r[8] for r in data(report).rows}
    assert concerns == {"citation:apa", ""}  # MLA had one student, and "citation" alone is rare too
    actions = {r[10] for r in data(report).rows}
    assert actions == {""}  # "modeled" had one student
    assert about(report)["Topics generalized or dropped"] >= 2


def test_the_about_table_says_what_was_done(world) -> None:
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    facts = about(build(world))
    assert facts["IRB protocol"] == "" and facts["Smallest group shown"] == 5
    assert facts["Sessions"] == 5 and facts["Students"] == 5
    assert "new in every export" in facts["Student codes"].lower().replace("n", "n")


# --- The page ----------------------------------------


def page(world) -> str:
    return reverse("reports:research", kwargs={"slug": world.center.slug})


def test_the_page_explains_what_is_missing(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    shown = client.get(page(world)).content.decode()
    assert "Record the IRB protocol number first." in shown
    assert "About this file" not in shown and "sessions in this file" not in shown
    assert client.get(page(world), {"format": "csv"}).status_code == 200  # the page, not a file


def test_a_director_records_the_protocol_and_downloads(client, world) -> None:
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    response = client.post(
        page(world),
        {
            "protocol_number": "IRB-2026-114",
            "investigator": "Dr. Rivera",
            "approved_on": (today - timedelta(days=5)).isoformat(),
            "expires_on": (today + timedelta(days=300)).isoformat(),
        },
        follow=True,
    )
    assert "The protocol is recorded." in response.content.decode()
    assert AuditEvent.objects.filter(action=AuditAction.RESEARCH_SETTINGS_CHANGED).exists()
    shown = client.get(page(world)).content.decode()
    assert "5 sessions in this file" in shown and "About this file" in shown
    download = client.get(page(world), {"format": "csv"})
    assert download.status_code == 200 and "text/csv" in download["Content-Type"]
    assert "no-store" in download["Cache-Control"]
    rows = list(csv.reader(io.StringIO(download.content.decode())))
    assert any("IRB-2026-114" in row for row in rows)
    event = AuditEvent.objects.get(action=AuditAction.RESEARCH_EXPORTED)
    assert event.details["protocol"] == "IRB-2026-114" and event.details["sessions"] == 5
    first = next(r for r in rows if r and re.fullmatch(r"[0-9a-f]{12}", r[0]))[0]
    second_download = client.get(page(world), {"format": "csv"}).content.decode()
    assert first not in second_download  # new codes every time


def test_an_expired_protocol_blocks_the_download(client, world) -> None:
    approve(world, days=30)
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    ResearchSettings.objects.update(expires_on=today - timedelta(days=1))
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert "has ended" in client.get(page(world)).content.decode()
    client.get(page(world), {"format": "csv"})
    assert not AuditEvent.objects.filter(action=AuditAction.RESEARCH_EXPORTED).exists()


def test_bad_dates_are_refused(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    response = client.post(
        page(world),
        {"protocol_number": "IRB-1", "approved_on": "2026-05-02", "expires_on": "2026-05-01"},
    )
    assert "can&#x27;t end before it starts" in response.content.decode()
    assert not research.settings_for(world.center).protocol_number


def test_only_directors_open_it(client, world, person_with_role) -> None:
    for role in ("tutor", "front_desk", "student", "site_admin"):
        client.force_login(person_with_role(role, world.center))
        assert client.get(page(world)).status_code == 403
