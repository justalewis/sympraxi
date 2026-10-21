"""Raising and handling alerts; sharing, revoking and opening visit confirmations."""

import hashlib
import secrets
from datetime import timedelta
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
from sympraxi.alerts.models import Alert, AlertKind, AlertStatus, VisitShare
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Participant

MIN_REASON = 15
SHARE_DAYS = 120


class AlertError(Exception):
    """A rule refused the request; the message is written for the person who asked."""


# --- Alerts ----------


def raise_alert(
    center: Center,
    student: User,
    by: User,
    *,
    kind: str,
    reason: str,
    student_informed: bool,
    request: HttpRequest | None = None,
) -> Alert:
    reason = reason.strip()
    if kind not in AlertKind.values:
        raise AlertError(_("Choose what the alert is about."))
    if len(reason) < MIN_REASON:
        raise AlertError(
            _("Say why you're raising it, in at least %(n)s characters.") % {"n": MIN_REASON}
        )
    if not Membership.objects.filter(
        user=student, center=center, role=Role.STUDENT, is_active=True
    ).exists():
        raise AlertError(_("That person isn't a student in this center."))
    with transaction.atomic():
        alert = Alert.objects.create(
            center=center,
            student=student,
            raised_by=by,
            kind=kind,
            reason=reason,
            student_informed=student_informed,
        )
        record(
            AuditAction.ALERT_RAISED,
            request=request,
            actor=by,
            center=center,
            target=alert,
            student=str(student.pk),
            kind=kind,
            student_informed=student_informed,
        )
        base = _base(request)
        transaction.on_commit(lambda: notify(alert.pk, base_url=base))
    return alert


def acknowledge(alert: Alert, by: User, *, request: HttpRequest | None = None) -> bool:
    with transaction.atomic():
        locked = Alert.objects.select_for_update().get(pk=alert.pk)
        if locked.status != AlertStatus.OPEN:
            return False
        locked.status = AlertStatus.ACKNOWLEDGED
        locked.acknowledged_by = by
        locked.acknowledged_at = timezone.now()
        locked.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])
        record(
            AuditAction.ALERT_HANDLED,
            request=request,
            actor=by,
            center=locked.center,
            target=locked,
            what="acknowledged",
        )
    return True


def close(alert: Alert, by: User, *, outcome: str, request: HttpRequest | None = None) -> bool:
    outcome = outcome.strip()
    if len(outcome) < 5:
        raise AlertError(_("Say what was done before closing it."))
    with transaction.atomic():
        locked = Alert.objects.select_for_update().get(pk=alert.pk)
        if locked.is_closed:
            return False
        locked.status = AlertStatus.CLOSED
        locked.closed_by = by
        locked.closed_at = timezone.now()
        locked.outcome = outcome
        locked.save(update_fields=["status", "closed_by", "closed_at", "outcome"])
        record(
            AuditAction.ALERT_HANDLED,
            request=request,
            actor=by,
            center=locked.center,
            target=locked,
            what="closed",
        )
    return True


def _base(request: HttpRequest | None) -> str:
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return str(settings.SYMPRAXI_BASE_URL).rstrip("/")


def notify(alert_id: Any, *, base_url: str) -> int:
    """Tell the center's alert address, or its directors, that one was raised.

    The email names the student and the kind of alert and links to the page. The reason is
    written only on the page, which needs a sign-in.
    """
    alert = Alert.objects.select_related("center", "student", "raised_by").get(pk=alert_id)
    center = alert.center
    if center.alert_email:
        recipients = [center.alert_email]
    else:
        recipients = list(
            User.objects.filter(
                memberships__center=center,
                memberships__role=Role.DIRECTOR,
                memberships__is_active=True,
                is_active=True,
            )
            .distinct()
            .values_list("email", flat=True)
        )
    if not recipients:
        return 0
    with timezone.override(center.time_zone):
        body = render_to_string(
            "alerts/email/raised.txt",
            {
                "alert": alert,
                "center": center,
                "url": base_url.rstrip("/")
                + reverse("alerts:detail", kwargs={"slug": center.slug, "alert_id": alert.pk}),
                "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
            },
        )
    message = EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] "
        + _("Early alert: %(kind)s") % {"kind": alert.get_kind_display()},
        body=body,
        to=recipients,
    )
    return mailers.default.send_messages([message]) or 0


# --- Shared visits ----------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def share_visit(
    participant: Participant,
    *,
    instructor_email: str,
    course: str,
    request: HttpRequest | None = None,
) -> tuple[VisitShare, str]:
    """Make the link and email it to the instructor. Returns the share and the link's token."""
    center = participant.appointment.schedule.center
    if not center.visit_sharing_enabled:
        raise AlertError(_("This center doesn't offer shared visit confirmations."))
    if participant.canceled_at is not None or participant.appointment.is_placeholder:
        raise AlertError(_("There's no visit to confirm for that appointment."))
    if participant.appointment.starts_at > timezone.now():
        raise AlertError(_("You can share a confirmation once the visit has happened."))
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        share = VisitShare.objects.create(
            participant=participant,
            center=center,
            student=participant.student,
            instructor_email=instructor_email.strip().lower(),
            course=course.strip(),
            token_hash=_hash(token),
            expires_at=timezone.now() + timedelta(days=SHARE_DAYS),
        )
        record(
            AuditAction.VISIT_SHARED,
            request=request,
            actor=participant.student,
            center=center,
            target=share,
            appointment=str(participant.appointment_id),
        )
        base = _base(request)
        transaction.on_commit(lambda: send_share(share.pk, token, base_url=base))
    return share, token


def send_share(share_id: Any, token: str, *, base_url: str) -> int:
    share = VisitShare.objects.select_related("student", "center").get(pk=share_id)
    if not share.is_usable:
        return 0
    with timezone.override(share.center.time_zone):
        body = render_to_string(
            "alerts/email/shared.txt",
            {
                "share": share,
                "center": share.center,
                "student": share.student,
                "url": base_url.rstrip("/")
                + reverse("visit:confirmation", kwargs={"token": token}),
                "days": SHARE_DAYS,
                "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
            },
        )
    message = EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] "
        + _("%(name)s sent you a visit confirmation") % {"name": share.student.display_name},
        body=body,
        to=[share.instructor_email],
        reply_to=[share.student.email],
    )
    return mailers.default.send_messages([message]) or 0


def revoke_share(share: VisitShare, by: User, *, request: HttpRequest | None = None) -> bool:
    if share.revoked_at is not None:
        return False
    share.revoked_at = timezone.now()
    share.save(update_fields=["revoked_at"])
    record(
        AuditAction.VISIT_SHARE_REVOKED,
        request=request,
        actor=by,
        center=share.center,
        target=share,
    )
    return True


def open_share(token: str) -> VisitShare | None:
    """The usable share for a link, counting the opening. None for any unusable link."""
    if not token or len(token) > 100:
        return None
    share = (
        VisitShare.objects.select_related(
            "student", "center", "participant__appointment", "participant__appointment__schedule"
        )
        .filter(token_hash=_hash(token))
        .first()
    )
    if share is None or not share.is_usable or not share.student.is_active:
        return None
    VisitShare.objects.filter(pk=share.pk).update(
        opened_count=share.opened_count + 1, last_opened_at=timezone.now()
    )
    return share


def describe_visit(share: VisitShare) -> dict[str, Any]:
    """What the instructor is told: the student, the day, the length, and whether they came."""
    appointment = share.participant.appointment
    with timezone.override(share.center.time_zone):
        day = date_format(timezone.localtime(appointment.starts_at), "l, F j, Y")
    return {
        "student": share.student.display_name,
        "center": share.center.name,
        "day": day,
        "minutes": appointment.minutes,
        "attended": share.participant.attendance == "attended",
        "course": share.course,
    }
