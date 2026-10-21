"""Arrivals: a student checks in at the front desk's kiosk and waits to be seen.

The kiosk is a page on a shared screen, kept signed in by a front desk person or a
director. A student types their ID, or swipes a card through a reader that types
the number and presses Enter, and is told whether they're checked in. The same
check-in is open to staff on the waiting room page.

- A student with an appointment starting within the next half hour (or already
  under way) is checked in for it. Staff see when they arrived, and the no-show
  sweep leaves them alone.
- A student with no appointment is a walk-in and joins the waiting room, with an
  optional note on what they're working on.
- Checking in twice does nothing the second time.
- The kiosk says only the student's first name and the time, so a stranger who
  guesses an ID learns nothing else.
"""

import re
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Appointment, AppointmentStatus
from sympraxi.staff.models import Arrival, ArrivalStatus

# A student may check in this long before their appointment starts.
EARLY = timedelta(minutes=30)
# The kiosk page returns to its start this many seconds after showing the result.
RESULT_SECONDS = 8


def parse_card(text: str) -> str:
    """The ID in what a keyboard-style card reader or a person typed.

    A magnetic stripe reader sends track data like ";123456789?" or "%123456789?".
    Anything else is taken as typed, without spaces.
    """
    text = text.strip()
    if text[:1] in (";", "%"):
        found = re.match(r"[;%]\s*([A-Za-z0-9-]+)", text)
        return found.group(1) if found else ""
    return re.sub(r"\s+", "", text)


def find_student(center: Center, text: str) -> User | None:
    code = parse_card(text)
    if not code:
        return None
    return (
        User.objects.filter(
            student_id__iexact=code,
            is_active=True,
            memberships__center=center,
            memberships__role=Role.STUDENT,
            memberships__is_active=True,
        )
        .distinct()
        .first()
    )


def first_name(user: User) -> str:
    return user.preferred_name or (user.full_name.split() or [user.email])[0]


def upcoming_appointment(center: Center, student: User, *, now=None) -> Appointment | None:
    """The student's appointment that is under way or about to start, earliest first."""
    now = now or timezone.now()
    return (
        Appointment.objects.filter(
            schedule__center=center,
            status=AppointmentStatus.SCHEDULED,
            is_placeholder=False,
            participants__student=student,
            participants__canceled_at__isnull=True,
            starts_at__lte=now + EARLY,
            ends_at__gt=now,
        )
        .select_related("tutor")
        .order_by("starts_at")
        .first()
    )


@dataclass
class CheckIn:
    arrival: Arrival
    appointment: Appointment | None
    already: bool


def check_in(
    center: Center, student: User, *, request: HttpRequest, via: str, reason: str = ""
) -> CheckIn:
    appointment = upcoming_appointment(center, student)
    waiting = Arrival.objects.filter(
        center=center, student=student, status=ArrivalStatus.WAITING
    ).first()
    if waiting is not None:
        return CheckIn(waiting, waiting.appointment, already=True)
    try:
        with transaction.atomic():
            arrival = Arrival.objects.create(
                center=center,
                student=student,
                appointment=appointment,
                via=via,
                reason=reason.strip()[:200] if appointment is None else "",
            )
            record(
                AuditAction.ARRIVAL_RECORDED,
                request=request,
                center=center,
                target=arrival,
                student=str(student.pk),
                via=via,
                walk_in=appointment is None,
            )
    except IntegrityError:
        waiting = Arrival.objects.get(center=center, student=student, status=ArrivalStatus.WAITING)
        return CheckIn(waiting, waiting.appointment, already=True)
    return CheckIn(arrival, appointment, already=False)


def waiting_now(center: Center) -> QuerySet[Arrival]:
    return (
        Arrival.objects.filter(center=center, status=ArrivalStatus.WAITING)
        .select_related("student", "appointment", "appointment__tutor")
        .order_by("arrived_at")
    )


def handled_today(center: Center, since) -> QuerySet[Arrival]:
    return (
        Arrival.objects.filter(center=center, arrived_at__gte=since)
        .exclude(status=ArrivalStatus.WAITING)
        .select_related("student", "appointment", "appointment__tutor", "handled_by")
        .order_by("-handled_at")
    )


def mark(arrival: Arrival, status: str, *, request: HttpRequest) -> bool:
    """Mark a waiting student seen or gone. False if someone else already did."""
    if status not in (ArrivalStatus.SEEN, ArrivalStatus.LEFT):
        raise ValueError(status)
    with transaction.atomic():
        locked = Arrival.objects.select_for_update().get(pk=arrival.pk)
        if locked.status != ArrivalStatus.WAITING:
            return False
        locked.status = status
        locked.handled_at = timezone.now()
        locked.handled_by = request.user  # type: ignore[assignment]
        locked.save(update_fields=["status", "handled_at", "handled_by"])
        record(
            AuditAction.ARRIVAL_HANDLED,
            request=request,
            center=locked.center,
            target=locked,
            student=str(locked.student_id),
            status=status,
            waited_minutes=int((locked.handled_at - locked.arrived_at).total_seconds() // 60),
        )
    return True


def describe(arrival: Arrival) -> str:
    """What the student is here for, for the waiting room."""
    if arrival.appointment is not None:
        return _("Appointment with %(tutor)s") % {"tutor": arrival.appointment.tutor.display_name}
    return arrival.reason or _("Walk-in")
