"""Wall-clock times in a center's time zone, and the instants they stand for.

Appointments are stored in UTC. Weekly availability and opening hours are stored
as wall-clock times in the center's zone ("Mondays 10:00 to 12:00") and turned
into instants here, one date at a time, so 10:00 stays 10:00 across daylight-saving
changes.

Two wall-clock times need a rule:

- On the night clocks go back, 1:30 happens twice. It means the first time.
- On the night clocks go forward, 2:30 never happens. It means the moment the
  clock jumps (3:00), so a block from 2:00 to 4:00 is one hour long.

Both follow from one definition: a wall-clock time stands for the first instant
at which the local clock reads that time or later.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def first_instant(day: date, wall: time, tz: ZoneInfo) -> datetime:
    """The first instant (in UTC) at which the clock in `tz` reads `day` `wall` or later."""
    naive = datetime.combine(day, wall)
    early = naive.replace(tzinfo=tz, fold=0)
    late = naive.replace(tzinfo=tz, fold=1)
    if early.utcoffset() == late.utcoffset():
        return early.astimezone(UTC)
    candidates = sorted([early.astimezone(UTC), late.astimezone(UTC)])
    if _wall(candidates[0], tz) == naive:
        # The time happens twice; take the first.
        return candidates[0]
    # The time falls in a gap. The clock reads earlier than `naive` at the lower
    # candidate and later at the upper one; find the jump between them.
    lo, hi = candidates
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        if _wall(mid, tz) >= naive:
            hi = mid
        else:
            lo = mid
    return hi.replace(microsecond=0)


def _wall(instant: datetime, tz: ZoneInfo) -> datetime:
    return instant.astimezone(tz).replace(tzinfo=None, fold=0)


def local_date(instant: datetime, tz: ZoneInfo) -> date:
    return instant.astimezone(tz).date()


def day_bounds(day: date, tz: ZoneInfo) -> Interval:
    """The instants from local midnight on `day` to local midnight the next day."""
    return first_instant(day, time(0), tz), first_instant(day + timedelta(days=1), time(0), tz)


def week_start(day: date) -> date:
    """Weeks run Monday to Sunday."""
    return day - timedelta(days=day.weekday())


def week_bounds(day: date, tz: ZoneInfo) -> Interval:
    start = week_start(day)
    return first_instant(start, time(0), tz), first_instant(start + timedelta(days=7), time(0), tz)


def date_range_bounds(first: date, last: date, tz: ZoneInfo) -> Interval:
    """From local midnight on `first` to local midnight after `last`."""
    return first_instant(first, time(0), tz), first_instant(last + timedelta(days=1), time(0), tz)


def has_transition(day: date, tz: ZoneInfo) -> bool:
    """True if the UTC offset changes during the local day (a daylight-saving switch)."""
    start, end = day_bounds(day, tz)
    return (
        start.astimezone(tz).utcoffset() != (end - timedelta(seconds=1)).astimezone(tz).utcoffset()
    )


# --- Interval arithmetic on [start, end) pairs ---------------------------------


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sort and join overlapping or touching intervals. Empty intervals are dropped."""
    result: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def subtract(intervals: Iterable[Interval], holes: Iterable[Interval]) -> list[Interval]:
    """`intervals` with every part covered by `holes` removed."""
    result = merge(intervals)
    for hole_start, hole_end in merge(holes):
        pieces: list[Interval] = []
        for start, end in result:
            if hole_end <= start or hole_start >= end:
                pieces.append((start, end))
                continue
            if start < hole_start:
                pieces.append((start, hole_start))
            if hole_end < end:
                pieces.append((hole_end, end))
        result = pieces
    return result


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    lo, hi = window
    return merge((max(start, lo), min(end, hi)) for start, end in intervals)


def covers(intervals: Iterable[Interval], start: datetime, end: datetime) -> bool:
    """True if one of the (merged) intervals contains all of [start, end)."""
    return any(s <= start and end <= e for s, e in merge(intervals))


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]
