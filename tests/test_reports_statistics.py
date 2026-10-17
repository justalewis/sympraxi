"""Statistics: counts checked against the hand counts in tests/reports_world.py."""

import html
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.models import AuditEvent
from sympraxi.records.models import SessionRecord
from sympraxi.reports import statistics
from sympraxi.reports.forms import Period, PeriodForm
from sympraxi.scheduling.models import Appointment, Participant
from tests.reports_world import FALL, book, came, counted_center
from tests.scheduling_world import at, make_member

pytestmark = pytest.mark.django_db

COLUMNS = ["booked", "came", "late_cancels", "no_shows", "canceled", "not_marked"]


def tables(report):
    return {t.name: t for t in report.tables}


def counts(table) -> dict[str, list[int]]:
    """Each row's label and its six counts."""
    return {row[0]: list(row[1:]) for row in table.rows}


@pytest.fixture
def world():
    return counted_center()


def fall(world, schedule=None) -> Period:
    return Period(FALL[0], FALL[1], world.term, schedule)


def test_every_visit_by_outcome(world) -> None:
    report = statistics.build(world.center, fall(world))
    assert counts(tables(report)["visits"]) == {"All visits": [9, 5, 1, 1, 1, 1]}


def test_visits_by_tutor_modality_and_kind(world) -> None:
    found = tables(statistics.build(world.center, fall(world)))
    assert counts(found["by_tutor"]) == {
        "Ana Tutor": [6, 5, 0, 0, 0, 1],
        "Ben Tutor": [3, 0, 1, 1, 1, 0],
    }
    assert counts(found["by_modality"]) == {
        "In person": [8, 5, 1, 1, 0, 1],
        "Online (meeting link)": [1, 0, 0, 0, 1, 0],
    }
    assert counts(found["by_kind"]) == {
        "Walk-in": [1, 1, 0, 0, 0, 0],
        "Group": [2, 1, 0, 0, 0, 1],
        "Weekly series": [1, 1, 0, 0, 0, 0],
        "Single booking": [5, 2, 1, 1, 1, 0],
    }
    assert counts(found["by_schedule"]) == {
        "In person": [8, 5, 1, 1, 0, 1],
        "Online": [1, 0, 0, 0, 1, 0],
    }


def test_appointments_students_and_first_visits(world) -> None:
    summary = dict(tables(statistics.build(world.center, fall(world)))["summary"].rows)
    assert summary == {
        "Appointments booked": 8,
        "Appointments canceled": 2,
        "Placeholders (time held without a student)": 1,
        "Students who booked": 4,
        "Students who came": 3,
        "First visits (a student's first visit to the center)": 2,
        "Return visits": 3,
    }


def test_a_first_visit_before_the_period_makes_every_visit_a_return(world) -> None:
    """s3 first came in September; in spring, s3's visit is a return."""
    spring = Period(date(2026, 1, 5), date(2026, 5, 1), None, None)
    summary = dict(tables(statistics.build(world.center, spring))["summary"].rows)
    assert summary["First visits (a student's first visit to the center)"] == 0
    assert summary["Return visits"] == 1


def test_topics_come_from_final_records_only(world) -> None:
    topics = tables(statistics.build(world.center, fall(world)))["topics"]
    assert {row[2]: row[3] for row in topics.rows} == {
        "argument:thesis": 2,
        "revising": 1,
        "citation:apa": 1,
    }
    assert topics.note == "3 final session records were held in this period."


def test_one_schedule(world) -> None:
    found = tables(statistics.build(world.center, fall(world, world.in_person)))
    assert counts(found["visits"]) == {"All visits": [8, 5, 1, 1, 0, 1]}
    assert "by_schedule" not in found
    # The record for a meeting that wasn't booked has no schedule.
    assert {row[2] for row in found["topics"].rows} == {"argument:thesis", "revising"}


def test_dates_outside_the_term_count_nothing(world) -> None:
    empty = Period(date(2025, 10, 1), date(2025, 10, 31), None, None)
    found = tables(statistics.build(world.center, empty))
    assert counts(found["visits"]) == {"All visits": [0, 0, 0, 0, 0, 0]}
    assert found["by_tutor"].rows == []


def test_other_centers_are_left_out(world) -> None:
    other = counted_center()
    report = statistics.build(world.center, fall(world))
    assert counts(tables(report)["visits"]) == {"All visits": [9, 5, 1, 1, 1, 1]}
    assert other.center != world.center


def test_no_appointment_is_loaded_into_python(world, monkeypatch, django_assert_max_num_queries):
    """Everything is counted in PostgreSQL, so a big term costs no more Python than a small one."""

    def refuse(cls, *args, **kwargs):
        raise AssertionError(f"{cls.__name__} rows were loaded into Python")

    for model in (Appointment, Participant, SessionRecord):
        monkeypatch.setattr(model, "from_db", classmethod(refuse))
    with django_assert_max_num_queries(12):
        statistics.build(world.center, fall(world))


def test_query_count_does_not_grow_with_appointments(world, django_assert_num_queries) -> None:
    period = fall(world)
    with CaptureQueriesContext(connection) as small:
        statistics.build(world.center, period)
    assignment = world.in_person.assignments.first()
    for hour in (13, 14, 15):
        student = make_member(world.center, Role.STUDENT)
        start = at(world.in_person, date(2025, 10, 6), hour)
        came(book(world.in_person, assignment, start, [student]), student)
    with django_assert_num_queries(len(small.captured_queries)):
        report = statistics.build(world.center, period)
    assert counts(tables(report)["visits"]) == {"All visits": [12, 8, 1, 1, 1, 1]}


# --- The page -----------------------------------------------------------------------


def director(world):
    return make_member(world.center, Role.DIRECTOR)


def test_the_page_shows_the_chosen_term(client, world) -> None:
    client.force_login(director(world))
    url = reverse("reports:statistics", kwargs={"slug": world.center.slug})
    response = client.get(url, {"term": str(world.term.pk)})
    assert response.status_code == 200
    page = response.content.decode()
    assert "Fall 2025 (September 1, 2025 to December 15, 2025)" in page
    assert "Ana Tutor" in page
    assert "Survey answers aren't here" in html.unescape(page)


def test_the_page_opens_on_the_current_term(world) -> None:
    form = PeriodForm(None, center=world.center, today=date(2025, 10, 1))
    assert form.period().term == world.term
    form = PeriodForm(None, center=world.center, today=date(2026, 2, 1))
    assert form.period().term.name == "Spring 2026"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"first": "2025-09-01"}, "Give both a first and a last day, or neither."),
        ({"first": "2025-09-10", "last": "2025-09-01"}, "The last day can't be before the first"),
        ({"first": "2024-01-01", "last": "2025-09-01"}, "Choose a year or less."),
    ],
)
def test_bad_dates_are_explained(client, world, data, error) -> None:
    client.force_login(director(world))
    url = reverse("reports:statistics", kwargs={"slug": world.center.slug})
    response = client.get(url, data)
    assert response.status_code == 200
    assert error in html.unescape(response.content.decode())
    assert response.context["report"] is None


def test_dates_win_over_the_term(world) -> None:
    form = PeriodForm(
        {"term": str(world.term.pk), "first": "2025-09-08", "last": "2025-09-09"},
        center=world.center,
        today=date(2025, 10, 1),
    )
    period = form.period()
    assert (period.first, period.last, period.term) == (date(2025, 9, 8), date(2025, 9, 9), None)


def test_statistics_are_not_audited(client, world) -> None:
    """They name tutors, never students."""
    client.force_login(director(world))
    before = AuditEvent.objects.count()
    url = reverse("reports:statistics", kwargs={"slug": world.center.slug})
    client.get(url, {"term": str(world.term.pk)})
    client.get(url, {"term": str(world.term.pk), "format": "csv"})
    assert AuditEvent.objects.count() == before
