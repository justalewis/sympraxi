"""The files section of the appointment page."""

from typing import Any

from django.http import HttpRequest

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, can
from sympraxi.files.models import AppointmentFile
from sympraxi.scheduling.models import Appointment


def students_for(user: Any, appointment: Appointment) -> list[User]:
    """Whom a file can be for: a student attaches to themselves; staff, to any student in it."""
    active = [p.student for p in appointment.active_participants().select_related("student")]
    if any(s.pk == user.pk for s in active):
        return [s for s in active if s.pk == user.pk]
    return sorted(active, key=lambda s: s.display_name.lower())


def visible_files(user: Any, appointment: Appointment) -> list[AppointmentFile]:
    found = (
        AppointmentFile.objects.filter(appointment=appointment)
        .select_related("file", "file__uploaded_by", "student", "appointment__schedule__center")
        .order_by("file__uploaded_at")
    )
    return [f for f in found if can(user, Action.VIEW_FILES, f)]


def appointment_context(request: HttpRequest, appointment: Appointment) -> dict[str, Any]:
    """What the appointment page shows about files."""
    user = request.user
    if not can(user, Action.VIEW_FILES, appointment):
        return {}
    return {
        "files": visible_files(user, appointment),
        "may_attach": appointment.is_active
        and can(user, Action.ATTACH_FILES, appointment)
        and bool(students_for(user, appointment)),
        "shows_files": True,
    }
