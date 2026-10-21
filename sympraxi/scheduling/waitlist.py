"""The waiting list: a full schedule can offer a canceled time to the students who asked.

A student joins with a day, a window of the day, a length and how they'd meet, and
optionally a tutor. When an appointment that overlaps the wish is canceled or moved
away, the oldest waiting student whose wish fits and who could actually book it is
offered the time, alone:

- The time is held as a placeholder appointment for the schedule's hold length, so
  the database's overlap rule keeps everyone else out, and the student is emailed.
- Accepting cancels the hold and books the student into the time through the usual
  booking rules, in one transaction. Their limits and any booking block still apply;
  only the minimum notice and how-far-ahead rules are waived, because the time just
  opened. If a rule stops them, nothing changes and they are told why.
- Declining, leaving the list, or letting the hold run out frees the time and offers it
  to the next student on the list.
- A wish for a day that has passed lapses.

The scheduler calls `sweep` every minute, which expires holds and lapses old wishes.
`services.cancel` and `services.reschedule` call `slot_opened` after they commit.
"""

import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, can
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling import availability, services
from sympraxi.scheduling.models import (
    ACTIVE_WAITING,
    Appointment,
    AppointmentStatus,
    Schedule,
    TutorAssignment,
    WaitingEntry,
    WaitingStatus,
)
from sympraxi.scheduling.timeutils import local_date

logger = logging.getLogger("sympraxi.waitlist")

# The most wishes a student may have open at once.
MAX_ACTIVE = 5


class WaitlistError(Exception):
    """Something the student can read and act on."""


class _DryRun(Exception):
    """Raised to roll back a trial booking."""


# --- Joining and leaving ------------------------------------------------------------


def join(
    student: User,
    schedule: Schedule,
    *,
    day: date,
    earliest: time,
    latest: time,
    minutes: int,
    modality: str,
    tutor: User | None,
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> WaitingEntry:
    now = now or timezone.now()
    today = local_date(now, schedule.tz)
    if not schedule.waitlist_enabled or not schedule.accepts_bookings:
        raise WaitlistError(_("This schedule doesn't have a waiting list."))
    if not can(student, Action.BOOK_OWN, schedule):
        raise WaitlistError(_("You can't book on this schedule."))
    if day < today:
        raise WaitlistError(_("Choose a day that hasn't passed."))
    if day > today + timedelta(days=schedule.horizon_days):
        raise WaitlistError(_("That day is further ahead than this schedule takes bookings."))
    if latest <= earliest:
        raise WaitlistError(_("The latest time has to be after the earliest."))
    window = (datetime.combine(day, latest) - datetime.combine(day, earliest)).total_seconds() / 60
    if window < minutes:
        raise WaitlistError(_("Your window is shorter than the appointment you asked for."))
    if minutes not in availability.lengths_offered(schedule):
        raise WaitlistError(_("This schedule doesn't offer appointments of that length."))
    if tutor is not None and not schedule.assignments.filter(tutor=tutor, is_active=True).exists():
        raise WaitlistError(_("That tutor isn't on this schedule."))
    if (
        WaitingEntry.objects.filter(student=student, status__in=ACTIVE_WAITING).count()
        >= MAX_ACTIVE
    ):
        raise WaitlistError(
            _("You can be on the waiting list for %(n)s days at a time.") % {"n": MAX_ACTIVE}
        )
    try:
        with transaction.atomic():
            entry = WaitingEntry.objects.create(
                schedule=schedule,
                student=student,
                tutor=tutor,
                day=day,
                earliest=earliest,
                latest=latest,
                minutes=minutes,
                modality=modality,
            )
            record(
                AuditAction.WAITLIST_JOINED,
                request=request,
                actor=student,
                center=schedule.center,
                target=entry,
                schedule=str(schedule.pk),
                day=day.isoformat(),
            )
    except IntegrityError as error:
        raise WaitlistError(
            _("You're already on the waiting list for that day on this schedule.")
        ) from error
    return entry


def leave(entry: WaitingEntry, *, actor: User, request: HttpRequest | None = None) -> bool:
    """Take a wish off the list; if a time was held, free it for the next student."""
    freed = None
    with transaction.atomic():
        locked = (
            WaitingEntry.objects.select_for_update(of=("self",))
            .select_related("hold")
            .get(pk=entry.pk)
        )
        if not locked.is_active:
            return False
        freed = _release(locked, WaitingStatus.REMOVED)
        record(
            AuditAction.WAITLIST_LEFT,
            request=request,
            actor=actor,
            center=locked.schedule.center,
            target=locked,
            student=str(locked.student_id),
            by_student=actor.pk == locked.student_id,
        )
    _after_release(freed)
    return True


def decline(entry: WaitingEntry, *, request: HttpRequest | None = None) -> bool:
    with transaction.atomic():
        locked = (
            WaitingEntry.objects.select_for_update(of=("self",))
            .select_related("hold")
            .get(pk=entry.pk)
        )
        if locked.status != WaitingStatus.OFFERED:
            return False
        freed = _release(locked, WaitingStatus.DECLINED)
        record(
            AuditAction.WAITLIST_DECLINED,
            request=request,
            actor=locked.student,
            center=locked.schedule.center,
            target=locked,
        )
    _after_release(freed)
    return True


# --- Offering a time ------------------------------------------------------------------


def slot_opened(
    assignment_id: Any, starts_at: datetime, ends_at: datetime, *, now: datetime | None = None
) -> bool:
    """Offer a time that just opened to the first waiting student it suits. True if offered."""
    now = now or timezone.now()
    try:
        assignment = TutorAssignment.objects.select_related("schedule__center", "tutor").get(
            pk=assignment_id
        )
    except TutorAssignment.DoesNotExist:
        return False
    schedule = assignment.schedule
    if not schedule.waitlist_enabled or starts_at <= now or not assignment.is_active:
        return False
    local_start = starts_at.astimezone(schedule.tz)
    freed_minutes = int((ends_at - starts_at).total_seconds() // 60)
    lengths = assignment.lengths()
    candidates = WaitingEntry.objects.filter(
        schedule=schedule, status=WaitingStatus.WAITING, day=local_start.date()
    ).filter(Q(tutor__isnull=True) | Q(tutor=assignment.tutor))
    for entry in candidates.select_related("student").order_by("joined_at", "pk"):
        if entry.minutes > freed_minutes or entry.minutes not in lengths:
            continue
        if entry.modality not in assignment.modalities:
            continue
        local_end = local_start + timedelta(minutes=entry.minutes)
        if local_start.time() < entry.earliest or local_end.date() != local_start.date():
            continue
        if local_end.time() > entry.latest:
            continue
        if _offer(entry, assignment, starts_at, ends_at, now):
            return True
    return False


def _could_book(entry: WaitingEntry, assignment: TutorAssignment, starts_at: datetime, now) -> bool:
    """Try the student's booking and roll it back, to learn whether the rules would allow it."""
    try:
        with transaction.atomic():
            services.book(
                actor=entry.student,
                assignment=assignment,
                starts_at=starts_at,
                minutes=entry.minutes,
                modality=entry.modality,
                students=[entry.student],
                as_staff=False,
                now=now,
                notify=False,
                from_waiting_list=True,
            )
            raise _DryRun
    except _DryRun:
        return True
    except services.BookingError:
        return False


def _offer(
    entry: WaitingEntry,
    assignment: TutorAssignment,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> bool:
    schedule = assignment.schedule
    with transaction.atomic():
        locked = WaitingEntry.objects.select_for_update().get(pk=entry.pk)
        if locked.status != WaitingStatus.WAITING:
            return False
        if not _could_book(locked, assignment, starts_at, now):
            return False
        try:
            with transaction.atomic():
                hold = Appointment.objects.create(
                    schedule=schedule,
                    assignment=assignment,
                    tutor=assignment.tutor,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    blocked_until=ends_at + timedelta(minutes=schedule.buffer_minutes),
                    modality=locked.modality,
                    is_placeholder=True,
                    capacity=assignment.capacity,
                    created_by=None,
                )
        except IntegrityError:
            return False  # the time was taken between the cancel and now
        locked.status = WaitingStatus.OFFERED
        locked.offered_at = now
        locked.offer_expires_at = now + timedelta(minutes=schedule.waitlist_hold_minutes)
        locked.hold = hold
        locked.save(update_fields=["status", "offered_at", "offer_expires_at", "hold"])
        record(
            AuditAction.WAITLIST_OFFERED,
            actor_label="waiting list",
            center=schedule.center,
            target=locked,
            student=str(locked.student_id),
            starts_at=starts_at.isoformat(),
        )
        base = str(settings.SYMPRAXI_BASE_URL).rstrip("/")
        transaction.on_commit(lambda: send_offer(locked.pk, base_url=base))
    return True


def _release(entry: WaitingEntry, status: str) -> tuple[Any, datetime, datetime] | None:
    """Close the entry and cancel any hold. Returns the freed time, if there was a hold."""
    freed = None
    hold = entry.hold
    if hold is not None and hold.status != AppointmentStatus.CANCELED:
        now = timezone.now()
        hold.status = AppointmentStatus.CANCELED
        hold.canceled_at = now
        hold.sequence += 1
        hold.save(update_fields=["status", "canceled_at", "sequence", "updated_at"])
        freed = (hold.assignment_id, hold.starts_at, hold.ends_at)
    entry.status = status
    entry.closed_at = timezone.now()
    entry.save(update_fields=["status", "closed_at"])
    return freed


def _after_release(freed: tuple[Any, datetime, datetime] | None) -> None:
    if freed is not None:
        transaction.on_commit(lambda: slot_opened(*freed))


# --- Taking the time ---------------------------------------------------------------------


def accept(
    entry: WaitingEntry, *, request: HttpRequest | None = None, now: datetime | None = None
) -> Appointment:
    """Turn the held time into the student's booking. Raises WaitlistError with the reason."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = (
            WaitingEntry.objects.select_for_update(of=("self",))
            .select_related("hold", "student", "schedule__center")
            .get(pk=entry.pk)
        )
        if locked.status != WaitingStatus.OFFERED or locked.hold is None:
            raise WaitlistError(_("There's no time held for you right now."))
        if locked.offer_expires_at is not None and locked.offer_expires_at <= now:
            raise WaitlistError(_("The hold on that time has run out."))
        hold = locked.hold
        hold.status = AppointmentStatus.CANCELED
        hold.canceled_at = now
        hold.sequence += 1
        hold.save(update_fields=["status", "canceled_at", "sequence", "updated_at"])
        try:
            appointment = services.book(
                actor=locked.student,
                assignment=hold.assignment,
                starts_at=hold.starts_at,
                minutes=locked.minutes,
                modality=locked.modality,
                students=[locked.student],
                as_staff=False,
                now=now,
                request=request,
                from_waiting_list=True,
            )
        except services.BookingError as problem:
            raise WaitlistError(str(problem)) from problem
        locked.status = WaitingStatus.BOOKED
        locked.appointment = appointment
        locked.closed_at = now
        locked.save(update_fields=["status", "appointment", "closed_at"])
        record(
            AuditAction.WAITLIST_ACCEPTED,
            request=request,
            actor=locked.student,
            center=locked.schedule.center,
            target=locked,
            appointment=str(appointment.pk),
        )
    # Whatever the booking left of the held time goes back to the list.
    remainder_start = appointment.ends_at
    if hold.ends_at > remainder_start:
        transaction.on_commit(
            lambda: slot_opened(hold.assignment_id, remainder_start, hold.ends_at)
        )
    return appointment


# --- The scheduler's sweep ------------------------------------------------------------------


def sweep(now: datetime | None = None) -> int:
    """Expire unanswered offers and lapse wishes for days that have passed."""
    now = now or timezone.now()
    done = 0
    for pk in list(
        WaitingEntry.objects.filter(
            status=WaitingStatus.OFFERED, offer_expires_at__lte=now
        ).values_list("pk", flat=True)
    ):
        with transaction.atomic():
            entry = (
                WaitingEntry.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("hold", "schedule__center")
                .filter(pk=pk, status=WaitingStatus.OFFERED)
                .first()
            )
            if entry is None:
                continue
            freed = _release(entry, WaitingStatus.EXPIRED)
            record(
                AuditAction.WAITLIST_EXPIRED,
                actor_label="scheduler",
                center=entry.schedule.center,
                target=entry,
                student=str(entry.student_id),
            )
        _after_release(freed)
        done += 1
    for entry in WaitingEntry.objects.filter(status=WaitingStatus.WAITING).select_related(
        "schedule__center"
    ):
        if entry.day < local_date(now, entry.schedule.tz):
            entry.status = WaitingStatus.EXPIRED
            entry.closed_at = now
            entry.save(update_fields=["status", "closed_at"])
            record(
                AuditAction.WAITLIST_EXPIRED,
                actor_label="scheduler",
                center=entry.schedule.center,
                target=entry,
                student=str(entry.student_id),
                lapsed=True,
            )
            done += 1
    return done


# --- Email -----------------------------------------------------------------------------------


def send_offer(entry_id: Any, *, base_url: str) -> int:
    entry = WaitingEntry.objects.select_related(
        "student", "hold", "hold__tutor", "schedule__center"
    ).get(pk=entry_id)
    if entry.status != WaitingStatus.OFFERED or entry.hold is None or not entry.student.is_active:
        return 0
    return mailers.default.send_messages([offer_message(entry, url_base=base_url)]) or 0


def offer_message(entry: WaitingEntry, *, url_base: str) -> EmailMessage:
    center = entry.schedule.center
    hold = entry.hold
    assert hold is not None  # noqa: S101
    with timezone.override(center.time_zone):
        context = {
            "entry": entry,
            "hold": hold,
            "center": center,
            "recipient": entry.student,
            "minutes": entry.schedule.waitlist_hold_minutes,
            "url": url_base.rstrip("/")
            + reverse("scheduling:waiting_list", kwargs={"slug": center.slug}),
            "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        }
        body = render_to_string("scheduling/email/waitlist_offer.txt", context)
        when = date_format(timezone.localtime(hold.starts_at), "M j, P")
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] "
        + _("A time opened up: %(when)s") % {"when": when},
        body=body,
        to=[entry.student.email],
        reply_to=[center.contact_email] if center.contact_email else [],
    )


def active_for(student: User, center: Any) -> Iterable[WaitingEntry]:
    return (
        WaitingEntry.objects.filter(student=student, schedule__center=center)
        .select_related("schedule", "tutor", "hold", "hold__tutor", "appointment")
        .order_by("-joined_at")
    )
