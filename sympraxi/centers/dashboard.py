"""What the center's front page shows each person: numbers and next things, not only links.

A student sees their next appointment and what they were last working on. Tutors see
their own day; staff who can see every appointment see the center's day, and a row of
counts for what needs someone (records to write, possible no-shows, open alerts,
bookings waiting to be confirmed). Every count is scoped to what the person's role
may see, with the same checks the pages behind the links use.
"""

import zoneinfo
from datetime import datetime, time, timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.alerts.models import Alert, AlertStatus
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can
from sympraxi.records.models import RecordStatus, SessionRecord
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Participant,
)
from sympraxi.workshops.models import Workshop, WorkshopStatus

TODAY_ROWS = 12
RECORD_DAYS = 14


def _day_bounds(center: Center) -> tuple[datetime, datetime]:
    tz = zoneinfo.ZoneInfo(center.time_zone)
    today = timezone.localdate(timezone=tz)
    start = datetime.combine(today, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _live(center: Center) -> Any:
    return Appointment.objects.filter(schedule__center=center, is_placeholder=False).exclude(
        status=AppointmentStatus.CANCELED
    )


def build(user: User, center: Center) -> dict[str, Any]:
    """The dashboard's panels for this person. Empty keys mean the panel isn't shown."""
    panels: dict[str, Any] = {}
    now = timezone.now()
    day_start, day_end = _day_bounds(center)
    sees_all = can(user, Action.VIEW_ALL_APPOINTMENTS, center)
    is_tutor = can(user, Action.VIEW_OWN_TUTORING, center)

    if can(user, Action.BOOK_OWN, center):
        panels["student"] = _student(user, center, now)

    if sees_all or is_tutor:
        scope = _live(center)
        if not sees_all:
            scope = scope.filter(tutor=user)
        today = list(
            scope.filter(starts_at__gte=day_start, starts_at__lt=day_end)
            .select_related("tutor")
            .prefetch_related("participants__student")
            .order_by("starts_at")
        )
        panels["today_title"] = "Today's sessions" if sees_all else "Your sessions today"
        panels["today"] = [_row(a, now) for a in today[:TODAY_ROWS]]
        panels["today_more"] = max(0, len(today) - TODAY_ROWS)
        week_end = day_start + timedelta(days=7)
        tiles = [
            {"label": "Sessions today", "value": len(today)},
            {
                "label": "Booked, next 7 days",
                "value": scope.filter(starts_at__gte=now, starts_at__lt=week_end).count(),
            },
        ]
        waiting = scope.filter(starts_at__gte=now, is_tentative=True).count()
        if waiting:
            tiles.append({"label": "Waiting for confirmation", "value": waiting, "alert": True})
        if can(user, Action.VIEW_RECORD_STATUS, center):
            tiles.append(_to_write(scope, now))
        if can(user, Action.MARK_ATTENDANCE, center):
            tiles.append(_to_check(center, user, sees_all))
        if can(user, Action.HANDLE_ALERTS, center):
            open_alerts = Alert.objects.filter(
                center=center, status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]
            ).count()
            tiles.append(
                {"label": "Open early alerts", "value": open_alerts, "alert": bool(open_alerts)}
            )
        panels["tiles"] = [t for t in tiles if t]
        if sees_all:
            panels["month"] = _month(center, now)

    if can(user, Action.VIEW_WORKSHOPS, center):
        panels["workshops"] = list(
            Workshop.objects.filter(
                center=center, status=WorkshopStatus.PUBLISHED, starts_at__gte=now
            ).order_by("starts_at")[:3]
        )
    return panels


def _row(appointment: Appointment, now: datetime) -> dict[str, Any]:
    people = [
        p.student.full_name or p.student.email
        for p in appointment.participants.all()
        if p.canceled_at is None
    ]
    if appointment.ends_at <= now:
        state = "done"
    elif appointment.starts_at <= now:
        state = "now"
    else:
        state = "next"
    return {
        "appointment": appointment,
        "people": ", ".join(people) or "Open",
        "state": state,
        "tentative": appointment.is_tentative,
    }


def _to_write(scope: Any, now: datetime) -> dict[str, Any]:
    done = SessionRecord.objects.filter(status=RecordStatus.FINAL).values("appointment_id")
    count = (
        scope.filter(starts_at__lte=now, starts_at__gte=now - timedelta(days=RECORD_DAYS))
        .filter(
            participants__canceled_at__isnull=True,
        )
        .exclude(participants__attendance=Attendance.NO_SHOW)
        .exclude(pk__in=done)
        .distinct()
        .count()
    )
    return {
        "label": "Records to write",
        "value": count,
        "alert": bool(count),
        "link": "records:to_write",
    }


def _to_check(center: Center, user: User, sees_all: bool) -> dict[str, Any]:
    flagged = Participant.objects.filter(
        appointment__schedule__center=center,
        flagged_at__isnull=False,
        flag_cleared_at__isnull=True,
        attendance=Attendance.UNMARKED,
        canceled_at__isnull=True,
    ).exclude(appointment__status=AppointmentStatus.CANCELED)
    if not sees_all:
        flagged = flagged.filter(appointment__tutor=user)
    count = flagged.count()
    return {
        "label": "Attendance to check",
        "value": count,
        "alert": bool(count),
        "link": "scheduling:attendance_list",
    }


def _month(center: Center, now: datetime) -> dict[str, Any]:
    """The last 30 days at a glance, for the people who run the center."""
    since = now - timedelta(days=30)
    held = SessionRecord.objects.filter(
        center=center, status=RecordStatus.FINAL, held_at__gte=since
    )
    sessions = held.count()
    return {
        "sessions": sessions,
        "students": held.values("student").distinct().count(),
        "hours": round(sum(held.values_list("minutes", flat=True)) / 60, 1),
        "booked": _live(center).filter(starts_at__gte=since, starts_at__lt=now).count(),
    }


def _student(user: User, center: Center, now: datetime) -> dict[str, Any]:
    mine = Participant.objects.filter(
        student=user, appointment__schedule__center=center, canceled_at__isnull=True
    ).exclude(appointment__status=AppointmentStatus.CANCELED)
    upcoming = (
        mine.filter(appointment__starts_at__gte=now)
        .select_related("appointment__tutor")
        .order_by("appointment__starts_at")[:3]
    )
    last = (
        SessionRecord.objects.filter(center=center, student=user, status=RecordStatus.FINAL)
        .filter(Q(summary__gt="") | Q(next_steps__gt=""))
        .select_related("tutor", "project")
        .order_by("-held_at")
        .first()
    )
    return {"upcoming": [p.appointment for p in upcoming], "last": last}
