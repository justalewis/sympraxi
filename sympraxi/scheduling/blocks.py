"""Booking blocks: pausing a student's online booking after too many no-shows or
late cancellations.

A center sets limits that count every schedule, and a schedule can set its own
that count only that schedule. Each limit counts confirmed no-shows, or late
cancellations, that haven't been waived: within the term of the appointment
being counted, or from a date the director chose. Counting is by the
appointment's start, like the booking limits.

When a no-show is confirmed or a late cancellation recorded, `evaluate()` runs
in the same transaction, after the student's membership is locked, so two at
once can't both set a block. A block reaching its limit ends after the last day
of that term, unless a director lifts it sooner. Lifting doesn't reset the count:
the next no-show or late cancellation past the limit sets a new block. To take
one out of the count, staff waive it.

A block stops only the student booking for themselves. Staff can still book for
them, and their existing appointments stay; they can still cancel or move those.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import (
    Appointment,
    Attendance,
    Block,
    BlockReason,
    Participant,
    Schedule,
    Term,
)
from sympraxi.scheduling.timeutils import first_instant, local_date


def lock_students(center: Center, student_ids: Iterable[Any]) -> None:
    """Lock the students' memberships in the center, in a fixed order.

    The first lock in `services`' order. Inactive memberships are locked too: a
    student whose membership ended can still have a no-show confirmed.
    """
    list(
        Membership.objects.select_for_update(of=("self",))
        .filter(center=center, role=Role.STUDENT, user__in=list(student_ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def counted(
    student: User,
    *,
    center: Center,
    reason: str,
    schedule: Schedule | None = None,
    term: Term | None = None,
    count_from: date | None = None,
) -> QuerySet[Participant]:
    """The student's unwaived no-shows or late cancellations that count toward a limit.

    `schedule` narrows the count to one schedule. Pass `count_from` to count from
    a date, or else `term` to count within a term.
    """
    rows = Participant.objects.filter(
        student=student, waived_at__isnull=True, appointment__schedule__center=center
    )
    if reason == BlockReason.NO_SHOWS:
        rows = rows.filter(attendance=Attendance.NO_SHOW, canceled_at__isnull=True)
    else:
        rows = rows.filter(late_cancel=True)
    if schedule is not None:
        rows = rows.filter(appointment__schedule=schedule)
    if count_from is not None:
        start = first_instant(count_from, time(0), ZoneInfo(center.time_zone))
        rows = rows.filter(appointment__starts_at__gte=start)
    elif term is not None:
        rows = rows.filter(appointment__schedule__term=term)
    return rows


def _limit(owner: Center | Schedule, reason: str) -> int | None:
    if reason == BlockReason.NO_SHOWS:
        return owner.no_show_block_limit
    return owner.late_cancel_block_limit


@dataclass
class Standing:
    """Where a student stands against the limits that apply to one appointment.

    `count` and `limit` come from the center's limit if it has one, else the
    schedule's. `blocks` are the blocks in effect for this reason that cover the
    appointment's schedule. `since` is the date counting started, or None for
    "this term".
    """

    reason: str
    count: int
    limit: int | None
    since: date | None
    blocks: list[Block]

    @property
    def blocked(self) -> bool:
        return bool(self.blocks)

    @property
    def first_free_day(self) -> date | None:
        return max(b.first_free_day for b in self.blocks) if self.blocks else None


def standing(
    student: User, appointment: Appointment, reason: str, *, now: datetime | None = None
) -> Standing:
    schedule = appointment.schedule
    center = schedule.center
    owner: Center | Schedule = center
    if _limit(center, reason) is None and _limit(schedule, reason) is not None:
        owner = schedule
    since = owner.block_count_from
    rows = counted(
        student,
        center=center,
        reason=reason,
        schedule=schedule if owner is schedule else None,
        term=schedule.term,
        count_from=since,
    )
    blocks = [b for b in in_effect(student, schedule, now=now) if b.reason == reason]
    return Standing(
        reason=reason,
        count=rows.count(),
        limit=_limit(owner, reason),
        since=since,
        blocks=blocks,
    )


def evaluate(
    *,
    student: User,
    appointment: Appointment,
    reason: str,
    actor: User | None,
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> list[Block]:
    """Set a block for each limit this no-show or late cancellation reaches.

    Call inside the transaction that recorded it, with the student's membership
    already locked.
    """
    now = now or timezone.now()
    schedule = appointment.schedule
    center = schedule.center
    term = schedule.term
    today = local_date(now, schedule.tz)
    if term.ends_on < today:
        return []  # A block for a term that's over would already have ended.
    created = []
    for owner, scope in ((center, None), (schedule, schedule)):
        limit = _limit(owner, reason)
        if limit is None:
            continue
        existing = Block.objects.filter(
            student=student,
            center=center,
            schedule=scope,
            reason=reason,
            lifted_at__isnull=True,
            ends_on__gte=today,
        )
        if existing.exists():
            continue
        count = counted(
            student,
            center=center,
            reason=reason,
            schedule=scope,
            term=term,
            count_from=owner.block_count_from,
        ).count()
        if count < limit:
            continue
        block = Block.objects.create(
            center=center,
            schedule=scope,
            student=student,
            reason=reason,
            count=count,
            limit=limit,
            term=term if owner.block_count_from is None else None,
            counted_from=owner.block_count_from,
            ends_on=term.ends_on,
        )
        record(
            AuditAction.BLOCK_SET,
            request=request,
            actor=actor,
            actor_label="" if actor else "scheduler",
            center=center,
            target=block,
            student=str(student.pk),
            reason=reason,
            count=count,
            limit=limit,
            schedule=str(scope.pk) if scope else None,
            ends_on=block.ends_on.isoformat(),
            appointment=str(appointment.pk),
        )
        created.append(block)
    return created


def in_effect(student: User, schedule: Schedule, *, now: datetime | None = None) -> list[Block]:
    """Blocks that stop the student booking on this schedule today."""
    today = local_date(now or timezone.now(), schedule.tz)
    return list(
        Block.objects.filter(
            student=student,
            center=schedule.center,
            lifted_at__isnull=True,
            ends_on__gte=today,
        )
        .filter(Q(schedule__isnull=True) | Q(schedule=schedule))
        .select_related("schedule", "center")
        .order_by("ends_on")
    )


def in_center(student: User, center: Center, *, now: datetime | None = None) -> list[Block]:
    """Every block on the student in this center today, center-wide or for one schedule."""
    today = local_date(now or timezone.now(), ZoneInfo(center.time_zone))
    return list(
        Block.objects.filter(
            student=student, center=center, lifted_at__isnull=True, ends_on__gte=today
        )
        .select_related("schedule", "center")
        .order_by("ends_on")
    )


def what_counted(block: Block) -> QuerySet[Participant]:
    """The no-shows or late cancellations that count toward the block's limit now."""
    return (
        counted(
            block.student,
            center=block.center,
            reason=block.reason,
            schedule=block.schedule,
            term=block.term,
            count_from=block.counted_from,
        )
        .select_related("appointment__schedule", "appointment__tutor")
        .order_by("appointment__starts_at")
    )


def lift(
    *,
    actor: User,
    block: Block,
    note: str = "",
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> Block:
    """End a block early. The note stays on the block; the audit log records who and when."""
    now = now or timezone.now()
    with transaction.atomic():
        current = Block.objects.select_for_update().select_related("center").get(pk=block.pk)
        if current.lifted_at is not None:
            return current
        current.lifted_at = now
        current.lifted_by = actor
        current.lift_note = note.strip()
        current.save(update_fields=["lifted_at", "lifted_by", "lift_note"])
        record(
            AuditAction.BLOCK_LIFTED,
            request=request,
            actor=actor,
            center=current.center,
            target=current,
            student=str(current.student_id),
            reason=current.reason,
        )
    return current
