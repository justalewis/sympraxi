"""Wall-clock times and instants, including both daylight-saving transitions.

In America/New_York, clocks go back on November 1, 2026 (1:00 to 1:59 happen
twice) and forward on March 14, 2027 (2:00 to 2:59 never happen).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from sympraxi.scheduling.timeutils import (
    clip,
    covers,
    date_range_bounds,
    day_bounds,
    first_instant,
    has_transition,
    merge,
    subtract,
    week_bounds,
    week_start,
)

NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
FALL_BACK = date(2026, 11, 1)
SPRING_FORWARD = date(2027, 3, 14)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_ordinary_time() -> None:
    assert first_instant(date(2026, 10, 15), time(10), NY) == utc(2026, 10, 15, 14)
    assert first_instant(date(2027, 1, 15), time(10), NY) == utc(2027, 1, 15, 15)


def test_a_repeated_time_means_its_first_occurrence() -> None:
    # 1:30 happens at 5:30 UTC (EDT) and again at 6:30 UTC (EST).
    assert first_instant(FALL_BACK, time(1, 30), NY) == utc(2026, 11, 1, 5, 30)


def test_a_skipped_time_means_the_moment_the_clock_jumps() -> None:
    # 2:30 never happens; the clock reads 3:00 EDT at 7:00 UTC.
    assert first_instant(SPRING_FORWARD, time(2, 30), NY) == utc(2027, 3, 14, 7)
    assert first_instant(SPRING_FORWARD, time(2, 0), NY) == utc(2027, 3, 14, 7)
    assert first_instant(SPRING_FORWARD, time(3, 0), NY) == utc(2027, 3, 14, 7)


def test_times_either_side_of_the_switch() -> None:
    assert first_instant(SPRING_FORWARD, time(1, 59), NY) == utc(2027, 3, 14, 6, 59)
    assert first_instant(FALL_BACK, time(2, 0), NY) == utc(2026, 11, 1, 7)


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 10, 15), 24), (FALL_BACK, 25), (SPRING_FORWARD, 23)],
)
def test_day_lengths(day: date, hours: int) -> None:
    start, end = day_bounds(day, NY)
    assert end - start == timedelta(hours=hours)


def test_has_transition_only_on_switch_days() -> None:
    assert has_transition(FALL_BACK, NY)
    assert has_transition(SPRING_FORWARD, NY)
    assert not has_transition(date(2026, 10, 31), NY)
    assert not has_transition(date(2027, 3, 15), NY)
    # London switches on different days.
    assert not has_transition(FALL_BACK, LONDON)
    assert has_transition(date(2026, 10, 25), LONDON)


def test_weeks_run_monday_to_sunday_and_can_be_an_hour_long_or_short() -> None:
    assert week_start(FALL_BACK) == date(2026, 10, 26)
    start, end = week_bounds(FALL_BACK, NY)
    assert end - start == timedelta(days=7, hours=1)
    start, end = week_bounds(SPRING_FORWARD, NY)
    assert end - start == timedelta(days=7, hours=-1)


def test_date_range_bounds_cover_whole_days() -> None:
    start, end = date_range_bounds(date(2026, 10, 30), FALL_BACK, NY)
    assert start == utc(2026, 10, 30, 4)
    assert end == utc(2026, 11, 2, 5)


def h(hour: int, minute: int = 0) -> datetime:
    return utc(2027, 1, 4, hour, minute)


def test_merge_joins_overlapping_and_touching() -> None:
    assert merge([(h(9), h(10)), (h(10), h(11)), (h(13), h(14)), (h(9, 30), h(9, 45))]) == [
        (h(9), h(11)),
        (h(13), h(14)),
    ]
    assert merge([(h(9), h(9))]) == []


def test_subtract_cuts_holes() -> None:
    assert subtract([(h(9), h(17))], [(h(12), h(13)), (h(16), h(18))]) == [
        (h(9), h(12)),
        (h(13), h(16)),
    ]
    assert subtract([(h(9), h(10))], [(h(8), h(11))]) == []


def test_clip_and_covers() -> None:
    assert clip([(h(7), h(10)), (h(16), h(20))], (h(8), h(18))) == [
        (h(8), h(10)),
        (h(16), h(18)),
    ]
    assert covers([(h(9), h(10)), (h(10), h(11))], h(9, 30), h(10, 30))
    assert not covers([(h(9), h(10))], h(9, 30), h(10, 30))
