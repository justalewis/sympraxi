"""Booking, changing and canceling appointments. Every rule is checked here.

Views call these functions and show `BookingError.message` when one refuses.
Each function runs in one transaction and takes row locks in a fixed order, so
two requests at the same moment can't both slip past a rule:

1. the students' memberships in the center (their limits and their own calendar),
2. every assignment of the tutors involved, on any schedule, by primary key (the
   tutor's caps, group seats and calendar),
3. the appointment being joined or changed.

Locking all of a tutor's assignments makes two bookings for one tutor on two
schedules take turns, so the later one sees the earlier and is refused. The
database's exclusion constraint is still the last line against double-booking a
tutor; if it, a deadlock or a serialization failure stops a write anyway, the
person is told the time was just taken.

Students booking for themselves are held to the schedule's notice, horizon,
change cutoff and limits, and can't book while a booking block is in effect
(`blocks`). Staff booking for someone (`as_staff=True`) are not held to notice,
horizon, cutoff or blocks, and may override limits and caps; overrides are
recorded in the audit log. Nobody can book outside a tutor's hours or on top of
another appointment.

A student who cancels inside the schedule's late cancellation window is recorded
as canceling late (`attendance.is_late`).
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.policy import Action, can
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling import attendance, blocks, notifications
from sympraxi.scheduling.availability import fits_availability
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    BlockReason,
    Exemption,
    Modality,
    Participant,
    Schedule,
    TutorAssignment,
)
from sympraxi.scheduling.timeutils import (
    Interval,
    date_range_bounds,
    day_bounds,
    first_instant,
    local_date,
    week_bounds,
)

OVERLAP_CONSTRAINT = "scheduling_appointment_no_tutor_overlap"


class BookingError(Exception):
    """A rule refused the request. `message` is written for the person who asked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Booking --------------------------------------------------------------------


def book(
    *,
    actor: User,
    assignment: TutorAssignment,
    starts_at: datetime,
    minutes: int,
    modality: str,
    students: Sequence[User],
    as_staff: bool,
    is_walk_in: bool = False,
    is_placeholder: bool = False,
    is_tentative: bool = False,
    override_limits: bool = False,
    series_id: uuid.UUID | None = None,
    now: datetime | None = None,
    request: HttpRequest | None = None,
    notify: bool = True,
) -> Appointment:
    """Book a new appointment, or a seat in a group appointment at the same time."""
    now = now or timezone.now()
    schedule = assignment.schedule
    _check_booking_permission(actor, schedule, students, as_staff, is_placeholder)
    if not as_staff:
        is_walk_in = is_placeholder = is_tentative = override_limits = False
        is_tentative = assignment.needs_confirmation
    ends_at = starts_at + timedelta(minutes=minutes)
    try:
        with transaction.atomic():
            memberships = _lock_students(schedule.center, students)
            assignment = _lock_tutors([assignment.pk])[assignment.pk]
            schedule = assignment.schedule
            _check_slot(assignment, starts_at, minutes, modality, as_staff=as_staff, now=now)

            group = None if is_placeholder else _lock_group(assignment, starts_at, ends_at)
            if group is not None:
                if group.modality != modality:
                    raise BookingError(
                        "modality",
                        _("This group meets %(how)s.") % {"how": group.get_modality_display()},
                    )
                taken = group.active_participants().count()
                if taken + len(students) > group.capacity:
                    raise BookingError("full", _("That group appointment is full."))
            else:
                if len(students) > assignment.capacity:
                    raise BookingError(
                        "full", _("That tutor can't take this many students at once.")
                    )
                _check_tutor_free(assignment, starts_at, ends_at)
                if not (override_limits or is_placeholder):
                    _check_tutor_caps(assignment, starts_at)

            for membership in memberships:
                if not as_staff and blocks.in_effect(membership.user, schedule, now=now):
                    raise BookingError(
                        "blocked",
                        _("Online booking is paused for you. Contact the center to book."),
                    )
                _check_student_free(membership.user, starts_at, ends_at)
                if not override_limits:
                    _check_limits(schedule, membership.user, starts_at, minutes)

            if group is not None:
                appointment = group
                for student in students:
                    _add_participant(appointment, student, actor)
                action = AuditAction.APPOINTMENT_JOINED
            else:
                appointment = Appointment.objects.create(
                    schedule=schedule,
                    assignment=assignment,
                    tutor=assignment.tutor,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    blocked_until=ends_at + timedelta(minutes=schedule.buffer_minutes),
                    modality=modality,
                    is_walk_in=is_walk_in,
                    is_placeholder=is_placeholder,
                    is_tentative=is_tentative,
                    capacity=assignment.capacity,
                    series_id=series_id,
                    meeting_url=assignment.meeting_url if modality == Modality.ONLINE else "",
                    room=assignment.room if modality == Modality.IN_PERSON else None,
                    created_by=actor,
                )
                for student in students:
                    _add_participant(appointment, student, actor)
                action = AuditAction.APPOINTMENT_BOOKED
            _audit(
                action,
                actor,
                appointment,
                request,
                students=[str(s.pk) for s in students],
                staff=as_staff,
                override_limits=override_limits,
            )
    except (IntegrityError, OperationalError) as exc:
        _raise_if_taken(exc)
        raise
    if notify and not appointment.is_placeholder:
        _notify_on_commit(notifications.BOOKED, appointment, request, only=students)
    return appointment


def book_series(
    *,
    weeks: int,
    starts_at: datetime,
    assignment: TutorAssignment,
    request: HttpRequest | None = None,
    notify: bool = True,
    **kwargs: Any,
) -> list[Appointment]:
    """Book the same local time every week. All or nothing; staff only.

    Each week keeps its own wall-clock time, so a 4 p.m. series stays at 4 p.m.
    after clocks change.
    """
    if weeks < 1:
        raise BookingError("weeks", _("Book at least one week."))
    if weeks > 1 and not kwargs.get("as_staff"):
        raise BookingError("not_allowed", _("Only staff can book repeating appointments."))
    tz = assignment.schedule.tz
    local = starts_at.astimezone(tz)
    series_id = uuid.uuid4() if weeks > 1 else None
    booked: list[Appointment] = []
    with transaction.atomic():
        for week in range(weeks):
            day = local.date() + timedelta(weeks=week)
            start = first_instant(day, local.time().replace(tzinfo=None), tz)
            try:
                booked.append(
                    book(
                        assignment=assignment,
                        starts_at=start,
                        series_id=series_id,
                        request=request,
                        notify=False,
                        **kwargs,
                    )
                )
            except BookingError as exc:
                raise BookingError(
                    exc.code,
                    _("%(date)s: %(reason)s")
                    % {"date": _short_date(start, assignment.schedule), "reason": exc.message},
                ) from exc
    if notify and booked and not booked[0].is_placeholder:
        _notify_on_commit(notifications.BOOKED_SERIES, booked[0], request, series=booked)
    return booked


# --- Changing -------------------------------------------------------------------


def reschedule(
    *,
    actor: User,
    appointment: Appointment,
    starts_at: datetime,
    minutes: int,
    as_staff: bool,
    assignment: TutorAssignment | None = None,
    override_limits: bool = False,
    now: datetime | None = None,
    request: HttpRequest | None = None,
    notify: bool = True,
) -> Appointment:
    """Move an appointment to a new time, length or tutor on the same schedule."""
    now = now or timezone.now()
    target = assignment or appointment.assignment
    if target.schedule_id != appointment.schedule_id:
        raise BookingError(
            "schedule", _("To move an appointment to another schedule, cancel it and book again.")
        )
    if not as_staff:
        override_limits = False
    ends_at = starts_at + timedelta(minutes=minutes)
    try:
        with transaction.atomic():
            students = _students_in(appointment)
            memberships = _lock_students(appointment.schedule.center, students)
            locked = _lock_tutors({appointment.assignment_id, target.pk})
            target = locked[target.pk]
            current = (
                Appointment.objects.select_for_update(of=("self",))
                .select_related("schedule__center")
                .get(pk=appointment.pk)
            )
            if not current.is_active:
                raise BookingError("canceled", _("This appointment has been canceled."))
            if {s.pk for s in _students_in(current)} != {s.pk for s in students}:
                raise BookingError("changed", _("Someone changed this appointment. Try again."))
            _check_change_permission(actor, current, as_staff, now, Action.CHANGE_APPOINTMENT)
            if not as_staff:
                if [m.user_id for m in memberships] != [actor.pk]:
                    raise BookingError(
                        "not_allowed", _("Ask the front desk to change a group appointment.")
                    )
                if not can(actor, Action.BOOK_OWN, target.schedule):
                    raise BookingError("not_allowed", _("You can't book on this schedule."))
            schedule = target.schedule
            _check_slot(target, starts_at, minutes, current.modality, as_staff=as_staff, now=now)
            if len(students) > target.capacity:
                raise BookingError("full", _("That tutor can't take this many students at once."))
            _check_tutor_free(target, starts_at, ends_at, exclude=current)
            if not (override_limits or current.is_placeholder):
                _check_tutor_caps(target, starts_at, exclude=current)
            for membership in memberships:
                _check_student_free(membership.user, starts_at, ends_at, exclude=current)
                if not override_limits:
                    _check_limits(schedule, membership.user, starts_at, minutes, exclude=current)

            current.assignment = target
            current.tutor = target.tutor
            current.starts_at = starts_at
            current.ends_at = ends_at
            current.blocked_until = ends_at + timedelta(minutes=schedule.buffer_minutes)
            current.capacity = target.capacity
            if current.modality == Modality.ONLINE:
                current.meeting_url = target.meeting_url
            else:
                current.room = target.room
            current.sequence += 1
            current.save()
            _audit(
                AuditAction.APPOINTMENT_CHANGED,
                actor,
                current,
                request,
                staff=as_staff,
                override_limits=override_limits,
            )
    except (IntegrityError, OperationalError) as exc:
        _raise_if_taken(exc)
        raise
    if notify and not current.is_placeholder:
        _notify_on_commit(notifications.CHANGED, current, request)
    return current


def confirm(
    *,
    actor: User,
    appointment: Appointment,
    request: HttpRequest | None = None,
    notify: bool = True,
) -> Appointment:
    """Confirm a tentative booking."""
    if not can(actor, Action.BOOK_FOR_OTHERS, appointment.schedule.center):
        raise BookingError("not_allowed", _("Only staff can confirm appointments."))
    with transaction.atomic():
        current = Appointment.objects.select_for_update().get(pk=appointment.pk)
        if not current.is_active:
            raise BookingError("canceled", _("This appointment has been canceled."))
        if not current.is_tentative:
            raise BookingError("confirmed", _("This appointment is already confirmed."))
        current.is_tentative = False
        current.sequence += 1
        current.save(update_fields=["is_tentative", "sequence", "updated_at"])
        _audit(AuditAction.APPOINTMENT_CONFIRMED, actor, current, request)
    if notify:
        _notify_on_commit(notifications.CONFIRMED, current, request)
    return current


def cancel(
    *,
    actor: User,
    appointment: Appointment,
    as_staff: bool,
    student_asked: bool = False,
    now: datetime | None = None,
    request: HttpRequest | None = None,
    notify: bool = True,
) -> Appointment:
    """Cancel. A student in a group leaves it; the appointment ends with its last student.

    Staff cancel the whole appointment and every student in it is told. The tutor
    is told whenever the appointment itself is canceled.

    A student canceling inside the schedule's late cancellation window is recorded
    as canceling late, gets a late-cancellation email instead of the usual one,
    and may reach a booking block. Staff canceling for a student who asked
    (`student_asked`, one-student appointments only) are held to the same window.
    Staff canceling for the center's own reasons never record a late cancellation.
    """
    now = now or timezone.now()
    if not as_staff:
        student_asked = True
    asking: list[User] = []
    with transaction.atomic():
        if student_asked:
            # A late cancellation can set a block, so the students' memberships
            # are locked first, as when booking.
            asking = [actor] if not as_staff else _students_in(appointment)
            blocks.lock_students(appointment.schedule.center, [s.pk for s in asking])
        current = (
            Appointment.objects.select_for_update(of=("self",))
            .select_related("schedule__center")
            .get(pk=appointment.pk)
        )
        if not current.is_active:
            raise BookingError("canceled", _("This appointment is already canceled."))
        _check_change_permission(actor, current, as_staff, now, Action.CANCEL_APPOINTMENT)
        students = list(
            User.objects.filter(
                participations__appointment=current, participations__canceled_at__isnull=True
            )
        )
        if as_staff:
            told = students
        else:
            if actor not in students:
                raise BookingError("not_allowed", _("You aren't in this appointment."))
            told = [actor]
        late = student_asked and bool(told) and attendance.is_late(current, now)
        if late and len(told) > 1:
            raise BookingError(
                "late_group",
                _("A late cancellation is recorded for one student at a time."),
            )
        if late and {s.pk for s in told} - {s.pk for s in asking}:
            raise BookingError("changed", _("Someone changed this appointment. Try again."))
        current.participants.filter(student__in=told, canceled_at__isnull=True).update(
            canceled_at=now, late_cancel=late
        )
        whole = not current.active_participants().exists()
        if whole:
            current.status = AppointmentStatus.CANCELED
            current.canceled_at = now
            current.canceled_by = actor
            current.sequence += 1
            current.save()
            _audit(
                AuditAction.APPOINTMENT_CANCELED, actor, current, request, staff=as_staff, late=late
            )
        else:
            _audit(
                AuditAction.APPOINTMENT_LEFT,
                actor,
                current,
                request,
                student=str(actor.pk),
                late=late,
            )
        if late:
            for student in told:
                blocks.evaluate(
                    student=student,
                    appointment=current,
                    reason=BlockReason.LATE_CANCELS,
                    actor=actor,
                    request=request,
                    now=now,
                )
    if notify and not current.is_placeholder:
        if late:
            _notify_on_commit(notifications.LATE_CANCELED, current, request, only=told, tutor=False)
            if whole:
                _notify_on_commit(notifications.CANCELED, current, request, only=[], tutor=True)
        else:
            _notify_on_commit(notifications.CANCELED, current, request, only=told, tutor=whole)
    return current


def can_change(appointment: Appointment, now: datetime | None = None) -> bool:
    """True if a student may still cancel or move this appointment themselves."""
    now = now or timezone.now()
    cutoff = appointment.starts_at - timedelta(minutes=appointment.schedule.change_cutoff_minutes)
    return appointment.is_active and now < cutoff and now < appointment.starts_at


# --- Rules ----------------------------------------------------------------------


def _check_booking_permission(
    actor: User,
    schedule: Schedule,
    students: Sequence[User],
    as_staff: bool,
    is_placeholder: bool,
) -> None:
    if as_staff:
        if not can(actor, Action.BOOK_FOR_OTHERS, schedule):
            raise BookingError("not_allowed", _("You can't book for other people here."))
        if is_placeholder and students:
            raise BookingError("placeholder", _("A placeholder holds time without a student."))
        if not is_placeholder and not students:
            raise BookingError("no_student", _("Choose a student."))
    else:
        if not can(actor, Action.BOOK_OWN, schedule):
            raise BookingError("not_allowed", _("You can't book on this schedule."))
        if [s.pk for s in students] != [actor.pk]:
            raise BookingError("not_allowed", _("You can only book for yourself."))
    if len({s.pk for s in students}) != len(students):
        raise BookingError("duplicate", _("A student is listed twice."))


def _check_change_permission(
    actor: User, appointment: Appointment, as_staff: bool, now: datetime, action: Action
) -> None:
    if as_staff:
        if not can(actor, Action.BOOK_FOR_OTHERS, appointment.schedule.center):
            raise BookingError("not_allowed", _("You can't change other people's appointments."))
        return
    if not can(actor, action, appointment):
        raise BookingError("not_allowed", _("You aren't in this appointment."))
    if not can_change(appointment, now):
        raise BookingError(
            "cutoff",
            _("It's too close to the appointment to change it online. Contact the center instead."),
        )


def _check_slot(
    assignment: TutorAssignment,
    starts_at: datetime,
    minutes: int,
    modality: str,
    *,
    as_staff: bool,
    now: datetime,
) -> None:
    schedule = assignment.schedule
    if not schedule.accepts_bookings:
        raise BookingError("schedule_closed", _("This schedule isn't taking appointments."))
    if not assignment.is_active:
        raise BookingError("tutor_inactive", _("This tutor isn't taking appointments."))
    if minutes not in assignment.lengths():
        raise BookingError("length", _("This tutor doesn't offer appointments of that length."))
    if modality not in assignment.modalities:
        raise BookingError("modality", _("This tutor doesn't meet that way."))
    ends_at = starts_at + timedelta(minutes=minutes)
    if not fits_availability(assignment, starts_at, ends_at):
        raise BookingError("outside_hours", _("The tutor isn't available at that time."))
    if as_staff:
        return
    if starts_at < now + timedelta(minutes=schedule.min_notice_minutes):
        raise BookingError("notice", _("That time is too soon to book online."))
    tz = schedule.tz
    if local_date(starts_at, tz) > local_date(now, tz) + timedelta(days=schedule.horizon_days):
        raise BookingError(
            "horizon",
            _("You can book up to %(days)s days ahead.") % {"days": schedule.horizon_days},
        )


def _check_tutor_free(
    assignment: TutorAssignment,
    starts_at: datetime,
    ends_at: datetime,
    exclude: Appointment | None = None,
) -> None:
    blocked_until = ends_at + timedelta(minutes=assignment.schedule.buffer_minutes)
    clash = (
        Appointment.objects.filter(
            tutor_id=assignment.tutor_id, starts_at__lt=blocked_until, blocked_until__gt=starts_at
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .exclude(pk__in=[exclude.pk] if exclude else [])
    )
    if clash.exists():
        raise BookingError("slot_taken", _("Someone else just booked that time."))


def _check_tutor_caps(
    assignment: TutorAssignment, starts_at: datetime, exclude: Appointment | None = None
) -> None:
    tz = assignment.schedule.tz
    day = local_date(starts_at, tz)
    for bounds, cap, message in (
        (day_bounds(day, tz), assignment.max_per_day, _("This tutor is fully booked that day.")),
        (week_bounds(day, tz), assignment.max_per_week, _("This tutor is fully booked that week.")),
    ):
        if cap is None:
            continue
        count = (
            Appointment.objects.filter(
                assignment=assignment,
                is_placeholder=False,
                starts_at__gte=bounds[0],
                starts_at__lt=bounds[1],
            )
            .exclude(status=AppointmentStatus.CANCELED)
            .exclude(pk__in=[exclude.pk] if exclude else [])
            .count()
        )
        if count + 1 > cap:
            raise BookingError("tutor_cap", message)


def _check_student_free(
    student: User, starts_at: datetime, ends_at: datetime, exclude: Appointment | None = None
) -> None:
    clash = (
        _active_participations(student)
        .filter(appointment__starts_at__lt=ends_at, appointment__ends_at__gt=starts_at)
        .exclude(appointment=exclude)
    )
    if clash.exists():
        raise BookingError(
            "student_busy", _("%(name)s already has an appointment then.") % {"name": student}
        )


def is_exempt(schedule: Schedule, student: User, day: date) -> bool:
    """True if an exemption lifts the student's limits for an appointment on `day`."""
    return (
        Exemption.objects.filter(
            center=schedule.center,
            student=student,
            revoked_at__isnull=True,
            expires_on__gte=day,
        )
        .filter(Q(schedule__isnull=True) | Q(schedule=schedule))
        .exists()
    )


def limit_windows(schedule: Schedule, starts_at: datetime) -> list[tuple[str, Interval]]:
    """The day, week and term an appointment counts against, as instants."""
    tz = schedule.tz
    day = local_date(starts_at, tz)
    return [
        ("day", day_bounds(day, tz)),
        ("week", week_bounds(day, tz)),
        ("term", date_range_bounds(schedule.term.starts_on, schedule.term.ends_on, tz)),
    ]


def counted_participations(
    schedule: Schedule, student: User, window: Interval, exclude: Appointment | None = None
) -> list[tuple[datetime, datetime]]:
    """The student's appointments that count toward this schedule's limits in a window."""
    rows = _active_participations(student).filter(
        appointment__is_placeholder=False,
        appointment__starts_at__gte=window[0],
        appointment__starts_at__lt=window[1],
    )
    if schedule.limits_span_center:
        rows = rows.filter(appointment__schedule__center=schedule.center)
    else:
        rows = rows.filter(appointment__schedule=schedule)
    if exclude is not None:
        rows = rows.exclude(appointment=exclude)
    return list(rows.values_list("appointment__starts_at", "appointment__ends_at"))


LIMIT_FIELDS = {
    "day": ("max_per_day", "max_minutes_per_day"),
    "week": ("max_per_week", "max_minutes_per_week"),
    "term": ("max_per_term", "max_minutes_per_term"),
}


def _check_limits(
    schedule: Schedule,
    student: User,
    starts_at: datetime,
    minutes: int,
    exclude: Appointment | None = None,
) -> None:
    if is_exempt(schedule, student, local_date(starts_at, schedule.tz)):
        return
    messages = {
        "day": _("That would go over the limit of %(n)s appointments a day."),
        "week": _("That would go over the limit of %(n)s appointments a week."),
        "term": _("That would go over the limit of %(n)s appointments this term."),
    }
    minute_messages = {
        "day": _("That would go over the limit of %(n)s minutes a day."),
        "week": _("That would go over the limit of %(n)s minutes a week."),
        "term": _("That would go over the limit of %(n)s minutes this term."),
    }
    for period, window in limit_windows(schedule, starts_at):
        count_field, minutes_field = LIMIT_FIELDS[period]
        max_count = getattr(schedule, count_field)
        max_minutes = getattr(schedule, minutes_field)
        if max_count is None and max_minutes is None:
            continue
        rows = counted_participations(schedule, student, window, exclude)
        if max_count is not None and len(rows) + 1 > max_count:
            raise BookingError(f"limit_{period}", messages[period] % {"n": max_count})
        used = sum(int((end - start) / timedelta(minutes=1)) for start, end in rows)
        if max_minutes is not None and used + minutes > max_minutes:
            raise BookingError(f"minutes_{period}", minute_messages[period] % {"n": max_minutes})


# --- Helpers --------------------------------------------------------------------


def _active_participations(student: User) -> QuerySet[Participant]:
    return Participant.objects.filter(student=student, canceled_at__isnull=True).exclude(
        appointment__status=AppointmentStatus.CANCELED
    )


def _students_in(appointment: Appointment) -> list[User]:
    return list(
        User.objects.filter(
            participations__appointment=appointment, participations__canceled_at__isnull=True
        ).order_by("pk")
    )


def _lock_students(center: Center, students: Sequence[User]) -> list[Membership]:
    """Lock each student's membership, in a fixed order. Every student must be one here."""
    if not students:
        return []
    memberships = list(
        Membership.objects.select_for_update(of=("self",))
        .select_related("user")
        .filter(
            center=center,
            role=Role.STUDENT,
            is_active=True,
            user__in=[s.pk for s in students],
            user__is_active=True,
        )
        .order_by("pk")
    )
    if len(memberships) != len({s.pk for s in students}):
        raise BookingError("not_member", _("Only students of this center can be booked."))
    return memberships


def _lock_tutors(ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, TutorAssignment]:
    """Lock every assignment the tutors of these assignments have, by primary key.

    Locking only the assignment being booked lets two bookings for one tutor on
    two schedules both insert at once; PostgreSQL then stops one with a deadlock
    while checking the overlap constraint. With every assignment locked they
    take turns, and the later one's check sees the earlier appointment.
    """
    tutors = TutorAssignment.objects.filter(pk__in=list(ids)).values("tutor_id")
    rows = (
        TutorAssignment.objects.select_for_update(of=("self",))
        .select_related("schedule__center", "schedule__term", "tutor", "room")
        .filter(tutor__in=tutors)
        .order_by("pk")
    )
    return {a.pk: a for a in rows}


def _lock_group(
    assignment: TutorAssignment, starts_at: datetime, ends_at: datetime
) -> Appointment | None:
    if assignment.capacity <= 1:
        return None
    return (
        Appointment.objects.select_for_update(of=("self",))
        .select_related("schedule__center")
        .filter(
            assignment=assignment,
            starts_at=starts_at,
            ends_at=ends_at,
            is_placeholder=False,
            capacity__gt=1,
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .first()
    )


def _add_participant(appointment: Appointment, student: User, actor: User) -> None:
    participant, created = Participant.objects.get_or_create(
        appointment=appointment, student=student, defaults={"added_by": actor}
    )
    if not created:
        if participant.canceled_at is None:
            raise BookingError(
                "already_in", _("%(name)s is already in that appointment.") % {"name": student}
            )
        participant.canceled_at = None
        participant.added_by = actor
        participant.save(update_fields=["canceled_at", "added_by"])


def _raise_if_taken(exc: DatabaseError) -> None:
    """Refuse politely when a booking at the same moment stopped this write.

    The tutor locks should leave only the overlap constraint to catch, but a
    deadlock or serialization failure means the same thing to the person asking.
    """
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    if getattr(diag, "constraint_name", None) == OVERLAP_CONSTRAINT or isinstance(
        cause, (pg_errors.DeadlockDetected, pg_errors.SerializationFailure)
    ):
        raise BookingError("slot_taken", _("Someone else just booked that time.")) from exc


def _audit(
    action: AuditAction,
    actor: User,
    appointment: Appointment,
    request: HttpRequest | None,
    **details: Any,
) -> None:
    record(
        action,
        request=request,
        actor=actor,
        center=appointment.schedule.center,
        target=appointment,
        schedule=str(appointment.schedule_id),
        starts_at=appointment.starts_at.isoformat(),
        **details,
    )


def _notify_on_commit(
    kind: str, appointment: Appointment, request: HttpRequest | None, **kwargs: Any
) -> None:
    base_url = notifications.base_url(request)
    transaction.on_commit(
        lambda: notifications.send(kind, appointment.pk, base_url=base_url, **_ids(kwargs))
    )


def _ids(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Pass IDs, not model instances, to the mailer, so it reads fresh rows."""
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, list):
            out[key] = [v.pk for v in value]
        else:
            out[key] = value
    return out


def _short_date(instant: datetime, schedule: Schedule) -> str:
    return date_format(instant.astimezone(schedule.tz).date(), "DATE_FORMAT")
