"""Video rooms on the installation's Jitsi server.

An installation points Sympraxi at one Jitsi server with three settings
(`SYMPRAXI_JITSI_DOMAIN`, `_APP_ID` and `_APP_SECRET`), and each center chooses
whether its online appointments get a room. Each appointment has one room, named
from a keyed hash of its ID, so the name can't be worked out from anything
people can see. Nobody enters it with just the name: the Jitsi server accepts
only people who hold a token Sympraxi signed for that room, and Sympraxi hands
the token only to the appointment's tutor and the students in it, from ten
minutes before the start to half an hour after the end. The tutor's token makes
them the moderator, who admits people from the lobby.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can
from sympraxi.online import jwt
from sympraxi.online import room as shared_room
from sympraxi.online.models import VideoSettings
from sympraxi.scheduling.models import Appointment, Modality

JOIN_EARLY = timedelta(minutes=10)
STAY_LATE = timedelta(minutes=30)

EARLY = "early"
OPEN = "open"
OVER = "over"


def configured() -> bool:
    """Whether the installation is connected to a Jitsi server."""
    return bool(
        settings.SYMPRAXI_JITSI_DOMAIN
        and settings.SYMPRAXI_JITSI_APP_ID
        and settings.SYMPRAXI_JITSI_APP_SECRET
    )


def video_on(center: Center) -> bool:
    return configured() and VideoSettings.objects.filter(center=center, is_on=True).exists()


def settings_for(center: Center) -> VideoSettings:
    found, _created = VideoSettings.objects.get_or_create(center=center)
    return found


def room_name(appointment: Appointment) -> str:
    digest = salted_hmac("sympraxi.online.room", str(appointment.pk), algorithm="sha256")
    return "sympraxi-" + digest.hexdigest()[:24]


def window(appointment: Appointment) -> tuple[datetime, datetime]:
    return appointment.starts_at - JOIN_EARLY, appointment.ends_at + STAY_LATE


def state(appointment: Appointment, now: datetime | None = None) -> str:
    now = now or timezone.now()
    opens, closes = window(appointment)
    if now < opens:
        return EARLY
    return OPEN if now <= closes else OVER


def has_room(appointment: Appointment) -> bool:
    """Whether this appointment is one a video room belongs to."""
    return (
        appointment.is_active
        and not appointment.is_placeholder
        and appointment.modality == Modality.ONLINE
        and video_on(appointment.schedule.center)
    )


def token(appointment: Appointment, user: User, *, now: datetime | None = None) -> str:
    """A token that lets this person into this appointment's room, and no other."""
    now = now or timezone.now()
    opens, closes = window(appointment)
    moderator = appointment.tutor_id == user.pk
    claims: dict[str, Any] = {
        "aud": "jitsi",
        "iss": settings.SYMPRAXI_JITSI_APP_ID,
        "sub": settings.SYMPRAXI_JITSI_DOMAIN,
        "room": room_name(appointment),
        "nbf": int(min(opens, now).timestamp()),
        "exp": int(closes.timestamp()),
        "moderator": moderator,
        "context": {
            "user": {"id": str(user.pk), "name": user.display_name, "moderator": moderator},
        },
    }
    return jwt.encode(claims, settings.SYMPRAXI_JITSI_APP_SECRET)


def join_url(appointment: Appointment, user: User, *, now: datetime | None = None) -> str:
    base = f"https://{settings.SYMPRAXI_JITSI_DOMAIN}/{quote(room_name(appointment))}"
    return base + "?" + urlencode({"jwt": token(appointment, user, now=now)})


def appointment_context(user: Any, appointment: Appointment) -> dict[str, Any]:
    """What the appointment page shows about the video room and the shared document,
    for those who may enter them."""
    context: dict[str, Any] = {}
    if has_room(appointment) and can(user, Action.JOIN_VIDEO, appointment):
        context["video"] = joining(appointment)
    if shared_room.may_use(user, appointment):
        context["shared_document"] = shared_room.state(appointment)
    return context


@dataclass
class Joining:
    state: str
    opens_at: datetime
    closes_at: datetime


def joining(appointment: Appointment, *, now: datetime | None = None) -> Joining:
    opens, closes = window(appointment)
    return Joining(state(appointment, now), opens, closes)
