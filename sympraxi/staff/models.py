"""Hours staff work: clocked in and out, or added by a director.

A time entry is one stretch of work in one center, in one category. Entries are
employment records rather than education records, but they are still personal
data: only the person and the center's directors see them, and every change a
director makes is audited with what it was before.

The database keeps the rules: an end after the start, one open entry per person
in a center, and no two entries for a person in a center that overlap. A
removed entry is kept and marked removed, so a payroll question months later
can still be answered.
"""

import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import TsTzRange


class Category(models.TextChoices):
    TUTORING = "tutoring", _("Tutoring")
    TRAINING = "training", _("Training")
    MEETING = "meeting", _("Staff meeting")
    OFFICE = "office", _("Front desk or office work")
    OTHER = "other", _("Other")


def to_hours(span: timedelta) -> Decimal:
    """A length of time in hours, to the hundredth."""
    return (Decimal(span.total_seconds()) / 3600).quantize(Decimal("0.01"), ROUND_HALF_UP)


class TimeEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="time_entries")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="time_entries"
    )
    category = models.CharField(_("what for"), max_length=20, choices=Category.choices)
    started_at = models.DateTimeField(_("started"))
    # Empty while the person is clocked in.
    ended_at = models.DateTimeField(_("ended"), null=True, blank=True)
    note = models.CharField(_("note"), max_length=200, blank=True)
    # The scheduler closed it at midnight because nobody clocked out.
    closed_automatically = models.BooleanField(default=False)
    # Set when a director added the entry instead of the person clocking in.
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The last director to change it. The audit log has every change and what it was.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    changed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["center", "started_at"], name="staff_entry_center_start")]
        constraints = [
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F("started_at")),
                name="staff_timeentry_ends_after_start",
            ),
            models.UniqueConstraint(
                fields=["center", "person"],
                condition=Q(ended_at__isnull=True, removed_at__isnull=True),
                name="staff_timeentry_one_open",
            ),
            # An open entry's range has no end, so nothing can follow it until it
            # closes. Needs btree_gist (scheduling migration 0001) for "=".
            ExclusionConstraint(
                name="staff_timeentry_no_overlap",
                expressions=[
                    ("center", RangeOperators.EQUAL),
                    ("person", RangeOperators.EQUAL),
                    (TsTzRange("started_at", "ended_at", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(removed_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} · {self.get_category_display()} · {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def hours(self) -> Decimal | None:
        if self.ended_at is None:
            return None
        return to_hours(self.ended_at - self.started_at)
