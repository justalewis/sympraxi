"""Reminders to tutors about session records they haven't written.

Each center says how long after a session its tutor is reminded
(`Center.record_reminder_hours`, a day unless a director changes it; empty
means never). The scheduler checks every minute. An appointment is due once it
ended that long ago and still has a student without a final record, counting
students who came or weren't marked, not no-shows or students who left the
group. A draft counts as not written yet.

Each tutor gets one email for everything that fell due since the last sweep,
and each appointment is mentioned once, ever: `RecordReminder` is written in
the same transaction as the email is queued, and its unique constraint means a
second scheduler, or a sweep run twice, sends nothing. Like every email to a
tutor about an appointment, it names no students: each session is listed by
its time and how many records it still needs, with a link to the appointment,
which needs a sign-in. A sweep looks back only
`LOOK_BACK` past the due time, so turning reminders on, or a scheduler that
was down for days, doesn't send a pile of old ones. Tutors whose tutor role
in the center is off get none.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ngettext

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.records.models import RecordReminder, RecordStatus, SessionRecord
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Attendance, Participant

# How long after its due time an appointment can still get a reminder.
LOOK_BACK = timedelta(days=2)


def missing(appointment: Appointment) -> list[User]:
    """The students in the appointment who still need a final record, by name.
    Uses the `active` and `written` prefetches from `due_appointments`."""
    final = {r.student_id for r in appointment.written if r.status == RecordStatus.FINAL}  # type: ignore[attr-defined]
    students = [p.student for p in appointment.active if p.student_id not in final]  # type: ignore[attr-defined]
    return sorted(students, key=lambda s: (s.display_name.lower(), str(s.pk)))


def due_appointments(center: Center, now: datetime) -> list[Appointment]:
    hours = center.record_reminder_hours
    if not hours:
        return []
    latest_end = now - timedelta(hours=hours)
    found = (
        Appointment.objects.filter(
            schedule__center=center,
            is_placeholder=False,
            ends_at__lte=latest_end,
            ends_at__gt=latest_end - LOOK_BACK,
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .exclude(record_reminders__isnull=False)
        .select_related("tutor", "schedule__center")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.filter(canceled_at__isnull=True)
                .exclude(attendance=Attendance.NO_SHOW)
                .select_related("student"),
                to_attr="active",
            ),
            Prefetch("records", queryset=SessionRecord.objects.all(), to_attr="written"),
        )
        .order_by("starts_at", "pk")
    )
    return [a for a in found if missing(a)]


def _tutors_on(center: Center) -> set[Any]:
    return set(
        Membership.objects.filter(
            center=center, role=Role.TUTOR, is_active=True, user__is_active=True
        ).values_list("user_id", flat=True)
    )


def message(
    center: Center, tutor: User, appointments: list[Appointment], *, url_base: str
) -> EmailMessage:
    base = url_base.rstrip("/")
    with timezone.override(center.time_zone):
        sessions = [
            {
                "appointment": a,
                "count": len(missing(a)),
                "url": base
                + reverse(
                    "scheduling:appointment",
                    kwargs={"slug": center.slug, "appointment_id": a.pk},
                ),
            }
            for a in appointments
        ]
        body = render_to_string(
            "records/email/record_reminder.txt",
            {
                "center": center,
                "tutor": tutor,
                "sessions": sessions,
                "to_write_url": base + reverse("records:to_write", kwargs={"slug": center.slug}),
                "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
            },
        )
    subject = ngettext(
        "A session record to write", "%(n)s session records to write", len(appointments)
    ) % {"n": len(appointments)}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[tutor.email],
        reply_to=[center.contact_email] if center.contact_email else None,
    )


def send_record_reminders(*, base_url: str, now: datetime | None = None) -> int:
    """Email each tutor about appointments newly due. Returns how many emails were queued."""
    now = now or timezone.now()
    sent = 0
    centers = Center.objects.filter(is_active=True, record_reminder_hours__isnull=False)
    for center in centers:
        due = due_appointments(center, now)
        if not due:
            continue
        tutors = _tutors_on(center)
        by_tutor: dict[Any, list[Appointment]] = defaultdict(list)
        for appointment in due:
            if appointment.tutor_id in tutors:
                by_tutor[appointment.tutor_id].append(appointment)
        for appointments in by_tutor.values():
            tutor = appointments[0].tutor
            with transaction.atomic():
                claimed = []
                for appointment in appointments:
                    try:
                        with transaction.atomic():
                            RecordReminder.objects.create(
                                appointment=appointment, tutor=tutor, sent_at=now
                            )
                    except IntegrityError:
                        continue  # another sweep got there first
                    claimed.append(appointment)
                if claimed:
                    message(center, tutor, claimed, url_base=base_url).send()
                    sent += 1
    return sent
