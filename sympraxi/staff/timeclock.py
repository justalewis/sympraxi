"""Clocking in and out, directors' corrections, and the midnight clock-out.

Tutors, front desk staff and directors clock in and out for themselves. A
director adds an entry someone forgot, changes one, or removes one, always with
a reason; the audit log keeps the reason and what the entry was before. Nobody
changes their own entries after the fact, so a timesheet is something a
director has signed off on.

Anyone still clocked in at midnight, in the center's time zone, is clocked out
at midnight by the scheduler, and the entry says so. The person sees it on
their time clock page and asks a director to fix the time.

Timesheets count each entry on the day it started.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.timeutils import date_range_bounds, first_instant, local_date
from sympraxi.staff.models import Category, TimeEntry, to_hours


class TimeClockError(Exception):
    """Something the person can fix; the message says what."""


def _snapshot(entry: TimeEntry) -> dict[str, Any]:
    """What an entry says, for the audit log. The note's words stay out of it."""
    return {
        "category": entry.category,
        "started_at": entry.started_at.isoformat(),
        "ended_at": entry.ended_at.isoformat() if entry.ended_at else None,
        "has_note": bool(entry.note),
    }


def _overlap_message() -> str:
    return _("That overlaps another entry for the same person. Check their times and try again.")


def open_entry(center: Center, person: Any) -> TimeEntry | None:
    return TimeEntry.objects.filter(
        center=center, person=person, ended_at__isnull=True, removed_at__isnull=True
    ).first()


def clock_in(
    center: Center,
    person: User,
    category: str,
    note: str = "",
    *,
    request: HttpRequest,
    now: datetime | None = None,
) -> TimeEntry:
    now = (now or timezone.now()).replace(microsecond=0)
    try:
        with transaction.atomic():
            entry = TimeEntry.objects.create(
                center=center,
                person=person,
                category=Category(category),
                started_at=now,
                note=note.strip(),
            )
            record(
                AuditAction.TIME_CLOCKED_IN,
                request=request,
                center=center,
                target=entry,
                category=entry.category,
            )
    except IntegrityError as error:
        if open_entry(center, person) is not None:
            raise TimeClockError(_("You're already clocked in.")) from error
        raise TimeClockError(
            _("A director has already entered time for you that covers now.")
        ) from error
    return entry


def clock_out(
    center: Center, person: User, *, request: HttpRequest, now: datetime | None = None
) -> TimeEntry:
    now = (now or timezone.now()).replace(microsecond=0)
    with transaction.atomic():
        entry = (
            TimeEntry.objects.select_for_update()
            .filter(center=center, person=person, ended_at__isnull=True, removed_at__isnull=True)
            .first()
        )
        if entry is None:
            raise TimeClockError(_("You aren't clocked in."))
        # Clocking out in the same second still makes an entry with some length.
        entry.ended_at = max(now, entry.started_at + timedelta(seconds=1))
        entry.save(update_fields=["ended_at"])
        record(
            AuditAction.TIME_CLOCKED_OUT,
            request=request,
            center=center,
            target=entry,
            minutes=int((entry.ended_at - entry.started_at).total_seconds() // 60),
        )
    return entry


def add_entry(
    center: Center,
    person: User,
    *,
    category: str,
    started_at: datetime,
    ended_at: datetime,
    note: str,
    reason: str,
    request: HttpRequest,
) -> TimeEntry:
    try:
        with transaction.atomic():
            entry = TimeEntry.objects.create(
                center=center,
                person=person,
                category=Category(category),
                started_at=started_at,
                ended_at=ended_at,
                note=note.strip(),
                added_by=cast(User, request.user),
            )
            record(
                AuditAction.TIME_ENTRY_ADDED,
                request=request,
                center=center,
                target=entry,
                person=str(person.pk),
                entry=_snapshot(entry),
                reason=reason,
            )
    except IntegrityError as error:
        raise TimeClockError(_overlap_message()) from error
    return entry


def change_entry(
    entry: TimeEntry,
    *,
    category: str,
    started_at: datetime,
    ended_at: datetime | None,
    note: str,
    reason: str,
    request: HttpRequest,
) -> TimeEntry:
    try:
        with transaction.atomic():
            locked = TimeEntry.objects.select_for_update().get(pk=entry.pk)
            before = _snapshot(locked)
            locked.category = Category(category)
            locked.started_at = started_at
            locked.ended_at = ended_at
            locked.note = note.strip()
            # A director has looked at it, so it no longer needs fixing.
            locked.closed_automatically = False
            locked.changed_by = cast(User, request.user)
            locked.changed_at = timezone.now()
            locked.save()
            record(
                AuditAction.TIME_ENTRY_CHANGED,
                request=request,
                center=locked.center,
                target=locked,
                person=str(locked.person_id),
                before=before,
                after=_snapshot(locked),
                reason=reason,
            )
    except IntegrityError as error:
        raise TimeClockError(_overlap_message()) from error
    return locked


def remove_entry(entry: TimeEntry, *, reason: str, request: HttpRequest) -> None:
    with transaction.atomic():
        locked = TimeEntry.objects.select_for_update().get(pk=entry.pk)
        if locked.removed_at is not None:
            return
        locked.removed_at = timezone.now()
        locked.removed_by = cast(User, request.user)
        locked.save(update_fields=["removed_at", "removed_by"])
        record(
            AuditAction.TIME_ENTRY_REMOVED,
            request=request,
            center=locked.center,
            target=locked,
            person=str(locked.person_id),
            before=_snapshot(locked),
            reason=reason,
        )


def midnight_after(entry: TimeEntry) -> datetime:
    """Local midnight at the end of the day the entry started."""
    tz = ZoneInfo(entry.center.time_zone)
    return first_instant(local_date(entry.started_at, tz) + timedelta(days=1), time(0), tz)


def close_forgotten(now: datetime | None = None) -> int:
    """Clock out everyone still clocked in after midnight. Safe for two schedulers at once."""
    now = now or timezone.now()
    closed = 0
    candidates = TimeEntry.objects.filter(
        ended_at__isnull=True, removed_at__isnull=True, started_at__lt=now
    ).values_list("pk", flat=True)
    for pk in list(candidates):
        with transaction.atomic():
            entry = (
                TimeEntry.objects.select_for_update(skip_locked=True)
                .select_related("center")
                .filter(pk=pk, ended_at__isnull=True, removed_at__isnull=True)
                .first()
            )
            if entry is None:
                continue
            midnight = midnight_after(entry)
            if midnight > now:
                continue
            entry.ended_at = midnight
            entry.closed_automatically = True
            entry.save(update_fields=["ended_at", "closed_automatically"])
            record(
                AuditAction.TIME_CLOSED_AUTOMATICALLY,
                actor_label="scheduler",
                center=entry.center,
                target=entry,
                person=str(entry.person_id),
            )
            closed += 1
    return closed


# --- Totals -------------------------------------------------------------------------


def entries_started_between(center: Center, first: date, last: date) -> QuerySet[TimeEntry]:
    """Entries that started on these local days, removed ones left out."""
    start, end = date_range_bounds(first, last, ZoneInfo(center.time_zone))
    return (
        TimeEntry.objects.filter(
            center=center, removed_at__isnull=True, started_at__gte=start, started_at__lt=end
        )
        .select_related("person", "added_by", "changed_by")
        .order_by("person__full_name", "started_at")
    )


@dataclass
class PersonTotals:
    person: User
    by_category: dict[str, Decimal] = field(default_factory=dict)
    entries: int = 0
    open: int = 0

    @property
    def total(self) -> Decimal:
        return sum(self.by_category.values(), Decimal("0.00"))


def totals(entries: QuerySet[TimeEntry] | list[TimeEntry]) -> list[PersonTotals]:
    """Hours by person and category. Open entries are counted but add no hours."""
    found: dict[Any, PersonTotals] = {}
    sums: dict[Any, dict[str, timedelta]] = defaultdict(lambda: defaultdict(timedelta))
    for entry in entries:
        person = found.setdefault(entry.person_id, PersonTotals(person=entry.person))
        person.entries += 1
        if entry.ended_at is None:
            person.open += 1
        else:
            sums[entry.person_id][entry.category] += entry.ended_at - entry.started_at
    for person_id, by_category in sums.items():
        found[person_id].by_category = {k: to_hours(v) for k, v in by_category.items()}
    return sorted(found.values(), key=lambda p: (p.person.full_name.lower(), str(p.person.pk)))
