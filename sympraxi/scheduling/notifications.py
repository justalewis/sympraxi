"""Appointment emails: booked, changed, confirmed, canceled, canceled late,
missed (a confirmed no-show), and reminders.

Emails carry as little as possible (build plan §6.1). A student's email names
their tutor and time; a tutor's email says a student booked, without the name,
and links to the appointment, where the tutor signs in to see who it is. No
email to a student mentions anyone else in a group. Booking emails have an .ics
file so the appointment lands in the person's calendar.

`services` and `attendance` call these after the transaction commits, so a
change that is rolled back never sends mail. Reminders are the exception: the
scheduler queues them in the same transaction that records them as sent
(`reminders`), so each goes out once.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from sympraxi.accounts.models import User
from sympraxi.scheduling import blocks, ics
from sympraxi.scheduling.models import Appointment, BlockReason, Participant

BOOKED = "booked"
BOOKED_SERIES = "booked_series"
CHANGED = "changed"
# A director moved the day's in-person appointments online (switch_online.py).
MOVED_ONLINE = "moved_online"
CONFIRMED = "confirmed"
CANCELED = "canceled"
# Sent to the student instead of CANCELED when the cancellation was late.
LATE_CANCELED = "late_canceled"


def base_url(request: HttpRequest | None) -> str:
    """Where links in emails point: the site the request came to, else the configured URL."""
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return str(settings.SYMPRAXI_BASE_URL).rstrip("/")


def duration_words(minutes: int) -> str:
    """How a window or reminder reads in an email: "30 minutes", "2 hours", "1 day"."""
    if minutes and minutes % 1440 == 0:
        days = minutes // 1440
        return ngettext("%(n)s day", "%(n)s days", days) % {"n": days}
    if minutes and minutes % 60 == 0:
        hours = minutes // 60
        return ngettext("%(n)s hour", "%(n)s hours", hours) % {"n": hours}
    return ngettext("%(n)s minute", "%(n)s minutes", minutes) % {"n": minutes}


def _url(base: str, name: str, **kwargs: Any) -> str:
    return base.rstrip("/") + reverse(name, kwargs=kwargs or None)


def _reply_to(appointment: Appointment) -> list[str]:
    email = appointment.schedule.center.contact_email
    return [email] if email else []


def _subject(kind: str, appointment: Appointment, *, for_tutor: bool) -> str:
    when = date_format(timezone.localtime(appointment.starts_at), "M j, P")
    if kind in (CANCELED, LATE_CANCELED):
        text = _("Appointment canceled: %(when)s")
    elif kind == CHANGED:
        text = _("Appointment changed: %(when)s")
    elif kind == MOVED_ONLINE:
        text = _("Appointment moved online: %(when)s")
    elif kind == CONFIRMED:
        text = _("Appointment confirmed: %(when)s")
    elif kind == BOOKED_SERIES:
        text = _("Weekly appointments booked, starting %(when)s")
    elif for_tutor:
        text = _("New appointment: %(when)s")
    elif appointment.is_tentative:
        text = _("Appointment requested: %(when)s")
    else:
        text = _("Appointment booked: %(when)s")
    return f"[{settings.SYMPRAXI_SITE_NAME}] " + text % {"when": when}


def _message(
    kind: str,
    appointment: Appointment,
    recipient: User,
    *,
    for_tutor: bool,
    series: Sequence[Appointment],
    url_base: str,
    note: str = "",
) -> EmailMessage:
    events = list(series) or [appointment]
    context = {
        "kind": kind,
        "appointment": appointment,
        "series": events if len(events) > 1 else [],
        "center": appointment.schedule.center,
        "recipient": recipient,
        "for_tutor": for_tutor,
        "url": ics.appointment_url(appointment, url_base),
        "location": ics.location(appointment),
        "note": note,
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
    }
    if kind == LATE_CANCELED:
        context["window"] = duration_words(appointment.schedule.late_cancel_minutes)
        context["standing"] = blocks.standing(recipient, appointment, BlockReason.LATE_CANCELS)
    body = render_to_string("scheduling/email/appointment.txt", context)
    message = EmailMessage(
        subject=_subject(kind, appointment, for_tutor=for_tutor),
        body=body,
        to=[recipient.email],
        reply_to=_reply_to(appointment),
    )
    method = "CANCEL" if kind in (CANCELED, LATE_CANCELED) else "PUBLISH"
    message.attach(
        "appointment.ics",
        ics.calendar(events, method=method, for_tutor=for_tutor, base_url=url_base),
        "text/calendar",
    )
    return message


def send(
    kind: str,
    appointment_id: UUID,
    *,
    base_url: str = "",
    only: Sequence[UUID] | None = None,
    tutor: bool = True,
    series: Sequence[UUID] = (),
    note: str = "",
    **_: Any,
) -> int:
    """Email the students (all active ones, or just `only`) and the tutor."""
    appointment = Appointment.objects.select_related("schedule__center", "tutor", "room").get(
        pk=appointment_id
    )
    events = list(
        Appointment.objects.filter(pk__in=series)
        .select_related("schedule__center", "tutor", "room")
        .order_by("starts_at")
    )
    if only is not None:
        students = list(User.objects.filter(pk__in=only))
    else:
        students = list(
            User.objects.filter(
                participations__appointment=appointment,
                participations__canceled_at__isnull=True,
            )
        )
    messages = []
    with timezone.override(appointment.schedule.center.time_zone):
        for student in students:
            messages.append(
                _message(
                    kind,
                    appointment,
                    student,
                    for_tutor=False,
                    series=events,
                    url_base=base_url,
                    note=note,
                )
            )
        if tutor:
            messages.append(
                _message(
                    kind,
                    appointment,
                    appointment.tutor,
                    for_tutor=True,
                    series=events,
                    url_base=base_url,
                    note=note,
                )
            )
    return mailers.default.send_messages(messages) or 0


def send_no_show(participant_id: int, *, base_url: str = "") -> int:
    """Tell a student, gently, that their appointment was recorded as missed."""
    participant = Participant.objects.select_related(
        "student",
        "appointment__schedule__center",
        "appointment__schedule__term",
        "appointment__tutor",
    ).get(pk=participant_id)
    if not participant.is_no_show or not participant.student.is_active:
        return 0
    return mailers.default.send_messages([no_show_message(participant, url_base=base_url)]) or 0


def no_show_message(participant: Participant, *, url_base: str) -> EmailMessage:
    appointment = participant.appointment
    center = appointment.schedule.center
    student = participant.student
    with timezone.override(center.time_zone):
        context = {
            "appointment": appointment,
            "center": center,
            "recipient": student,
            "standing": blocks.standing(student, appointment, BlockReason.NO_SHOWS),
            "book_url": _url(url_base, "scheduling:schedules", slug=center.slug),
            "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        }
        body = render_to_string("scheduling/email/no_show.txt", context)
        when = date_format(timezone.localtime(appointment.starts_at), "M j")
    subject = _("We missed you on %(when)s") % {"when": when}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[student.email],
        reply_to=_reply_to(appointment),
    )


def reminder_message(
    appointment: Appointment,
    recipient: User,
    *,
    for_tutor: bool,
    url_base: str,
    now: datetime,
) -> EmailMessage:
    """A reminder: when, where and a link. A tutor's names no students."""
    center = appointment.schedule.center
    cutoff = appointment.starts_at - timedelta(minutes=appointment.schedule.change_cutoff_minutes)
    with timezone.override(center.time_zone):
        context = {
            "appointment": appointment,
            "center": center,
            "recipient": recipient,
            "for_tutor": for_tutor,
            "url": ics.appointment_url(appointment, url_base),
            "location": ics.location(appointment),
            "can_cancel": now < cutoff,
            "settings_url": _url(url_base, "accounts:reminders"),
            "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        }
        body = render_to_string("scheduling/email/reminder.txt", context)
        when = date_format(timezone.localtime(appointment.starts_at), "M j, P")
    subject = _("Reminder: appointment %(when)s") % {"when": when}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[recipient.email],
        reply_to=_reply_to(appointment),
    )
