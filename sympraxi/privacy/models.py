from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

ACTION_LABELS = {
    "auth.login": _("Signed in"),
    "auth.login_failed": _("Failed sign-in"),
    "auth.logout": _("Signed out"),
    "auth.password_changed": _("Changed password"),
    "auth.password_reset": _("Reset password"),
    "auth.email_changed": _("Changed email address"),
    "auth.mfa_added": _("Added a two-factor method"),
    "auth.mfa_removed": _("Removed a two-factor method"),
    "auth.mfa_recovery_codes_reset": _("Generated new recovery codes"),
    "access.membership_added": _("Added someone to the center"),
    "access.membership_changed": _("Changed someone's role"),
    "access.membership_removed": _("Removed someone from the center"),
    "privacy.audit_log_viewed": _("Viewed the audit log"),
}


class AuditEvent(models.Model):
    """One entry in the append-only audit log.

    A database trigger (migration 0002) rejects UPDATE and DELETE on this table,
    so entries can't be edited or quietly removed, even from a database shell.
    Retention purges will set `sympraxi.audit_purge` for their transaction.

    The actor and center are kept as IDs without foreign-key constraints, plus a
    label captured at the time, so deleting a user or center never rewrites history.
    """

    occurred_at = models.DateTimeField(_("when"), default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_label = models.CharField(_("who"), max_length=254, blank=True)
    action = models.CharField(_("action"), max_length=64, db_index=True)
    center = models.ForeignKey(
        "centers.Center",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["center", "-occurred_at"], name="privacy_audit_center_time")
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action} by {self.actor_label or '-'}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Audit events are append-only.")

    @property
    def action_label(self) -> str:
        return str(ACTION_LABELS.get(self.action, self.action))
