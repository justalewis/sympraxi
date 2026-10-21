"""Who came: attendance, possible no-shows, late cancellations and waivers.

Attendance belongs to each student in an appointment (`Participant`), not to the
appointment, because in a group some students come and some don't. The
appointment's own status stays "scheduled" whatever happened: a no-show still
held the tutor's time, so the overlap constraint is unchanged. A late
cancellation is a cancellation like any other (the time is freed) with
`late_cancel` set on the student's row.

- Tutors (for appointments they teach), front desk and directors mark each
  student as here or a no-show once the appointment has started.
- A final session record means the student came. Finalizing marks them as here,
  and a student with a final record can't be marked a no-show.
- The scheduler flags a student no one has marked by the end of the schedule's
  grace period as a possible no-show (`flag_possible_no_shows`). Staff confirm
  it, mark the student as here, or clear it. Nothing becomes a no-show on its own.
- A confirmed no-show sends the student a gentle email, and may set a booking
  block (`blocks.evaluate`).
- Front desk and directors waive a late cancellation or no-show, with a reason,
  so it no longer counts toward a block.

Locks follow the order in `services`: the students' memberships, then the
appointment, then its participants.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, can
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records.models import RecordStatus, SessionRecord
from sympraxi.scheduling import blocks, notifications
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    BlockReason,
    Participant,
)
from sympraxi.staff.models import Arrival

# The no-show sweep looks back this far, so an upgrade doesn't flag a term of
# unmarked history and a long outage doesn't flag last week. Staff can still
# mark older appointments by hand.
FLAG_LOOKBACK = timedelta(days=2)

MARKS = {Attendance.ATTENDED.value, Attendance.NO_SHOW.value, Attendance.UNMARKED.value}


class AttendanceError(Exception):
    """A rule refused the request. `message` is written for the person who asked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_late(appointment: Appointment, now: datetime) -> bool:
    """Whether a cancellation now falls inside the schedule's late cancellation window."""
    window = appointment.schedule.late_cancel_minutes
    return window > 0 and now >= appointment.starts_at - timedelta(minutes=window)


# --- Marking --------------------------------------------------------------------


def mark(
    *,
    actor: User,
    appointment: Appointment,
    marks: Mapping[UUID, str],
    now: datetime | None = None,
    request: HttpRequest | None = None,
    notify: bool = True,
) -> int:
    """Record who came. Returns how many students' attendance changed.

    `marks` maps student IDs to "attended", "no_show" or "" (not marked). Any
    mark but "no_show" on a student flagged as a possible no-show clears the flag.
    """
    now = now or timezone.now()
    if not can(actor, Action.MARK_ATTENDANCE, appointment):
        raise AttendanceError("not_allowed", _("You can't mark attendance for this appointment."))
    if any(value not in MARKS for value in marks.values()):
        raise AttendanceError("invalid", _("Choose came, no-show or not marked."))
    center = appointment.schedule.center
    changed = 0
    no_shows: list[Participant] = []
    with transaction.atomic():
        blocks.lock_students(center, marks)
        current = (
            Appointment.objects.select_for_update(of=("self",))
            .select_related("schedule__center", "schedule__term")
            .get(pk=appointment.pk)
        )
        if not current.is_active:
            raise AttendanceError("canceled", _("This appointment was canceled."))
        if current.starts_at > now:
            raise AttendanceError(
                "not_started", _("You can mark attendance once the appointment starts.")
            )
        participants = {
            p.student_id: p
            for p in Participant.objects.select_for_update(of=("self",))
            .select_related("student")
            .filter(appointment=current, student__in=list(marks), canceled_at__isnull=True)
        }
        final = set(
            SessionRecord.objects.filter(
                appointment=current, student__in=list(marks), status=RecordStatus.FINAL
            ).values_list("student_id", flat=True)
        )
        for student_id, value in marks.items():
            participant = participants.get(student_id)
            if participant is None:
                raise AttendanceError("not_in_it", _("That student isn't in this appointment."))
            clearing = value == Attendance.UNMARKED and participant.is_possible_no_show
            if value == participant.attendance and not clearing:
                continue
            if value != Attendance.ATTENDED and student_id in final:
                raise AttendanceError(
                    "has_record",
                    _("%(name)s has a final session record, so they came.")
                    % {"name": participant.student},
                )
            before = participant.attendance
            _set(participant, value, actor, now)
            record(
                AuditAction.ATTENDANCE_MARKED,
                request=request,
                actor=actor,
                center=center,
                target=current,
                student=str(student_id),
                before=before or "unmarked",
                after=value or "unmarked",
                was_flagged=participant.flagged_at is not None,
            )
            changed += 1
            if value == Attendance.NO_SHOW:
                no_shows.append(participant)
        for participant in no_shows:
            blocks.evaluate(
                student=participant.student,
                appointment=current,
                reason=BlockReason.NO_SHOWS,
                actor=actor,
                request=request,
                now=now,
            )
    if notify and no_shows:
        base_url = notifications.base_url(request)
        ids = [p.pk for p in no_shows]
        transaction.on_commit(
            lambda: [notifications.send_no_show(pk, base_url=base_url) for pk in ids]
        )
    return changed


def _set(participant: Participant, value: str, actor: User | None, now: datetime) -> None:
    participant.attendance = value
    participant.attendance_marked_at = now
    participant.attendance_marked_by = actor
    fields = ["attendance", "attendance_marked_at", "attendance_marked_by"]
    if (
        participant.flagged_at is not None
        and participant.flag_cleared_at is None
        and value != Attendance.NO_SHOW
    ):
        participant.flag_cleared_at = now
        participant.flag_cleared_by = actor
        fields += ["flag_cleared_at", "flag_cleared_by"]
    participant.save(update_fields=fields)


def record_came(
    rec: SessionRecord,
    *,
    actor: User,
    now: datetime,
    request: HttpRequest | None = None,
) -> bool:
    """A final session record means the student came. Called by `records.services.finalize`.

    This overrides a no-show, since the record shows the session happened.
    """
    if rec.appointment_id is None:
        return False
    participant = (
        Participant.objects.select_for_update(of=("self",))
        .select_related("appointment")
        .filter(appointment=rec.appointment_id, student=rec.student_id, canceled_at__isnull=True)
        .first()
    )
    if participant is None or participant.attendance == Attendance.ATTENDED:
        return False
    before = participant.attendance
    _set(participant, Attendance.ATTENDED, actor, now)
    record(
        AuditAction.ATTENDANCE_MARKED,
        request=request,
        actor=actor,
        center=rec.center,
        target=participant.appointment,
        student=str(rec.student_id),
        before=before or "unmarked",
        after=Attendance.ATTENDED.value,
        source="session record",
    )
    return True


# --- Waivers --------------------------------------------------------------------


def waive(
    *,
    actor: User,
    appointment: Appointment,
    student_id: Any,
    reason: str,
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> Participant:
    """Take a late cancellation or no-show out of the block count. It stays on the record."""
    now = now or timezone.now()
    if not can(actor, Action.WAIVE_ATTENDANCE, appointment):
        raise AttendanceError("not_allowed", _("You can't waive anything here."))
    reason = reason.strip()
    if not reason:
        raise AttendanceError("reason", _("Say why you're waiving it."))
    with transaction.atomic():
        participant = (
            Participant.objects.select_for_update(of=("self",))
            .select_related("student")
            .filter(appointment=appointment, student=student_id)
            .first()
        )
        if participant is None or not participant.can_be_waived:
            raise AttendanceError("nothing", _("There's nothing to waive for this student."))
        participant.waived_at = now
        participant.waived_by = actor
        participant.waiver_reason = reason
        participant.save(update_fields=["waived_at", "waived_by", "waiver_reason"])
        record(
            AuditAction.ATTENDANCE_WAIVED,
            request=request,
            actor=actor,
            center=appointment.schedule.center,
            target=appointment,
            student=str(participant.student_id),
            what="late_cancel" if participant.late_cancel else "no_show",
        )
    return participant


# --- The no-show sweep ----------------------------------------------------------


def flag_possible_no_shows(now: datetime | None = None) -> int:
    """Flag students no one has marked by the end of their schedule's grace period.

    Tentative appointments, placeholders and students with a session record (even
    a draft) aren't flagged. Safe to run twice at once: the update only touches
    rows that are still unflagged.
    """
    now = now or timezone.now()
    has_record = SessionRecord.objects.filter(
        appointment=OuterRef("appointment"), student=OuterRef("student")
    )
    # A student who checked in at the front desk came, whatever else is recorded.
    arrived = Arrival.objects.filter(
        appointment=OuterRef("appointment"), student=OuterRef("student")
    )
    candidates = (
        Participant.objects.filter(
            canceled_at__isnull=True,
            attendance=Attendance.UNMARKED,
            flagged_at__isnull=True,
            appointment__status=AppointmentStatus.SCHEDULED,
            appointment__is_placeholder=False,
            appointment__is_tentative=False,
            appointment__starts_at__lte=now,
            appointment__starts_at__gte=now - FLAG_LOOKBACK,
        )
        .filter(~Exists(has_record))
        .filter(~Exists(arrived))
        .values_list("pk", "appointment__starts_at", "appointment__schedule__no_show_grace_minutes")
    )
    due = [pk for pk, start, grace in candidates if start + timedelta(minutes=grace) <= now]
    if not due:
        return 0
    return Participant.objects.filter(
        pk__in=due,
        canceled_at__isnull=True,
        attendance=Attendance.UNMARKED,
        flagged_at__isnull=True,
    ).update(flagged_at=now)
