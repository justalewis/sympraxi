"""Early alerts, and visit confirmations a student chooses to share.

An early alert is a staff member telling the center that a student may need support:
they have stopped coming, say, or something they said was worrying. It always carries a
reason in the staff member's words. Directors acknowledge it and close it with a note on
what was done. Nothing about it reaches an instructor.

A shared visit is the opposite flow: the student decides to send an instructor proof that
they came, as a link the instructor opens without an account. It shows the student's
name, the day and the length of the visit, and whether staff marked them as having come.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import Participant


class AlertKind(models.TextChoices):
    ATTENDANCE = "attendance", _("Stopped coming or missed sessions")
    ENGAGEMENT = "engagement", _("Seems disengaged or struggling with the work")
    WELLBEING = "wellbeing", _("Something said or seen about their well-being")
    OTHER = "other", _("Something else")


class AlertStatus(models.TextChoices):
    OPEN = "open", _("Open")
    ACKNOWLEDGED = "acknowledged", _("Being looked into")
    CLOSED = "closed", _("Closed")


class Alert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="alerts")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alerts_about"
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="alerts_raised"
    )
    kind = models.CharField(_("what it's about"), max_length=12, choices=AlertKind.choices)
    reason = models.TextField(_("why you're raising it"))
    student_informed = models.BooleanField(
        _("the student knows I'm raising this"),
        default=False,
        help_text=_("Tick this if you have told the student. It is recorded either way."),
    )
    status = models.CharField(
        max_length=12, choices=AlertStatus.choices, default=AlertStatus.OPEN, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(_("what was done"), blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["center", "status"], name="alerts_center_status")]
        constraints = [
            models.CheckConstraint(condition=~Q(reason=""), name="alerts_alert_has_reason"),
            models.CheckConstraint(
                condition=~Q(status="closed") | ~Q(outcome=""),
                name="alerts_alert_closed_has_outcome",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} · {self.get_kind_display()}"

    @property
    def is_closed(self) -> bool:
        return self.status == AlertStatus.CLOSED


class VisitShare(models.Model):
    """A student's choice to confirm one visit to one instructor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="shares")
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="visit_shares")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="visit_shares"
    )
    instructor_email = models.EmailField(_("instructor's email"))
    course = models.CharField(_("course"), max_length=100, blank=True)
    # Only the SHA-256 of the link's token is kept.
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    opened_count = models.PositiveIntegerField(default=0)
    last_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.student} to {self.instructor_email}"

    @property
    def is_usable(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()
