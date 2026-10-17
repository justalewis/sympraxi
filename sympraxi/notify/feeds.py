"""Private calendar feeds: a secret address a calendar app subscribes to.

A feed lists the person's appointments from a month ago to six months ahead:
those they booked as a student and those they teach as a tutor, in every center
where that role is on. Canceled appointments drop out, and calendar apps remove
them on their next fetch.

The address is a bearer secret. Anyone who has it sees the times and places of
the person's appointments, so:

- only a hash of the token is stored (`CalendarFeed.token_hash`);
- the token never reaches the logs (`sympraxi/core/logs.py` hides it);
- unknown, replaced and turned-off addresses, and accounts that were turned off,
  all get the same 404, so addresses can't be probed;
- events never name the student. Calendar apps keep their copy on the
  provider's servers (Google's, Microsoft's, Apple's), so a tutor's feed says
  "tutoring appointment" and links to the page in Sympraxi, which needs a sign-in.
"""

import re
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, centers_for
from sympraxi.notify.models import CalendarFeed, feed_token_hash
from sympraxi.scheduling import ics
from sympraxi.scheduling.models import Appointment, AppointmentStatus

# What `secrets.token_urlsafe(32)` makes. Anything else is refused before hashing.
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{40,50}")
# The window of appointments a feed lists.
PAST = timedelta(days=31)
AHEAD = timedelta(days=183)
# How often a feed's "last used" time is written, at most.
USE_GRANULARITY = timedelta(hours=1)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def start_feed(user: User) -> str:
    """Make the person's feed, or give it a new address. Returns the new token,
    which is shown once and never stored."""
    token = new_token()
    with transaction.atomic():
        CalendarFeed.objects.update_or_create(
            user=user,
            defaults={"token_hash": feed_token_hash(token), "last_used_at": None},
        )
    return token


def stop_feed(user: User) -> bool:
    deleted, _ = CalendarFeed.objects.filter(user=user).delete()
    return bool(deleted)


def open_feed(token: str, *, now: datetime | None = None) -> CalendarFeed | None:
    """The feed a token opens, or None. Notes when it was last used, once an hour."""
    if not TOKEN_SHAPE.fullmatch(token):
        return None
    feed = (
        CalendarFeed.objects.select_related("user")
        .filter(token_hash=feed_token_hash(token), user__is_active=True)
        .first()
    )
    if feed is None:
        return None
    now = now or timezone.now()
    if feed.last_used_at is None or now - feed.last_used_at >= USE_GRANULARITY:
        CalendarFeed.objects.filter(pk=feed.pk).update(last_used_at=now)
    return feed


def appointments_for(user: User, *, now: datetime | None = None) -> list[tuple[Appointment, bool]]:
    """(appointment, as their tutor) for everything the feed lists, earliest first."""
    now = now or timezone.now()
    window = {
        "starts_at__gte": now - PAST,
        "starts_at__lt": now + AHEAD,
    }
    base = (
        Appointment.objects.exclude(status=AppointmentStatus.CANCELED)
        .filter(**window)
        .select_related("schedule__center", "tutor", "room")
    )
    booked = base.filter(
        schedule__center__in=centers_for(user, Action.BOOK_OWN),
        participants__student=user,
        participants__canceled_at__isnull=True,
    ).distinct()
    taught = base.filter(
        schedule__center__in=centers_for(user, Action.VIEW_OWN_TUTORING), tutor=user
    )
    found = {a.pk: (a, False) for a in booked}
    for appointment in taught:
        found[appointment.pk] = (appointment, True)
    return sorted(found.values(), key=lambda pair: (pair[0].starts_at, str(pair[0].pk)))


def feed_body(user: User, *, base_url: str, now: datetime | None = None) -> str:
    return ics.calendar_of(
        appointments_for(user, now=now),
        base_url=base_url,
        now=now,
        name=f"{settings.SYMPRAXI_SITE_NAME} appointments",
    )
