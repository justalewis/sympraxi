"""Reminder emails before appointments, sent by the scheduler every minute.

Each schedule sets up to three reminders ("24 hours before", "1 hour before").
A reminder is due at the appointment's start minus its lead time, measured in
real time like notice and cutoff: a 24-hour reminder across a daylight-saving
change goes out 24 hours before, which on the clock is 23 or 25 hours earlier.
The due time comes from the appointment's current start, so a moved
appointment's reminders follow it.

The rules, each checked again with the appointment locked:

- Only scheduled appointments get reminders: not canceled, tentative or
  placeholder ones, and never once the appointment has started.
- A reminder due more than `CATCH_UP` ago is skipped, so a scheduler that was
  down sends at most the reminders that are still close to on time, never a
  stack of stale ones.
- A reminder that fell due before the appointment was booked, moved or
  confirmed is skipped; the confirmation email just went out. So is a student's
  reminder that fell due before they joined a group.
- Students get them unless they turned them off; tutors only if they turned
  them on (`User.reminders_for_appointments`, `User.reminders_for_tutoring`).

Each reminder is written to `Reminder` and queued for the mail worker in one
transaction. The unique constraint on `Reminder` means the second of two
schedulers running at once, or a sweep run twice, inserts nothing and sends
nothing. Locking the appointment row makes a reminder wait for a cancellation
or move that is committing at that moment, so it sees the result.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from django.core.mail import mailers
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.scheduling import notifications
from sympraxi.scheduling.models import REMINDER_CHOICES, Appointment, AppointmentStatus, Reminder
from sympraxi.sms import service as sms

logger = logging.getLogger("sympraxi.scheduler")

# How late a reminder may still go out. Shorter than the shortest gap between
# two reminder choices (30 minutes), so two reminders for one appointment are
# never sent in the same sweep.
CATCH_UP = timedelta(minutes=15)


def send_due_reminders(*, base_url: str, now: datetime | None = None) -> int:
    """Queue every reminder due now. Returns how many emails were queued."""
    now = now or timezone.now()
    sent = 0
    for minutes in sorted({m for m, _ in REMINDER_CHOICES}):
        lead = timedelta(minutes=minutes)
        due = (
            Appointment.objects.filter(
                status=AppointmentStatus.SCHEDULED,
                is_placeholder=False,
                is_tentative=False,
                starts_at__gt=max(now, now - CATCH_UP + lead),
                starts_at__lte=now + lead,
            )
            .filter(
                Q(schedule__reminder_1_minutes=minutes)
                | Q(schedule__reminder_2_minutes=minutes)
                | Q(schedule__reminder_3_minutes=minutes)
            )
            .order_by("starts_at", "pk")
            .values_list("pk", flat=True)
        )
        for appointment_id in due:
            try:
                sent += remind(appointment_id, minutes, base_url=base_url, now=now)
            except Exception:
                # One bad appointment mustn't stop the others. It's tried again
                # next minute, while it's still within the catch-up window.
                logger.exception("Reminder for appointment %s failed", appointment_id)
    return sent


def remind(appointment_id: UUID, minutes: int, *, base_url: str, now: datetime) -> int:
    """Send one appointment's reminder for one lead time, to everyone who should get it."""
    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update(of=("self",))
            .select_related("schedule__center", "tutor", "room")
            .get(pk=appointment_id)
        )
        due_at = appointment.starts_at - timedelta(minutes=minutes)
        if (
            not appointment.is_active
            or appointment.is_placeholder
            or appointment.is_tentative
            or minutes not in appointment.schedule.reminder_offsets()
            or appointment.starts_at <= now
            or not (now - CATCH_UP < due_at <= now)
            or appointment.updated_at >= due_at
        ):
            return 0
        recipients: list[tuple[User, bool]] = [
            (p.student, False)
            for p in appointment.participants.filter(
                canceled_at__isnull=True,
                added_at__lt=due_at,
                student__is_active=True,
                student__reminders_for_appointments=True,
            ).select_related("student")
        ]
        tutor = appointment.tutor
        if tutor.is_active and tutor.reminders_for_tutoring:
            recipients.append((tutor, True))
        messages = []
        for user, for_tutor in recipients:
            try:
                with transaction.atomic():
                    Reminder.objects.create(
                        appointment=appointment,
                        recipient=user,
                        minutes_before=minutes,
                        starts_at=appointment.starts_at,
                        due_at=due_at,
                        sent_at=now,
                    )
            except IntegrityError:
                continue  # Already sent.
            messages.append(
                notifications.reminder_message(
                    appointment, user, for_tutor=for_tutor, url_base=base_url, now=now
                )
            )
        # The queued mailer writes each message to the task table in this same
        # transaction, so a reminder is recorded as sent exactly when it's queued.
        if messages:
            mailers.default.send_messages(messages)
        # A text goes out only for the last reminder of the day, so a person who chose
        # texts isn't sent one for every email.
        if minutes == min(appointment.schedule.reminder_offsets()):
            for user, _for_tutor in recipients:
                sms.queue_reminder(user, appointment, now=now)
    return len(messages)
