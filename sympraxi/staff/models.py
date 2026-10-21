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
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import F, Func, Q
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


class DateRange(Func):
    """The inclusive range of days an approval covers, for the database to compare."""

    function = "DATERANGE"
    output_field = DateRangeField()


class PayrollDetail(models.Model):
    """What the payroll office needs to match a person's hours to their pay."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="payroll_details")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payroll_details"
    )
    payroll_id = models.CharField(_("payroll ID"), max_length=40, blank=True)
    pay_code = models.CharField(_("pay code"), max_length=40, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["center", "person"], name="staff_payrolldetail_one_per_person"
            )
        ]

    def __str__(self) -> str:
        return f"{self.person} · {self.payroll_id}"


class TimesheetApproval(models.Model):
    """A director's sign-off on one person's hours for a stretch of days.

    While it stands, the person's entries that started in those days can't be
    added, changed or removed. Withdrawing it, with a reason, unlocks them; the
    row stays so the history is whole.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="approvals")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="timesheet_approvals"
    )
    first_day = models.DateField(_("first day"))
    last_day = models.DateField(_("last day"))
    hours = models.DecimalField(_("hours approved"), max_digits=7, decimal_places=2)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    approved_at = models.DateTimeField(auto_now_add=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-first_day", "person__full_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(last_day__gte=F("first_day")), name="staff_approval_days_in_order"
            ),
            models.CheckConstraint(
                condition=Q(withdrawn_at__isnull=True) | ~Q(withdrawn_reason=""),
                name="staff_approval_withdrawal_has_reason",
            ),
            # Needs btree_gist (scheduling migration 0001) for "=".
            ExclusionConstraint(
                name="staff_approval_no_overlap",
                expressions=[
                    ("center", RangeOperators.EQUAL),
                    ("person", RangeOperators.EQUAL),
                    (
                        DateRange("first_day", "last_day", RangeBoundary(inclusive_upper=True)),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(withdrawn_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} · {self.first_day} to {self.last_day}"

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None
