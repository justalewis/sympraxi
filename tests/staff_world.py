"""Builders for time clock tests."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.scheduling.timeutils import first_instant, local_date
from sympraxi.staff.models import Category, TimeEntry
from tests.scheduling_world import World


def at(center: Center, day: date, hour: int, minute: int = 0) -> datetime:
    return first_instant(day, time(hour, minute), ZoneInfo(center.time_zone))


def today(center: Center) -> date:
    return local_date(timezone.now(), ZoneInfo(center.time_zone))


def entry(
    center: Center,
    person: User,
    day: date,
    start: tuple[int, int],
    end: tuple[int, int] | None,
    category: Category = Category.TUTORING,
    **fields: object,
) -> TimeEntry:
    started = at(center, day, *start)
    ended = at(center, day, *end) if end is not None else None
    return TimeEntry.objects.create(
        center=center,
        person=person,
        category=category,
        started_at=started,
        ended_at=ended,
        **fields,
    )


def add_time_clock(world: World) -> World:
    """Give a world `time_entry`: two hours the tutor worked yesterday."""
    yesterday = today(world.center) - timedelta(days=1)
    world.time_entry = entry(world.center, world.tutor, yesterday, (9, 0), (11, 0))
    return world
