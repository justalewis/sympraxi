"""Topics by class standing and course: which questions are read, how answers are tidied,
what is left out because the group is small, and the pages."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.forms.models import FormKind
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records.models import WritingProject
from sympraxi.reports import demographics
from sympraxi.reports.forms import Period
from sympraxi.scheduling.models import Term
from tests.forms_world import answer, make_form
from tests.records_world import appointment_for, make_topics, write_record
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

STANDING = "What is your class standing?"
COURSE = "What course is this for?"


@pytest.fixture
def world(center):
    make_topics()
    world = build_world(center)
    world.registration = make_form(
        center,
        FormKind.REGISTRATION,
        questions=[
            {
                "label": STANDING,
                "kind": "one_choice",
                "options": ["First-year", "Sophomore", "Junior"],
            },
            {"label": "Tell us about yourself", "kind": "long_text"},
        ],
    )
    world.intake = make_form(
        center, questions=[{"label": COURSE}, {"label": "Pages", "kind": "number"}]
    )
    return world


def choose_questions(world, standing=True, course=True):
    settings = demographics.settings_for(world.center)
    if standing:
        settings.standing_key = world.registration.published().questions.get(label=STANDING).key
    if course:
        settings.course_key = world.intake.published().questions.get(label=COURSE).key
    settings.save()


def session(world, standing: str, course: str, tags=("argument:thesis",), *, day: int):
    """A student of the given standing has a finished session about the course."""
    student = make_member(world.center, Role.STUDENT)
    answer(world.registration, student, {STANDING: standing})
    appointment = appointment_for(world, student, days=-day)
    answer(world.intake, student, {COURSE: course}, appointment=appointment)
    write_record(appointment, student, tags=(*tags, "revising"))
    return student


def period(world) -> Period:
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    return Period(today - timedelta(days=30), today, None, None)


def table(report, name):
    return next(t for t in report.tables if t.name == name)


# --- What is counted ---------------------------------------------------------------------------


def test_sessions_are_counted_by_standing_and_course(world) -> None:
    choose_questions(world)
    for n in range(6):
        session(world, "First-year", ["eng 101", "ENG  101", "Eng 101"][n % 3], day=n + 1)
    report = demographics.build(world.center, period(world))
    assert table(report, "standing").rows == [["First-year", 6, 6]]
    assert table(report, "course").rows == [["ENG 101", 6, 6]]  # spacing and case tidied


def test_a_group_of_fewer_than_five_students_is_left_out_and_counted(world) -> None:
    choose_questions(world)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    session(world, "Sophomore", "ENG 101", day=6)
    report = demographics.build(world.center, period(world))
    standing = table(report, "standing")
    assert [r[0] for r in standing.rows] == ["First-year"]
    assert "1 groups with fewer than 5 students are left out" in standing.note
    assert table(report, "course").rows == [["ENG 101", 6, 6]]


def test_concerns_are_by_standing_and_course_with_the_same_rule(world) -> None:
    choose_questions(world)
    for n in range(5):
        tags = ("argument:thesis", "citation:apa") if n < 3 else ("argument:thesis",)
        session(world, "First-year", "ENG 101", tags, day=n + 1)
    concerns = table(demographics.build(world.center, period(world)), "concerns")
    assert [(r[0], r[1], r[2], r[4], r[5]) for r in concerns.rows] == [
        ("First-year", "ENG 101", "Thesis, claim or focus", 5, 5)
    ]  # APA style had three students, so no row
    assert "fewer than 5 students" in concerns.note


def test_the_report_can_be_narrowed_to_one_standing(world) -> None:
    choose_questions(world)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    for n in range(5):
        session(world, "Junior", "BIO 220", ("evidence:quoting",), day=n + 6)
    report = demographics.build(world.center, period(world), standing="Junior")
    assert table(report, "standing").rows == [["Junior", 5, 5]]
    assert [r[2] for r in table(report, "concerns").rows] == ["Quoting"]
    assert report.described["Class standing"] == "Junior"
    assert demographics.standings_seen(world.center, period(world)) == ["First-year", "Junior"]


def test_a_students_latest_answer_to_standing_is_used(world) -> None:
    choose_questions(world)
    students = [session(world, "First-year", "ENG 101", day=n + 1) for n in range(5)]
    answer(world.registration, students[0], {STANDING: "Sophomore"})
    standing = table(demographics.build(world.center, period(world)), "standing")
    assert standing.rows == [] and "2 groups" in standing.note  # 4 and 1: both under five


def test_the_course_falls_back_to_the_writing_project(world) -> None:
    choose_questions(world, course=False)
    for n in range(5):
        student = make_member(world.center, Role.STUDENT)
        answer(world.registration, student, {STANDING: "First-year"})
        project = WritingProject.objects.create(
            center=world.center, student=student, title="Essay", course="  hist 300 "
        )
        appointment = appointment_for(world, student, days=-(n + 1))
        write_record(appointment, student, project=project)
    assert table(demographics.build(world.center, period(world)), "course").rows == [
        ["HIST 300", 5, 5]
    ]


def test_without_chosen_questions_everything_is_not_given(world) -> None:
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    report = demographics.build(world.center, period(world))
    assert table(report, "standing").rows == [["Not given", 5, 5]]
    assert "No questions are chosen yet" in table(report, "standing").note


def test_drafts_and_old_records_are_left_out(world) -> None:
    choose_questions(world)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    student = make_member(world.center, Role.STUDENT)
    answer(world.registration, student, {STANDING: "First-year"})
    write_record(appointment_for(world, student, days=-2.5), student, final=False)
    write_record(appointment_for(world, student, days=-90), student)
    assert table(demographics.build(world.center, period(world)), "standing").rows == [
        ["First-year", 5, 5]
    ]


def test_the_report_names_no_student(world) -> None:
    choose_questions(world)
    students = [session(world, "First-year", "ENG 101", day=n + 1) for n in range(5)]
    report = demographics.build(world.center, period(world))
    text = repr([t.rows for t in report.tables])
    assert not any(s.display_name in text for s in students)


def test_concerns_are_shown_across_the_centers_terms(world) -> None:
    choose_questions(world)
    last_fall = Term.objects.create(
        center=world.center,
        name="Last fall",
        starts_on=timezone.now().date() - timedelta(days=200),
        ends_on=timezone.now().date() - timedelta(days=100),
    )
    for n in range(5):
        student = make_member(world.center, Role.STUDENT)
        answer(world.registration, student, {STANDING: "First-year"})
        write_record(appointment_for(world, student, days=-(120 + n)), student)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    across = table(demographics.build(world.center, period(world)), "across_terms")
    terms = {r[0] for r in across.rows}
    assert terms == {last_fall.name, world.term.name}
    only = demographics.build(world.center, period(world), standing="Junior")
    assert table(only, "across_terms").rows == []


# --- Choosing the questions ---------------------------------------------------------------------


def questions_url(world) -> str:
    return reverse("reports:report_questions", kwargs={"slug": world.center.slug})


def report_url(world) -> str:
    return reverse("reports:by_course", kwargs={"slug": world.center.slug})


def test_only_short_answer_and_choose_one_questions_are_offered(world) -> None:
    labels = [label for _key, label in demographics.candidates(world.center)]
    assert any(STANDING in label for label in labels) and any(COURSE in label for label in labels)
    assert not any("Tell us about yourself" in label or "Pages" in label for label in labels)


def test_a_director_chooses_the_questions(client, world) -> None:
    key = world.registration.published().questions.get(label=STANDING).key
    course = world.intake.published().questions.get(label=COURSE).key
    client.force_login(make_member(world.center, Role.DIRECTOR))
    page = client.get(questions_url(world)).content.decode()
    assert STANDING in page and "None chosen" in page
    client.post(questions_url(world), {"standing": str(key), "course": str(course)})
    saved = demographics.settings_for(world.center)
    assert (saved.standing_key, saved.course_key) == (key, course)
    assert AuditEvent.objects.filter(action=AuditAction.REPORT_QUESTIONS_CHANGED).exists()
    client.post(questions_url(world), {"standing": "", "course": ""})
    saved.refresh_from_db()
    assert saved.standing_key is None


def test_a_question_that_isnt_on_offer_cant_be_chosen(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    response = client.post(
        questions_url(world), {"standing": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 200
    assert demographics.settings_for(world.center).standing_key is None


def test_the_choice_follows_the_question_into_a_new_version(world) -> None:
    from sympraxi.forms import builder

    choose_questions(world)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    draft = builder.start_draft(actor=make_member(world.center, Role.DIRECTOR), form=world.intake)
    builder.publish(actor=make_member(world.center, Role.DIRECTOR), draft=draft)
    assert world.intake.published().number == 2
    assert table(demographics.build(world.center, period(world)), "course").rows == [
        ["ENG 101", 5, 5]
    ]


# --- The pages ------------------------------------------------------------


def test_the_page_shows_the_tables_and_downloads(client, world) -> None:
    choose_questions(world)
    for n in range(5):
        session(world, "First-year", "ENG 101", day=n + 1)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    page = client.get(report_url(world)).content.decode()
    assert "Sessions by class standing" in page and "First-year" in page
    assert 'name="standing"' in page and "Concerns across terms" in page
    narrowed = client.get(report_url(world), {"standing": "Junior"}).content.decode()
    assert "Junior" in narrowed
    download = client.get(report_url(world), {"format": "csv", "standing": "First-year"})
    assert download.status_code == 200 and "text/csv" in download["Content-Type"]


def test_only_directors_open_them(client, world) -> None:
    for role in (Role.TUTOR, Role.FRONT_DESK, Role.STUDENT):
        client.force_login(make_member(world.center, role))
        assert client.get(report_url(world)).status_code == 403
        assert client.get(questions_url(world)).status_code == 403
