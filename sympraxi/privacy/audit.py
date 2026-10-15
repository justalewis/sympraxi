"""Write entries to the audit log.

Call `record()` whenever someone signs in, changes access, or reads or changes a
sensitive record. Keep `details` free of student work and note text: record what
happened and to which object, not the content.
"""

from enum import StrEnum
from typing import Any

from django.conf import settings
from django.db.models import Model
from django.http import HttpRequest

from sympraxi.privacy.models import AuditEvent


class AuditAction(StrEnum):
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105 (an event name, not a secret)
    PASSWORD_RESET = "auth.password_reset"  # noqa: S105
    EMAIL_CHANGED = "auth.email_changed"
    MFA_ADDED = "auth.mfa_added"
    MFA_REMOVED = "auth.mfa_removed"
    MFA_RECOVERY_CODES_RESET = "auth.mfa_recovery_codes_reset"
    MEMBERSHIP_ADDED = "access.membership_added"
    MEMBERSHIP_CHANGED = "access.membership_changed"
    MEMBERSHIP_REMOVED = "access.membership_removed"
    AUDIT_LOG_VIEWED = "privacy.audit_log_viewed"


def client_ip(request: HttpRequest) -> str | None:
    if settings.SYMPRAXI_TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # The proxy appends the address it saw; that last entry is the one we trust.
            return forwarded.split(",")[-1].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record(
    action: AuditAction,
    *,
    request: HttpRequest | None = None,
    actor: Any = None,
    actor_label: str = "",
    center: Any = None,
    target: Model | None = None,
    **details: Any,
) -> AuditEvent:
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            actor = user
    if actor is not None and not actor_label:
        actor_label = actor.email
    return AuditEvent.objects.create(
        action=action,
        actor=actor,
        actor_label=actor_label[:254],
        center=center,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        ip_address=client_ip(request) if request is not None else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else "",
        details=details,
    )
