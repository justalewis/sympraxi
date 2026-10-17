"""iCalendar (RFC 5545) files for appointments, attached to emails and downloadable.

Written by hand: an appointment needs a dozen properties, and the rules that
matter (escaping, CRLF line endings, folding at 75 octets) are short.

Each appointment keeps one UID for life. Bookings and changes go out as
`METHOD:PUBLISH` with a higher `SEQUENCE`, so calendar apps update the event
they already have; cancellations go out as `METHOD:CANCEL`.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse

from sympraxi.scheduling.models import Appointment, Modality

PRODID = "-//Sympraxi//Scheduling//EN"


def escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Split a content line into pieces of at most 75 octets, without splitting a character."""
    encoded = line.encode()
    if len(encoded) <= 75:
        return line
    pieces: list[str] = []
    current = ""
    limit = 75
    for char in line:
        if len((current + char).encode()) > limit:
            pieces.append(current)
            current = char
            limit = 74  # continuation lines start with a space
        else:
            current += char
    pieces.append(current)
    return "\r\n ".join(pieces)


def _stamp(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def uid(appointment: Appointment, base_url: str = "") -> str:
    host = urlsplit(base_url).hostname or "sympraxi.invalid"
    return f"{appointment.pk}@{host}"


def appointment_url(appointment: Appointment, base_url: str = "") -> str:
    path = reverse(
        "scheduling:appointment",
        kwargs={"slug": appointment.schedule.center.slug, "appointment_id": appointment.pk},
    )
    return base_url.rstrip("/") + path


def summary(appointment: Appointment, *, for_tutor: bool) -> str:
    center = appointment.schedule.center.name
    if for_tutor:
        return f"{center}: tutoring appointment"
    return f"{center}: appointment with {appointment.tutor.display_name}"


def location(appointment: Appointment) -> str:
    if appointment.modality == Modality.ONLINE:
        return appointment.meeting_url or "Online"
    if appointment.room is not None:
        return ", ".join(p for p in (appointment.room.name, appointment.room.details) if p)
    return appointment.schedule.center.name


def status(appointment: Appointment) -> str:
    if not appointment.is_active:
        return "CANCELLED"
    return "TENTATIVE" if appointment.is_tentative else "CONFIRMED"


def calendar(
    appointments: Iterable[Appointment],
    *,
    method: str = "PUBLISH",
    for_tutor: bool = False,
    base_url: str = "",
    now: datetime | None = None,
) -> str:
    return calendar_of(
        ((a, for_tutor) for a in appointments), method=method, base_url=base_url, now=now
    )


def calendar_of(
    appointments: Iterable[tuple[Appointment, bool]],
    *,
    method: str = "PUBLISH",
    base_url: str = "",
    now: datetime | None = None,
    name: str = "",
) -> str:
    """A calendar of (appointment, whether the reader is its tutor) pairs. `name` is
    what calendar apps call a subscribed calendar."""
    now = now or datetime.now(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
    ]
    if name:
        lines.append(f"X-WR-CALNAME:{escape(name)}")
    for appointment, for_tutor in appointments:
        url = appointment_url(appointment, base_url)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid(appointment, base_url)}",
            f"DTSTAMP:{_stamp(now)}",
            f"DTSTART:{_stamp(appointment.starts_at)}",
            f"DTEND:{_stamp(appointment.ends_at)}",
            f"SEQUENCE:{appointment.sequence}",
            f"STATUS:{status(appointment)}",
            f"SUMMARY:{escape(summary(appointment, for_tutor=for_tutor))}",
            f"LOCATION:{escape(location(appointment))}",
            f"DESCRIPTION:{escape(f'Details: {url}')}",
            f"URL:{url}",
        ]
        if method == "CANCEL":
            organizer = appointment.schedule.center.contact_email or settings.DEFAULT_FROM_EMAIL
            name = escape(appointment.schedule.center.name).replace('"', "'")
            lines.append(f'ORGANIZER;CN="{name}":mailto:{organizer}')
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
