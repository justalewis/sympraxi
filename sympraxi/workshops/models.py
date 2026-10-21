"""Workshops: a session for a group, with a roster and attendance.

A workshop is not an appointment. It has its own time, a leader, a capacity and a
list of students who signed up, and nothing in it touches the tutors' schedules or
the one-to-one booking limits. A director publishes it; students see it on the
center's workshop page and register until it starts.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


class WorkshopStatus(models.TextChoices):
    DRAFT = "draft", _("Draft: only staff see it")
    PUBLISHED = "published", _("Published: students can register")
    CANCELED = "canceled", _("Canceled")


class Workshop(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="workshops")
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("what it covers"), blank=True)
    starts_at = models.DateTimeField(_("starts"))
    ends_at = models.DateTimeField(_("ends"))
    location = models.CharField(_("where"), max_length=200, blank=True)
    meeting_url = models.URLField(_("meeting link"), blank=True)
    leader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_workshops",
        verbose_name=_("led by"),
    )
    capacity = models.PositiveSmallIntegerField(
        _("seats"), null=True, blank=True, help_text=_("Leave empty for no limit.")
    )
    status = models.CharField(
        _("status"), max_length=10, choices=WorkshopStatus.choices, default=WorkshopStatus.DRAFT
    )
    canceled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["center", "starts_at"], name="workshops_center_start")]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="workshops_workshop_in_order"
            ),
            models.CheckConstraint(
                condition=Q(capacity__isnull=True) | Q(capacity__gte=1),
                name="workshops_workshop_capacity_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.status == WorkshopStatus.PUBLISHED

    @property
    def is_canceled(self) -> bool:
        return self.status == WorkshopStatus.CANCELED

    @property
    def has_started(self) -> bool:
        return self.starts_at <= timezone.now()

    def taken(self) -> int:
        return self.registrations.filter(canceled_at__isnull=True).count()

    def seats_left(self) -> int | None:
        return None if self.capacity is None else max(self.capacity - self.taken(), 0)


class Attendance(models.TextChoices):
    UNMARKED = "", _("Not marked")
    ATTENDED = "attended", _("Came")
    NO_SHOW = "no_show", _("Did not come")


class Registration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name="registrations")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workshop_registrations"
    )
    registered_at = models.DateTimeField(default=timezone.now)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    canceled_at = models.DateTimeField(null=True, blank=True)
    attendance = models.CharField(
        max_length=10, choices=Attendance.choices, default=Attendance.UNMARKED, blank=True
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "student"], name="workshops_registration_unique"
            ),
            models.CheckConstraint(
                condition=Q(attendance__in=["", "attended", "no_show"]),
                name="workshops_registration_attendance_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} in {self.workshop}"

    @property
    def is_active(self) -> bool:
        return self.canceled_at is None
