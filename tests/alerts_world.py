"""Builders for early alert and shared visit tests."""

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from sympraxi.alerts.models import Alert, AlertKind, VisitShare
from sympraxi.scheduling.models import Participant
from tests.scheduling_world import World


def add_alerts(world: World) -> World:
    """Give a world `alert` (raised by the tutor about the student) and a live share link
    (`share`, whose link text is `share_token`) for the student's appointment."""
    center = world.center
    center.visit_sharing_enabled = True
    center.save(update_fields=["visit_sharing_enabled"])
    world.alert = Alert.objects.create(  # type: ignore[attr-defined]
        center=center,
        student=world.student,
        raised_by=world.tutor,
        kind=AlertKind.ATTENDANCE,
        reason="Has missed the last three sessions without a word.",
    )
    token = secrets.token_urlsafe(24)
    participant = Participant.objects.get(appointment=world.appointment, student=world.student)
    world.share = VisitShare.objects.create(  # type: ignore[attr-defined]
        participant=participant,
        center=center,
        student=world.student,
        instructor_email="prof@example.edu",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=30),
    )
    world.share_token = token  # type: ignore[attr-defined]
    return world
