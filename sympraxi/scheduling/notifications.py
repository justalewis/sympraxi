"""Appointment emails: booked, changed, confirmed, canceled.

Emails carry as little as possible (build plan §6.1). A student's email names
their tutor and time; a tutor's email says a student booked, without the name,
and links to the appointment, where the tutor signs in to see who it is. Each
email has an .ics file so the appointment lands in the person's calendar.

`services` calls `send()` after the transaction commits, so a booking that is
rolled back never sends mail.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.scheduling import ics
from sympraxi.scheduling.models import Appointment

BOOKED = "booked"
BOOKED_SERIES = "booked_series"
CHANGED = "changed"
CONFIRMED = "confirmed"
CANCELED = "canceled"


def base_url(request: HttpRequest | None) -> str:
    """Where links in emails point: the site the request came to, else the configured URL."""
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return str(settings.SYMPRAXI_BASE_URL).rstrip("/")


def _subject(kind: str, appointment: Appointment, *, for_tutor: bool) -> str:
    when = date_format(timezone.localtime(appointment.starts_at), "M j, P")
    if kind == CANCELED:
        text = _("Appointment canceled: %(when)s")
    elif kind == CHANGED:
        text = _("Appointment changed: %(when)s")
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
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
    }
    body = render_to_string("scheduling/email/appointment.txt", context)
    message = EmailMessage(
        subject=_subject(kind, appointment, for_tutor=for_tutor),
        body=body,
        to=[recipient.email],
        reply_to=[appointment.schedule.center.contact_email]
        if appointment.schedule.center.contact_email
        else [],
    )
    method = "CANCEL" if kind == CANCELED else "PUBLISH"
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
                    kind, appointment, student, for_tutor=False, series=events, url_base=base_url
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
                )
            )
    return mailers.default.send_messages(messages) or 0
