"""Availability and booking across both daylight-saving transitions (America/New_York).

Fall back: Sunday, November 1, 2026 (1:00 to 1:59 happen twice).
Spring forward: Sunday, March 14, 2027 (2:00 to 2:59 never happen).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from sympraxi.centers.models import Role
from sympraxi.scheduling import services
from sympraxi.scheduling.availability import open_times
from sympraxi.scheduling.models import Modality
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

NY = ZoneInfo("America/New_York")
FALL_BACK = date(2026, 11, 1)
SPRING_FORWARD = date(2027, 3, 14)
SWITCHES = pytest.mark.parametrize("switch", [FALL_BACK, SPRING_FORWARD], ids=["fall", "spring"])

pytestmark = pytest.mark.django_db


def local_times(times: list) -> list[str]:
    return [t.starts_at.astimezone(NY).strftime("%H:%M %Z") for t in times]


@pytest.fixture
def center():
    return CenterFactory(time_zone="America/New_York")


@SWITCHES
def test_weekly_hours_keep_their_wall_clock_time(center, switch: date) -> None:
    schedule = make_schedule(center, switch - timedelta(days=7), switch + timedelta(days=7))
    assign(schedule, hours=((time(10), time(12)),))
    week_before = open_times(schedule, switch - timedelta(days=7), 30)
    on_the_day = open_times(schedule, switch, 30)
    week_after = open_times(schedule, switch + timedelta(days=7), 30)
    for times in (week_before, on_the_day, week_after):
        assert [t.starts_at.astimezone(NY).time() for t in times] == [
            time(10),
            time(10, 30),
            time(11),
            time(11, 30),
        ]
    # The same wall-clock time is a different UTC hour on each side of the switch.
    utc_before = week_before[0].starts_at.astimezone(UTC).hour
    utc_after = week_after[0].starts_at.astimezone(UTC).hour
    assert {utc_before, utc_after} == {14, 15}
    assert utc_before != utc_after


def test_the_repeated_hour_is_bookable_twice(center) -> None:
    schedule = make_schedule(center, FALL_BACK, FALL_BACK, opens_at=time(0))
    assign(schedule, hours=((time(0), time(4)),))
    times = open_times(schedule, FALL_BACK, 60)
    assert local_times(times) == [
        "00:00 EDT",
        "00:30 EDT",
        "01:00 EDT",
        "01:30 EDT",
        "01:00 EST",
        "01:30 EST",
        "02:00 EST",
        "02:30 EST",
        "03:00 EST",
    ]
    # Every slot is a real hour long, and no two start at the same instant.
    assert all(t.ends_at - t.starts_at == timedelta(hours=1) for t in times)
    assert len({t.starts_at for t in times}) == len(times)


def test_the_skipped_hour_has_no_slots(center) -> None:
    schedule = make_schedule(center, SPRING_FORWARD, SPRING_FORWARD, opens_at=time(0))
    assign(schedule, hours=((time(1), time(4)),))
    times = open_times(schedule, SPRING_FORWARD, 60)
    # 1:00 to 4:00 on the wall clock is two real hours.
    assert local_times(times) == ["01:00 EST", "01:30 EST", "03:00 EDT"]
    assert all(t.ends_at - t.starts_at == timedelta(hours=1) for t in times)


def test_a_block_inside_the_skipped_hour_is_empty(center) -> None:
    schedule = make_schedule(center, SPRING_FORWARD, SPRING_FORWARD, opens_at=time(0))
    assign(schedule, hours=((time(2), time(2, 30)),))
    assert open_times(schedule, SPRING_FORWARD, 30) == []


def test_both_occurrences_of_the_repeated_hour_can_be_booked_but_not_overlapped(center) -> None:
    schedule = make_schedule(center, FALL_BACK, FALL_BACK, opens_at=time(0))
    assignment = assign(schedule, hours=((time(0), time(4)),))
    first, second, third = (make_member(center, Role.STUDENT) for _ in range(3))
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    edt = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 1:30 EDT
    est = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 1:30 EST
    for student, start in ((first, edt), (second, est)):
        services.book(
            actor=student,
            assignment=assignment,
            starts_at=start,
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[student],
            as_staff=False,
            now=now,
        )
    with pytest.raises(services.BookingError) as exc:
        services.book(
            actor=third,
            assignment=assignment,
            starts_at=datetime(2026, 11, 1, 6, 0, tzinfo=UTC),  # 1:00 EST, an hour long
            minutes=60,
            modality=Modality.IN_PERSON,
            students=[third],
            as_staff=False,
            now=now,
        )
    assert exc.value.code == "slot_taken"


@pytest.mark.parametrize(
    ("first_monday", "utc_hours"),
    [(date(2026, 10, 26), [20, 21, 21]), (date(2027, 3, 8), [21, 20, 20])],
    ids=["fall", "spring"],
)
def test_a_weekly_series_stays_at_the_same_local_time(center, first_monday, utc_hours) -> None:
    schedule = make_schedule(center, first_monday, first_monday + timedelta(days=21))
    assignment = assign(schedule, hours=((time(9), time(17)),))
    student = make_member(center, Role.STUDENT)
    staff = make_member(center, Role.FRONT_DESK)
    booked = services.book_series(
        weeks=3,
        actor=staff,
        assignment=assignment,
        starts_at=at(schedule, first_monday, 16),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[student],
        as_staff=True,
        notify=False,
    )
    assert [a.starts_at.astimezone(NY).time() for a in booked] == [time(16)] * 3
    assert [a.starts_at.astimezone(UTC).hour for a in booked] == utc_hours
    assert len({a.series_id for a in booked}) == 1 and booked[0].series_id is not None


def test_notice_is_measured_in_real_time_across_the_switch(center) -> None:
    # At 1:30 EDT on the fall-back night, 9:00 EST is 8.5 real hours away but only
    # 7.5 hours away on the wall clock. Eight hours' notice is enough.
    schedule = make_schedule(center, FALL_BACK, FALL_BACK, min_notice_minutes=8 * 60)
    assignment = assign(schedule, hours=((time(9), time(12)),))
    student = make_member(center, Role.STUDENT)
    now = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    appointment = services.book(
        actor=student,
        assignment=assignment,
        starts_at=at(schedule, FALL_BACK, 9),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[student],
        as_staff=False,
        now=now,
        notify=False,
    )
    assert appointment.starts_at - now == timedelta(hours=8, minutes=30)


@SWITCHES
def test_daily_limits_count_the_whole_local_day(center, switch: date) -> None:
    # Appointments at 0:00 and 23:00 fall in the same 23- or 25-hour local day.
    schedule = make_schedule(center, switch, switch, opens_at=time(0), max_per_day=2)
    assignment = assign(schedule, hours=((time(0), time(23, 30)),))
    student = make_member(center, Role.STUDENT)
    now = at(schedule, switch - timedelta(days=1), 12)

    def book(hour: int) -> None:
        services.book(
            actor=student,
            assignment=assignment,
            starts_at=at(schedule, switch, hour),
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[student],
            as_staff=False,
            now=now,
            notify=False,
        )

    book(0)
    book(23)
    with pytest.raises(services.BookingError) as exc:
        book(12)
    assert exc.value.code == "limit_day"
