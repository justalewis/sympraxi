"""Registering for workshops, taking attendance, and canceling a workshop.

- A student registers while the workshop is published and hasn't started, and while a
  seat is free. Seats are counted with the workshop row locked, so two students
  can't take the last one together.
- A student (or staff for them) can cancel a registration until the workshop starts;
  signing up again after canceling restores the same row.
- Anyone who runs the roster marks each student as having come or not, once the
  workshop has started. Marking again changes it; marking "not marked" clears it.
- Canceling a workshop tells everyone registered, and it can't be undone.
"""

from collections.abc import Iterable
from functools import partial
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.db import transaction
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.workshops.models import Attendance, Registration, Workshop, WorkshopStatus


class WorkshopError(Exception):
    """A rule refused the request; the message is written for the person who asked."""


def _is_student(workshop: Workshop, student: User) -> bool:
    return Membership.objects.filter(
        user=student, center=workshop.center, role=Role.STUDENT, is_active=True
    ).exists()


def register(
    workshop: Workshop, student: User, *, by: User, request: HttpRequest | None = None
) -> Registration:
    now = timezone.now()
    with transaction.atomic():
        locked = Workshop.objects.select_for_update().select_related("center").get(pk=workshop.pk)
        if locked.status != WorkshopStatus.PUBLISHED:
            raise WorkshopError(_("This workshop isn't open for registration."))
        if locked.starts_at <= now:
            raise WorkshopError(_("This workshop has already started."))
        if not _is_student(locked, student):
            raise WorkshopError(_("Only students in this center can register."))
        existing = Registration.objects.filter(workshop=locked, student=student).first()
        if existing is not None and existing.is_active:
            raise WorkshopError(
                _("%(name)s is already registered.") % {"name": student.display_name}
            )
        if locked.capacity is not None and locked.taken() >= locked.capacity:
            raise WorkshopError(_("This workshop is full."))
        if existing is None:
            registration = Registration.objects.create(
                workshop=locked, student=student, registered_by=by
            )
        else:
            existing.canceled_at = None
            existing.attendance = Attendance.UNMARKED
            existing.marked_at = existing.marked_by = None
            existing.registered_at = now
            existing.registered_by = by
            existing.save()
            registration = existing
        record(
            AuditAction.WORKSHOP_REGISTERED,
            request=request,
            actor=by,
            center=locked.center,
            target=registration,
            workshop=str(locked.pk),
            student=str(student.pk),
        )
        base = _base_url(request)
        transaction.on_commit(lambda: send_confirmation(registration.pk, base_url=base))
    return registration


def unregister(registration: Registration, *, by: User, request: HttpRequest | None = None) -> bool:
    with transaction.atomic():
        locked = (
            Registration.objects.select_for_update(of=("self",))
            .select_related("workshop__center")
            .get(pk=registration.pk)
        )
        if not locked.is_active:
            return False
        if locked.workshop.starts_at <= timezone.now():
            raise WorkshopError(_("This workshop has already started."))
        locked.canceled_at = timezone.now()
        locked.save(update_fields=["canceled_at"])
        record(
            AuditAction.WORKSHOP_UNREGISTERED,
            request=request,
            actor=by,
            center=locked.workshop.center,
            target=locked,
            workshop=str(locked.workshop_id),
            student=str(locked.student_id),
        )
    return True


def mark(
    registration: Registration, attendance: str, *, by: User, request: HttpRequest | None = None
) -> None:
    if attendance not in {a.value for a in Attendance}:
        raise WorkshopError(_("Choose came, did not come, or not marked."))
    with transaction.atomic():
        locked = (
            Registration.objects.select_for_update(of=("self",))
            .select_related("workshop__center")
            .get(pk=registration.pk)
        )
        if not locked.is_active:
            raise WorkshopError(_("That registration was canceled."))
        if locked.workshop.is_canceled:
            raise WorkshopError(_("This workshop was canceled."))
        if locked.workshop.starts_at > timezone.now():
            raise WorkshopError(_("Attendance can be marked once the workshop has started."))
        locked.attendance = attendance
        locked.marked_by = by if attendance else None
        locked.marked_at = timezone.now() if attendance else None
        locked.save(update_fields=["attendance", "marked_by", "marked_at"])
        record(
            AuditAction.WORKSHOP_ATTENDANCE_MARKED,
            request=request,
            actor=by,
            center=locked.workshop.center,
            target=locked,
            workshop=str(locked.workshop_id),
            student=str(locked.student_id),
            attendance=attendance or "cleared",
        )


def cancel_workshop(
    workshop: Workshop, *, reason: str, by: User, request: HttpRequest | None = None
) -> bool:
    with transaction.atomic():
        locked = Workshop.objects.select_for_update().select_related("center").get(pk=workshop.pk)
        if locked.is_canceled:
            return False
        was_published = locked.is_published
        locked.status = WorkshopStatus.CANCELED
        locked.canceled_at = timezone.now()
        locked.cancel_reason = reason
        locked.save(update_fields=["status", "canceled_at", "cancel_reason"])
        registered = list(
            locked.registrations.filter(canceled_at__isnull=True).values_list("pk", flat=True)
        )
        record(
            AuditAction.WORKSHOP_CANCELED,
            request=request,
            actor=by,
            center=locked.center,
            target=locked,
            registered=len(registered),
            reason=reason,
        )
        base = _base_url(request)
        if was_published:
            for pk in registered:
                transaction.on_commit(partial(send_canceled, pk, base_url=base))
    return True


# --- Email -----------------------------------------------------------------------------------


def _base_url(request: HttpRequest | None) -> str:
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return str(settings.SYMPRAXI_BASE_URL).rstrip("/")


def _message(
    registration: Registration, template: str, subject: str, base_url: str
) -> EmailMessage:
    workshop = registration.workshop
    center = workshop.center
    with timezone.override(center.time_zone):
        context: dict[str, Any] = {
            "workshop": workshop,
            "center": center,
            "recipient": registration.student,
            "url": base_url.rstrip("/")
            + reverse("workshops:detail", kwargs={"slug": center.slug, "workshop_id": workshop.pk}),
            "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        }
        body = render_to_string(template, context)
        when = date_format(timezone.localtime(workshop.starts_at), "M j, P")
    filled = subject % {"when": when, "title": workshop.title}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {filled}",
        body=body,
        to=[registration.student.email],
        reply_to=[center.contact_email] if center.contact_email else [],
    )


def send_confirmation(registration_id: Any, *, base_url: str) -> int:
    registration = Registration.objects.select_related(
        "student", "workshop", "workshop__center", "workshop__leader"
    ).get(pk=registration_id)
    if not registration.is_active or not registration.student.is_active:
        return 0
    message = _message(
        registration,
        "workshops/email/registered.txt",
        _("You're registered: %(title)s, %(when)s"),
        base_url,
    )
    return mailers.default.send_messages([message]) or 0


def send_canceled(registration_id: Any, *, base_url: str) -> int:
    registration = Registration.objects.select_related(
        "student", "workshop", "workshop__center"
    ).get(pk=registration_id)
    if not registration.student.is_active:
        return 0
    message = _message(
        registration,
        "workshops/email/canceled.txt",
        _("Canceled: %(title)s, %(when)s"),
        base_url,
    )
    return mailers.default.send_messages([message]) or 0


def roster(workshop: Workshop) -> Iterable[Registration]:
    return workshop.registrations.filter(canceled_at__isnull=True).select_related("student")
