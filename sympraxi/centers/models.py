import uuid
import zoneinfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


def validate_time_zone(value: str) -> None:
    if value not in zoneinfo.available_timezones():
        raise ValidationError(_("%(value)s is not a known time zone."), params={"value": value})


class Role(models.TextChoices):
    STUDENT = "student", _("Student")
    TUTOR = "tutor", _("Tutor")
    FRONT_DESK = "front_desk", _("Front desk")
    DIRECTOR = "director", _("Director")


STAFF_ROLES = frozenset({Role.TUTOR, Role.FRONT_DESK, Role.DIRECTOR})


class SurveyTiming(models.TextChoices):
    """When a center's survey goes out to a student who came to a session."""

    AFTER_RECORD = "record", _("When the tutor finalizes the session record")
    AFTER_END = "end", _("A set time after the appointment ends")


# How long after a session ends its tutor is reminded about a missing record, in hours.
RECORD_REMINDER_CHOICES = [
    (None, _("Don't remind tutors")),
    (2, _("2 hours after the session")),
    (4, _("4 hours after the session")),
    (24, _("1 day after the session")),
    (48, _("2 days after the session")),
]

# How long after an appointment ends its survey goes out, in minutes.
SURVEY_DELAY_CHOICES = [
    (30, _("30 minutes")),
    (60, _("1 hour")),
    (120, _("2 hours")),
    (240, _("4 hours")),
    (1440, _("1 day")),
]


class Center(models.Model):
    """A writing center (or any tutoring center) inside one installation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("short name for links"), unique=True)
    time_zone = models.CharField(
        _("time zone"), max_length=64, default="America/New_York", validators=[validate_time_zone]
    )
    contact_email = models.EmailField(_("contact email"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    # Session records. Students always see their summaries, next steps and
    # resources; these two settings decide whether they see more.
    students_see_staff_notes = models.BooleanField(
        _("show staff notes to students"),
        default=False,
        help_text=_(
            "Students see the notes written for staff on their My writing page. Students can "
            "ask to see these notes either way."
        ),
    )
    summary_email_includes_text = models.BooleanField(
        _("put the summary in the email"),
        default=False,
        help_text=_(
            "The email that tells a student their summary is ready also includes the summary "
            "and next steps. Otherwise it only links to them."
        ),
    )
    # How long after a session ends its tutor is emailed if a record is still
    # missing. Empty: no reminders.
    record_reminder_hours = models.PositiveSmallIntegerField(
        _("remind tutors about records not yet written"),
        choices=RECORD_REMINDER_CHOICES,
        null=True,
        blank=True,
        default=24,
        help_text=_(
            "Each tutor gets one email listing the sessions they still need to write up. "
            "Students who were no-shows don't need a record."
        ),
    )
    # Booking blocks across every schedule in the center. Each schedule can also
    # set its own limits, which count only that schedule.
    no_show_block_limit = models.PositiveSmallIntegerField(
        _("pause online booking after this many no-shows"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Counts every schedule in the center. Leave empty for no limit."),
    )
    late_cancel_block_limit = models.PositiveSmallIntegerField(
        _("pause online booking after this many late cancellations"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Counts every schedule in the center. Leave empty for no limit."),
    )
    block_count_from = models.DateField(
        _("count from this date"),
        null=True,
        blank=True,
        help_text=_("Leave empty to count within each term."),
    )
    # Surveys. They go out only once the center has a survey form in use.
    survey_timing = models.CharField(
        _("when the survey goes out"),
        max_length=10,
        choices=SurveyTiming.choices,
        default=SurveyTiming.AFTER_RECORD,
    )
    survey_delay_minutes = models.PositiveIntegerField(
        _("how long after the appointment ends"),
        choices=SURVEY_DELAY_CHOICES,
        default=60,
        help_text=_("Used only when the survey goes out a set time after the appointment."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("centers:detail", kwargs={"slug": self.slug})


class Membership(models.Model):
    """A person's role in one center. A person may hold several roles in a center."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["center__name", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "center", "role"], name="centers_membership_unique_role"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.get_role_display()} · {self.center}"
