"""Hours offered, booked and attended, against hand counts.

Two weeks at a center in New York, around the night clocks went back
(Sunday, November 2, 2025):

- Ana works Mondays and Wednesdays 10:00 to 12:00 and Sundays 0:00 to 4:00. The
  Sunday the clocks go back is five real hours long from 0:00 to 4:00.
- Extra hours Friday, October 31, 13:00 to 15:00. Time off Wednesday, November 5,
  10:00 to 11:00. The center is closed Monday, November 3.

Offered: the week of October 27 is 2 + 2 + 2 + 5 = 11 hours; the week of
November 3 is 0 + 1 + 4 = 5 hours.

Ana's appointments: Monday 10:00 for an hour (came); Wednesday 10:00, 30 minutes
(no-show); Wednesday 11:00 (canceled, not counted); Sunday 23:00 local, 30
minutes (came), which is Monday in UTC but belongs to the first week; and in the
second week a placeholder (not counted) and a group of two at 11:30 for 30
minutes with one student there (counted once).

Ben is on the schedule but not taking appointments, so he offers nothing, and
has one hour booked and attended on October 27.
"""

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.reports import utilization
from sympraxi.reports.forms import Period
from sympraxi.scheduling.models import AvailabilityException, Closure, ExceptionKind, Modality
from tests.factories import CenterFactory
from tests.reports_world import book, came, cancel, no_show
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db

FIRST, LAST = date(2025, 10, 27), date(2025, 11, 9)


@pytest.fixture
def setup():
    center = CenterFactory(time_zone="America/New_York")
    schedule = make_schedule(center, FIRST, LAST, name="Drop-in")
    Closure.objects.create(
        term=schedule.term,
        label="Fall break",
        starts_on=date(2025, 11, 3),
        ends_on=date(2025, 11, 3),
    )
    ana = make_member(center, Role.TUTOR, full_name="Ana Tutor")
    ben = make_member(center, Role.TUTOR, full_name="Ben Tutor")
    a = assign(schedule, ana, weekdays=[0, 2], hours=((time(10), time(12)),))
    a.weekly_hours.create(weekday=6, starts_at=time(0), ends_at=time(4))
    AvailabilityException.objects.create(
        assignment=a,
        kind=ExceptionKind.EXTRA,
        starts_at=at(schedule, date(2025, 10, 31), 13),
        ends_at=at(schedule, date(2025, 10, 31), 15),
    )
    AvailabilityException.objects.create(
        assignment=a,
        kind=ExceptionKind.BLACKOUT,
        starts_at=at(schedule, date(2025, 11, 5), 10),
        ends_at=at(schedule, date(2025, 11, 5), 11),
    )
    b = assign(schedule, ben, weekdays=[0], is_active=False)
    students = [make_member(center, Role.STUDENT) for _ in range(5)]

    def on(day: int, hour: int, minute: int = 0):
        return at(schedule, FIRST + timedelta(days=day), hour, minute)

    came(book(schedule, a, on(0, 10), [students[0]], minutes=60), students[0])
    no_show(book(schedule, a, on(2, 10), [students[1]]), students[1])
    cancel(book(schedule, a, on(2, 11), [students[2]]))
    came(book(schedule, a, on(6, 23), [students[2]]), students[2])
    book(schedule, a, on(9, 11), [], is_placeholder=True)
    group = book(schedule, a, on(9, 11, 30), [students[3], students[4]], capacity=4)
    came(group, students[3])
    came(book(schedule, b, on(0, 14), [students[0]], minutes=60), students[0])

    other = make_schedule(center, FIRST, LAST, name="Online")
    assign(other, ana, weekdays=[1], offers_in_person=False, offers_online=True)
    book(other, other.assignments.get(), on(1, 10), [students[1]], modality=Modality.ONLINE)
    return center, schedule, other


def by_name(report):
    return {t.name: t for t in report.tables}


def test_hours_by_tutor(setup) -> None:
    center, schedule, _other = setup
    report = utilization.build(center, Period(FIRST, LAST, None, schedule))
    rows = {row[0]: row[2:] for row in by_name(report)["by_tutor"].rows}
    assert rows == {
        "Ana Tutor": [Decimal("16.0"), Decimal("2.5"), Decimal("2.0"), 16],
        "Ben Tutor": [Decimal("0.0"), Decimal("1.0"), Decimal("1.0"), None],
    }


def test_hours_by_week_follow_the_local_calendar(setup) -> None:
    center, schedule, _other = setup
    report = utilization.build(center, Period(FIRST, LAST, None, schedule))
    rows = {row[0]: row[1:] for row in by_name(report)["by_week"].rows}
    assert rows == {
        date(2025, 10, 27): [Decimal("11.0"), Decimal("3.0"), Decimal("2.5"), 27],
        date(2025, 11, 3): [Decimal("5.0"), Decimal("0.5"), Decimal("0.5"), 10],
    }


def test_hours_by_schedule_and_every_schedule(setup) -> None:
    center, _schedule, _other = setup
    report = utilization.build(center, Period(FIRST, LAST, None, None))
    rows = {row[0]: row[1:] for row in by_name(report)["by_schedule"].rows}
    # The online schedule: two Tuesdays at 9:00 to 17:00, one booking that no one marked.
    assert rows == {
        "Drop-in": [Decimal("16.0"), Decimal("3.5"), Decimal("3.0"), 22],
        "Online": [Decimal("16.0"), Decimal("0.5"), Decimal("0.0"), 3],
        "Every schedule shown": [Decimal("32.0"), Decimal("4.0"), Decimal("3.0"), 13],
    }


def test_each_tutor_week_by_week(setup) -> None:
    center, schedule, _other = setup
    report = utilization.build(center, Period(FIRST, LAST, None, schedule))
    rows = by_name(report)["by_tutor_and_week"].rows
    assert rows == [
        [
            "Ana Tutor",
            "Drop-in",
            date(2025, 10, 27),
            Decimal("11.0"),
            Decimal("2.0"),
            Decimal("1.5"),
            18,
        ],
        [
            "Ana Tutor",
            "Drop-in",
            date(2025, 11, 3),
            Decimal("5.0"),
            Decimal("0.5"),
            Decimal("0.5"),
            10,
        ],
        [
            "Ben Tutor",
            "Drop-in",
            date(2025, 10, 27),
            Decimal("0.0"),
            Decimal("1.0"),
            Decimal("1.0"),
            None,
        ],
    ]


def test_part_of_a_week(setup) -> None:
    """Wednesday to Tuesday: the Wednesday, Friday and Sunday, then the closed Monday."""
    center, schedule, _other = setup
    report = utilization.build(
        center, Period(date(2025, 10, 29), date(2025, 11, 4), None, schedule)
    )
    rows = {row[0]: row[1:4] for row in by_name(report)["by_week"].rows}
    assert rows == {
        date(2025, 10, 27): [Decimal("9.0"), Decimal("1.0"), Decimal("0.5")],
        date(2025, 11, 3): [Decimal("0.0"), Decimal("0.0"), Decimal("0.0")],
    }


def test_queries_do_not_grow_with_appointments(setup, django_assert_max_num_queries) -> None:
    center, _schedule, _other = setup
    with django_assert_max_num_queries(10):
        utilization.build(center, Period(FIRST, LAST, None, None))


def test_the_page_and_its_download(client, setup) -> None:
    center, schedule, _other = setup
    client.force_login(make_member(center, Role.DIRECTOR))
    url = reverse("reports:utilization", kwargs={"slug": center.slug})
    query = {"first": FIRST.isoformat(), "last": LAST.isoformat(), "schedule": str(schedule.pk)}
    page = client.get(url, query)
    assert page.status_code == 200
    assert "Ana Tutor" in page.content.decode()
    csv_file = client.get(url, {**query, "format": "csv"})
    assert csv_file["Content-Type"] == "text/csv; charset=utf-8"
    assert csv_file["Content-Disposition"] == (
        'attachment; filename="utilization-2025-10-27-to-2025-11-09.csv"'
    )
    assert "no-store" in csv_file["Cache-Control"]
    text = csv_file.content.decode("utf-8-sig")
    assert "Ana Tutor,Drop-in,16.0,2.5,2.0,16" in text.replace("\r\n", "\n")
