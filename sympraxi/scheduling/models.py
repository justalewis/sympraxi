"""Terms, schedules, tutors' hours and appointments.

Times of day (opening hours, weekly availability) are wall-clock times in the
center's time zone. Appointments and one-off exceptions are instants, stored in
UTC. `sympraxi.scheduling.timeutils` turns one into the other.

Two rules are enforced by the database rather than by code, so a race between
two requests can't break them:

- A tutor can't have two active appointments whose time (plus the buffer after
  each) overlaps, on any schedule in any center. This is the exclusion
  constraint on `Appointment`.
- Seats in a group appointment are taken while the appointment row is locked
  (`sympraxi.scheduling.services`).
"""

import uuid
import zoneinfo
from datetime import date, time, timedelta
from functools import cached_property
from typing import Any

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Func, Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


class TsTzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Weekday(models.IntegerChoices):
    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class Term(models.Model):
    """An academic term. Per-term booking limits count appointments inside its dates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(_("name"), max_length=100, help_text=_("For example, Fall 2026."))
    starts_on = models.DateField(_("first day"))
    ends_on = models.DateField(_("last day"))

    class Meta:
        ordering = ["-starts_on"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")), name="scheduling_term_dates_in_order"
            ),
            models.UniqueConstraint(fields=["center", "name"], name="scheduling_term_unique_name"),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": _("The last day can't be before the first day.")})


class Closure(models.Model):
    """Days in a term when every schedule is closed, such as holidays."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="closures")
    label = models.CharField(_("what"), max_length=100, help_text=_("For example, Thanksgiving."))
    starts_on = models.DateField(_("first day closed"))
    ends_on = models.DateField(_("last day closed"))

    class Meta:
        ordering = ["starts_on"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")),
                name="scheduling_closure_dates_in_order",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def center(self) -> Center:
        return self.term.center

    def clean(self) -> None:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": _("The last day can't be before the first day.")})


class Room(models.Model):
    """A place where in-person appointments happen."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(_("name"), max_length=100)
    details = models.CharField(
        _("directions"), max_length=200, blank=True, help_text=_("Building, floor, room number.")
    )
    is_active = models.BooleanField(_("in use"), default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["center", "name"], name="scheduling_room_unique_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Visibility(models.TextChoices):
    EVERYONE = "everyone", _("Everyone in the center")
    STAFF = "staff", _("Staff only")
    HIDDEN = "hidden", _("Hidden (archived)")


# How long before an appointment a reminder can go out. The shortest gap between
# two choices (30 minutes) is longer than the reminder catch-up window, so two
# reminders for one appointment are never due in the same sweep.
REMINDER_CHOICES = [
    (30, _("30 minutes before")),
    (60, _("1 hour before")),
    (120, _("2 hours before")),
    (180, _("3 hours before")),
    (240, _("4 hours before")),
    (360, _("6 hours before")),
    (720, _("12 hours before")),
    (1440, _("24 hours before")),
    (2880, _("48 hours before")),
]


class Schedule(models.Model):
    """A bookable calendar: its dates, hours, slot size and booking rules."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="schedules")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="schedules")
    name = models.CharField(_("name"), max_length=100)
    description = models.TextField(_("description"), blank=True)
    starts_on = models.DateField(_("first day"))
    ends_on = models.DateField(_("last day"))
    opens_at = models.TimeField(_("opens at"), default=time(9))
    closes_at = models.TimeField(_("closes at"), default=time(17))
    slot_minutes = models.PositiveSmallIntegerField(
        _("slot length in minutes"),
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(240)],
        help_text=_("Appointments start on this grid, counted from the opening time."),
    )
    buffer_minutes = models.PositiveSmallIntegerField(
        _("break after each appointment, in minutes"),
        default=0,
        validators=[MaxValueValidator(120)],
    )
    visibility = models.CharField(
        _("who can see it"), max_length=10, choices=Visibility.choices, default=Visibility.EVERYONE
    )

    # Booking rules for students booking for themselves. Staff aren't held to the
    # notice, horizon or cutoff, and can override limits when they book for someone.
    min_notice_minutes = models.PositiveIntegerField(
        _("minimum notice, in minutes"),
        default=60,
        help_text=_("How long before an appointment students can still book it."),
    )
    horizon_days = models.PositiveSmallIntegerField(
        _("booking horizon, in days"),
        default=14,
        validators=[MaxValueValidator(366)],
        help_text=_("How many days ahead students can book."),
    )
    change_cutoff_minutes = models.PositiveIntegerField(
        _("change cutoff, in minutes"),
        default=60,
        help_text=_("Students can't cancel or change an appointment this close to its start."),
    )
    limits_span_center = models.BooleanField(
        _("count appointments on every schedule in the center"),
        default=False,
        help_text=_(
            "If checked, the limits below count a student's appointments on all of the "
            "center's schedules, not just this one."
        ),
    )
    max_per_day = models.PositiveSmallIntegerField(
        _("most appointments per day"), null=True, blank=True
    )
    max_per_week = models.PositiveSmallIntegerField(
        _("most appointments per week"), null=True, blank=True
    )
    max_per_term = models.PositiveSmallIntegerField(
        _("most appointments per term"), null=True, blank=True
    )
    max_minutes_per_day = models.PositiveIntegerField(
        _("most minutes per day"), null=True, blank=True
    )
    max_minutes_per_week = models.PositiveIntegerField(
        _("most minutes per week"), null=True, blank=True
    )
    max_minutes_per_term = models.PositiveIntegerField(
        _("most minutes per term"), null=True, blank=True
    )

    # Attendance. See `sympraxi.scheduling.attendance`.
    late_cancel_minutes = models.PositiveIntegerField(
        _("late cancellation window, in minutes"),
        default=0,
        help_text=_(
            "A cancellation this close to the start is recorded as late, and the student gets "
            "an email saying so. Staff can waive it. 0 turns this off."
        ),
    )
    no_show_grace_minutes = models.PositiveSmallIntegerField(
        _("minutes before a possible no-show is flagged"),
        default=15,
        validators=[MaxValueValidator(240)],
        help_text=_(
            "If no one has marked a student as here by this many minutes after the start, staff "
            "are asked to check. Nothing counts as a no-show until staff confirm it."
        ),
    )
    no_show_block_limit = models.PositiveSmallIntegerField(
        _("pause online booking on this schedule after this many no-shows"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Counts only this schedule. Leave empty for no limit."),
    )
    late_cancel_block_limit = models.PositiveSmallIntegerField(
        _("pause online booking on this schedule after this many late cancellations"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Counts only this schedule. Leave empty for no limit."),
    )
    block_count_from = models.DateField(
        _("count no-shows and late cancellations from this date"),
        null=True,
        blank=True,
        help_text=_("Leave empty to count within the term."),
    )

    # Reminder emails, sent by the scheduler (`manage.py scheduler`).
    reminder_1_minutes = models.PositiveIntegerField(
        _("first reminder"), choices=REMINDER_CHOICES, null=True, blank=True, default=1440
    )
    reminder_2_minutes = models.PositiveIntegerField(
        _("second reminder"), choices=REMINDER_CHOICES, null=True, blank=True, default=60
    )
    reminder_3_minutes = models.PositiveIntegerField(
        _("third reminder"), choices=REMINDER_CHOICES, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")),
                name="scheduling_schedule_dates_in_order",
            ),
            models.CheckConstraint(
                condition=Q(closes_at__gt=F("opens_at")),
                name="scheduling_schedule_hours_in_order",
            ),
            models.CheckConstraint(
                condition=Q(slot_minutes__gte=5), name="scheduling_schedule_slot_positive"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @cached_property
    def tz(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.center.time_zone)

    @property
    def is_published(self) -> bool:
        return self.visibility == Visibility.EVERYONE

    @property
    def accepts_bookings(self) -> bool:
        return self.visibility != Visibility.HIDDEN and self.center.is_active

    def reminder_offsets(self) -> set[int]:
        """Minutes before each appointment that a reminder goes out."""
        return {
            m
            for m in (self.reminder_1_minutes, self.reminder_2_minutes, self.reminder_3_minutes)
            if m
        }

    def clean(self) -> None:
        errors: dict[str, Any] = {}
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            errors["ends_on"] = _("The last day can't be before the first day.")
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            errors["closes_at"] = _("Closing time must be after opening time.")
        reminders = [
            m
            for m in (self.reminder_1_minutes, self.reminder_2_minutes, self.reminder_3_minutes)
            if m
        ]
        if len(reminders) != len(set(reminders)):
            errors["reminder_2_minutes"] = _("Choose a different time for each reminder.")
        if self.term_id and self.center_id:
            if self.term.center_id != self.center_id:
                errors["term"] = _("Choose a term in this center.")
            elif (
                self.starts_on
                and self.ends_on
                and not (
                    self.term.starts_on <= self.starts_on and self.ends_on <= self.term.ends_on
                )
            ):
                errors["starts_on"] = _("The schedule's dates must fall inside the term.")
        if errors:
            raise ValidationError(errors)


class Modality(models.TextChoices):
    IN_PERSON = "in_person", _("In person")
    ONLINE = "online", _("Online (meeting link)")


class TutorAssignment(models.Model):
    """One tutor on one schedule. A tutor account can be assigned to many schedules."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="assignments")
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tutor_assignments"
    )
    min_minutes = models.PositiveSmallIntegerField(
        _("shortest appointment, in minutes"), default=30
    )
    max_minutes = models.PositiveSmallIntegerField(_("longest appointment, in minutes"), default=60)
    max_per_day = models.PositiveSmallIntegerField(
        _("most appointments per day"), null=True, blank=True
    )
    max_per_week = models.PositiveSmallIntegerField(
        _("most appointments per week"), null=True, blank=True
    )
    offers_in_person = models.BooleanField(_("meets in person"), default=True)
    offers_online = models.BooleanField(_("meets online"), default=False)
    meeting_url = models.URLField(
        _("meeting link"),
        blank=True,
        help_text=_("The tutor's Zoom, Teams or Meet link, sent to students who book online."),
    )
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments"
    )
    capacity = models.PositiveSmallIntegerField(
        _("students per appointment"),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text=_("More than 1 makes this a group: other students can join a booked time."),
    )
    needs_confirmation = models.BooleanField(
        _("student bookings need staff confirmation"),
        default=False,
        help_text=_(
            "Students' bookings stay tentative until the front desk or a director confirms."
        ),
    )
    is_active = models.BooleanField(_("taking appointments"), default=True)

    class Meta:
        ordering = ["schedule", "tutor__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "tutor"], name="scheduling_assignment_unique_tutor"
            ),
            models.CheckConstraint(
                condition=Q(max_minutes__gte=F("min_minutes")),
                name="scheduling_assignment_lengths_in_order",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gte=1), name="scheduling_assignment_capacity_positive"
            ),
            models.CheckConstraint(
                condition=Q(offers_in_person=True) | Q(offers_online=True),
                name="scheduling_assignment_has_modality",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tutor} · {self.schedule}"

    @property
    def center(self) -> Center:
        return self.schedule.center

    @property
    def modalities(self) -> list[str]:
        result = []
        if self.offers_in_person:
            result.append(Modality.IN_PERSON.value)
        if self.offers_online:
            result.append(Modality.ONLINE.value)
        return result

    def lengths(self) -> list[int]:
        """Appointment lengths this tutor offers, on the schedule's slot grid."""
        step = self.schedule.slot_minutes
        first = -(-self.min_minutes // step) * step
        return list(range(first, self.max_minutes + 1, step))

    def _center_has_video(self) -> bool:
        """Whether the schedule's center gives online appointments a video room."""
        from sympraxi.online.services import video_on

        return bool(self.schedule_id) and video_on(self.schedule.center)

    def clean(self) -> None:
        errors: dict[str, Any] = {}
        if self.max_minutes is not None and self.min_minutes is not None:
            if self.max_minutes < self.min_minutes:
                errors["max_minutes"] = _(
                    "The longest appointment can't be shorter than the shortest."
                )
            elif self.schedule_id and not self.lengths():
                errors["min_minutes"] = _(
                    "No length between the shortest and longest fits the schedule's slot length."
                )
        if not self.offers_in_person and not self.offers_online:
            errors["offers_in_person"] = _("Choose at least one way to meet.")
        if self.offers_online and not self.meeting_url and not self._center_has_video():
            errors["meeting_url"] = _("Online appointments need a meeting link.")
        room = self.room if self.room_id else None
        if room is not None and self.schedule_id and room.center_id != self.schedule.center_id:
            errors["room"] = _("Choose a room in this center.")
        if errors:
            raise ValidationError(errors)


class WeeklyHours(models.Model):
    """A block of weekly availability, in the center's wall-clock time."""

    assignment = models.ForeignKey(
        TutorAssignment, on_delete=models.CASCADE, related_name="weekly_hours"
    )
    weekday = models.PositiveSmallIntegerField(_("day"), choices=Weekday.choices)
    starts_at = models.TimeField(_("from"))
    ends_at = models.TimeField(_("until"))

    class Meta:
        ordering = ["weekday", "starts_at"]
        verbose_name_plural = "weekly hours"
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="scheduling_weeklyhours_in_order"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()} {self.starts_at:%H:%M}-{self.ends_at:%H:%M}"

    def clean(self) -> None:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("The end must be after the start.")})


class ExceptionKind(models.TextChoices):
    BLACKOUT = "blackout", _("Not available")
    EXTRA = "extra", _("Extra hours")


class AvailabilityException(models.Model):
    """A one-off change to a tutor's weekly hours: time off, or extra hours."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        TutorAssignment, on_delete=models.CASCADE, related_name="exceptions"
    )
    kind = models.CharField(_("kind"), max_length=10, choices=ExceptionKind.choices)
    starts_at = models.DateTimeField(_("from"))
    ends_at = models.DateTimeField(_("until"))
    note = models.CharField(_("note"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="scheduling_exception_in_order"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.starts_at:%Y-%m-%d %H:%M}"

    @property
    def center(self) -> Center:
        return self.assignment.schedule.center

    def clean(self) -> None:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("The end must be after the start.")})


class AppointmentStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    CANCELED = "canceled", _("Canceled")


class Appointment(models.Model):
    """A booked time with a tutor. Students are attached through `Participant`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.PROTECT, related_name="appointments")
    assignment = models.ForeignKey(
        TutorAssignment, on_delete=models.PROTECT, related_name="appointments"
    )
    # Copied from the assignment so the database can check overlaps per person,
    # across every schedule the tutor works on.
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tutoring_appointments"
    )
    starts_at = models.DateTimeField(_("starts"))
    ends_at = models.DateTimeField(_("ends"))
    # The end plus the schedule's break, fixed at booking time. The tutor is busy
    # until then.
    blocked_until = models.DateTimeField()
    modality = models.CharField(_("how"), max_length=10, choices=Modality.choices)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.SCHEDULED,
    )
    is_walk_in = models.BooleanField(_("walk-in"), default=False)
    is_placeholder = models.BooleanField(
        _("placeholder"), default=False, help_text=_("Holds the time without a student.")
    )
    is_tentative = models.BooleanField(
        _("tentative"), default=False, help_text=_("Waiting for staff to confirm.")
    )
    capacity = models.PositiveSmallIntegerField(default=1)
    series_id = models.UUIDField(null=True, blank=True, db_index=True)
    meeting_url = models.URLField(_("meeting link"), blank=True)
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments"
    )
    # iCalendar SEQUENCE: goes up each time the appointment changes, so calendar
    # apps replace their copy instead of adding a second one.
    sequence = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["tutor", "starts_at"], name="scheduling_appt_tutor_start"),
            models.Index(fields=["schedule", "starts_at"], name="scheduling_appt_sched_start"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="scheduling_appointment_in_order"
            ),
            models.CheckConstraint(
                condition=Q(blocked_until__gte=F("ends_at")),
                name="scheduling_appointment_buffer_after_end",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gte=1), name="scheduling_appointment_capacity_positive"
            ),
            # Needs the btree_gist extension (migration 0001) for "=" on the tutor.
            ExclusionConstraint(
                name="scheduling_appointment_no_tutor_overlap",
                expressions=[
                    ("tutor", RangeOperators.EQUAL),
                    (
                        TsTzRange("starts_at", "blocked_until", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=~Q(status=AppointmentStatus.CANCELED),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tutor} {self.starts_at:%Y-%m-%d %H:%M}"

    @property
    def center(self) -> Center:
        return self.schedule.center

    @property
    def is_active(self) -> bool:
        return self.status != AppointmentStatus.CANCELED

    @property
    def minutes(self) -> int:
        return int((self.ends_at - self.starts_at) / timedelta(minutes=1))

    def active_participants(self) -> models.QuerySet["Participant"]:
        return self.participants.filter(canceled_at__isnull=True)


class Attendance(models.TextChoices):
    UNMARKED = "", _("Not marked")
    ATTENDED = "attended", _("Came")
    NO_SHOW = "no_show", _("No-show")


class Participant(models.Model):
    """A student in an appointment. A group appointment has several.

    Attendance is kept here, per student, because in a group some students come
    and some don't. The appointment's own status says only whether its time is
    still held (see `sympraxi.scheduling.attendance`).
    """

    appointment = models.ForeignKey(
        Appointment, on_delete=models.CASCADE, related_name="participants"
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="participations"
    )
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Set when the student leaves a group, or when the whole appointment is canceled.
    canceled_at = models.DateTimeField(null=True, blank=True)
    # The student asked to cancel inside the schedule's late cancellation window.
    late_cancel = models.BooleanField(default=False)

    attendance = models.CharField(
        _("attendance"), max_length=10, choices=Attendance.choices, blank=True, default=""
    )
    attendance_marked_at = models.DateTimeField(null=True, blank=True)
    attendance_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Set by the scheduler when no one marked the student by the end of the
    # schedule's grace period. Staff confirm it (a no-show), mark the student as
    # here, or clear it. It never becomes a no-show on its own.
    flagged_at = models.DateTimeField(null=True, blank=True)
    flag_cleared_at = models.DateTimeField(null=True, blank=True)
    flag_cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # A waived late cancellation or no-show stays on the record but no longer
    # counts toward a booking block.
    waived_at = models.DateTimeField(null=True, blank=True)
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    waiver_reason = models.TextField(_("reason"), blank=True)

    class Meta:
        ordering = ["added_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "student"], name="scheduling_participant_unique"
            ),
            models.CheckConstraint(
                condition=Q(attendance__in=["", "attended", "no_show"]),
                name="scheduling_participant_attendance_valid",
            ),
            models.CheckConstraint(
                condition=Q(late_cancel=False) | Q(canceled_at__isnull=False),
                name="scheduling_participant_late_is_canceled",
            ),
            models.CheckConstraint(
                condition=Q(waived_at__isnull=True) | ~Q(waiver_reason=""),
                name="scheduling_participant_waiver_has_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=["flagged_at"],
                condition=Q(flagged_at__isnull=False, flag_cleared_at__isnull=True, attendance=""),
                name="scheduling_part_flagged",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} in {self.appointment_id}"

    @property
    def is_possible_no_show(self) -> bool:
        return (
            self.flagged_at is not None
            and self.flag_cleared_at is None
            and self.attendance == Attendance.UNMARKED
            and self.canceled_at is None
        )

    @property
    def is_no_show(self) -> bool:
        return self.attendance == Attendance.NO_SHOW and self.canceled_at is None

    @property
    def can_be_waived(self) -> bool:
        return self.waived_at is None and (self.late_cancel or self.is_no_show)


class Exemption(models.Model):
    """Lifts a student's booking limits until a date, often for an accommodation.

    Covers appointments on or before `expires_on`. Notice, horizon and cutoff
    still apply. The note is visible to directors only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="exemptions")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="booking_exemptions"
    )
    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="exemptions",
        help_text=_("Leave empty to cover every schedule in the center."),
    )
    expires_on = models.DateField(_("covers appointments through"))
    note = models.TextField(_("note"), blank=True, help_text=_("Seen only by directors."))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-expires_on"]

    def __str__(self) -> str:
        return f"{self.student} until {self.expires_on}"


class BlockReason(models.TextChoices):
    NO_SHOWS = "no_shows", _("No-shows")
    LATE_CANCELS = "late_cancels", _("Late cancellations")


class Block(models.Model):
    """A pause on a student's online booking, set when they reach a center's or
    schedule's limit of no-shows or late cancellations.

    While a block is in effect the student can't book for themselves (on every
    schedule in the center, or on `schedule` only). Staff can still book for
    them, and their existing appointments stay. A block ends after `ends_on`,
    the last day of the term by default, or when a director lifts it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="booking_blocks")
    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="booking_blocks",
        help_text=_("Empty when the block covers every schedule in the center."),
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="booking_blocks"
    )
    reason = models.CharField(max_length=20, choices=BlockReason.choices)
    # What was counted when the block was set: how many, the limit, and the window.
    count = models.PositiveSmallIntegerField()
    limit = models.PositiveSmallIntegerField()
    term = models.ForeignKey(
        Term, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    counted_from = models.DateField(null=True, blank=True)
    # The last day of the block, in the center's time zone.
    ends_on = models.DateField(_("last day"))
    created_at = models.DateTimeField(auto_now_add=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    lift_note = models.TextField(_("note"), blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "center", "ends_on"], name="scheduling_block_student"),
        ]

    def __str__(self) -> str:
        return f"{self.student} until {self.ends_on}"

    def is_active(self, today: date) -> bool:
        return self.lifted_at is None and self.ends_on >= today

    @property
    def first_free_day(self) -> date:
        """The first day the student can book online again."""
        return self.ends_on + timedelta(days=1)


class Reminder(models.Model):
    """One reminder email sent, so it is never sent twice.

    The unique constraint is what stops two scheduler processes, or one that
    restarts mid-sweep, from sending the same reminder: the second insert fails
    and nothing is queued. `starts_at` is part of the key, so an appointment
    that moves gets reminders for its new time.
    """

    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="reminders")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    minutes_before = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    due_at = models.DateTimeField()
    sent_at = models.DateTimeField()

    class Meta:
        ordering = ["sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "recipient", "minutes_before", "starts_at"],
                name="scheduling_reminder_once",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.minutes_before} min before {self.appointment_id} to {self.recipient_id}"
