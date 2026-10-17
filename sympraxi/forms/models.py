"""Forms a center writes for itself: registration and intake now, session reports and
surveys later.

A `Form` is one place a center asks questions: its registration form, the intake
form for a schedule (or the center's default intake form), and later its
session-report questions and surveys. Its wording lives in numbered
`FormVersion`s. Directors edit a draft version and publish it; editing a
published form starts a new draft that copies it.

Every question has a permanent `key`, and every option of a choice question has
one too. A new version copies the keys, so moving or rewording a question never
changes what earlier answers mean. A question removed from a later version keeps
its key in the versions that had it.

A `Response` is one person's answers to one version: an `Answer` per question
answered, stored by the question's key and, for choices, by the options' keys.
Each answer also points to the exact question it answered, so it is always shown
with the wording the person saw. Questions skipped, or hidden because a
condition wasn't met, have no answer.

The database keeps this honest (migration 0002): a published version and its
questions and options can't change or be deleted, an answer must belong to its
response's version, and responses and answers are never updated or deleted.
Changing answers means a new response; the old one stays.

Intake and registration answers are FERPA education records.
"""

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Deferrable, Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import Appointment, Schedule


class FormKind(models.TextChoices):
    REGISTRATION = "registration", _("Registration")
    INTAKE = "intake", _("Intake")
    REPORT = "report", _("Session report")
    SURVEY = "survey", _("Survey")


# Kinds that may belong to one schedule instead of the whole center.
PER_SCHEDULE_KINDS = (FormKind.INTAKE, FormKind.SURVEY)


class QuestionKind(models.TextChoices):
    SHORT_TEXT = "short_text", _("Short answer")
    LONG_TEXT = "long_text", _("Paragraph")
    ONE_CHOICE = "one_choice", _("Choose one")
    SEVERAL_CHOICES = "several_choices", _("Choose any that apply")
    YES_NO = "yes_no", _("Yes or no")
    NUMBER = "number", _("Number")
    DATE = "date", _("Date")


# Questions answered by choosing options. Only these can be the question a
# follow-up depends on.
CHOICE_KINDS = frozenset(
    {QuestionKind.ONE_CHOICE, QuestionKind.SEVERAL_CHOICES, QuestionKind.YES_NO}
)

SHORT_TEXT_LENGTH = 300
LONG_TEXT_LENGTH = 5000


class Form(models.Model):
    """One place a center asks questions.

    A center has at most one form of each kind for each place: one registration
    form, one default intake form, and one intake form per schedule.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="forms")
    kind = models.CharField(_("kind"), max_length=20, choices=FormKind.choices)
    # An intake form for one schedule. Empty means the center's default, used on
    # every schedule that has no form of its own.
    schedule = models.ForeignKey(
        Schedule, on_delete=models.PROTECT, null=True, blank=True, related_name="forms"
    )
    is_active = models.BooleanField(
        _("ask these questions"),
        default=True,
        help_text=_("Turn this off to stop asking. Answers already given are kept."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["kind", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["center", "kind", "schedule"],
                nulls_distinct=False,
                name="forms_one_per_place",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=PER_SCHEDULE_KINDS) | Q(schedule__isnull=True),
                name="forms_schedule_only_where_allowed",
            ),
        ]

    def __str__(self) -> str:
        return str(self.title)

    @property
    def title(self) -> str:
        """What directors call this form: "Registration", "Intake: Drop-in tutoring"."""
        if self.kind == FormKind.INTAKE:
            if self.schedule_id is None:
                return str(_("Intake: every schedule without its own form"))
            return str(_("Intake: %(schedule)s") % {"schedule": self.schedule})
        return str(self.get_kind_display())

    def published(self) -> "FormVersion | None":
        """The version people answer now: the latest one published."""
        return self.versions.filter(published_at__isnull=False).order_by("-number").first()

    def draft(self) -> "FormVersion | None":
        return self.versions.filter(published_at__isnull=True).first()


class FormVersion(models.Model):
    """One numbered version of a form's wording. Frozen once published."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    intro = models.TextField(
        _("introduction"),
        blank=True,
        help_text=_("Shown above the questions. Optional."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["form", "number"]
        constraints = [
            models.UniqueConstraint(fields=["form", "number"], name="forms_version_number_once"),
            models.UniqueConstraint(
                fields=["form"],
                condition=Q(published_at__isnull=True),
                name="forms_one_draft_per_form",
            ),
            models.CheckConstraint(
                condition=Q(published_at__isnull=True) | Q(published_by__isnull=False),
                name="forms_published_by_someone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.form} v{self.number}"

    @property
    def center(self) -> Center:
        return self.form.center

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def ordered_questions(self) -> list["Question"]:
        return list(self.questions.prefetch_related("options").order_by("position"))


class Question(models.Model):
    """One question in one version. `key` stays the same across versions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(FormVersion, on_delete=models.CASCADE, related_name="questions")
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    position = models.PositiveIntegerField()
    kind = models.CharField(_("kind of answer"), max_length=20, choices=QuestionKind.choices)
    label = models.CharField(_("question"), max_length=500)
    help_text = models.TextField(
        _("hint"), blank=True, help_text=_("Shown under the question. Optional.")
    )
    required = models.BooleanField(_("an answer is required"), default=False)
    is_demographic = models.BooleanField(
        _("asks about who the student is"),
        default=False,
        help_text=_(
            "For example gender, race or ethnicity, age, or first-generation status. "
            "These questions are always optional."
        ),
    )
    # Registration forms only: tutors and front desk staff see the answer.
    # Directors see every answer.
    shown_to_staff = models.BooleanField(
        _("tutors and front desk staff see the answer"),
        default=False,
        help_text=_("Directors see every answer either way."),
    )
    # A follow-up: show this question only when the question with this key was
    # answered with the option with this key. Both are permanent keys, so the
    # condition carries over to new versions.
    show_if_question = models.UUIDField(null=True, blank=True)
    show_if_option = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["version", "position"]
        constraints = [
            models.UniqueConstraint(fields=["version", "key"], name="forms_question_key_once"),
            models.UniqueConstraint(
                fields=["version", "position"],
                name="forms_question_position_once",
                deferrable=Deferrable.DEFERRED,
            ),
            models.CheckConstraint(
                condition=Q(show_if_question__isnull=True, show_if_option__isnull=True)
                | Q(show_if_question__isnull=False, show_if_option__isnull=False),
                name="forms_condition_complete",
            ),
            models.CheckConstraint(
                condition=Q(is_demographic=False) | Q(required=False),
                name="forms_demographic_optional",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def center(self) -> Center:
        return self.version.form.center

    @property
    def is_choice(self) -> bool:
        return self.kind in CHOICE_KINDS

    @property
    def is_follow_up(self) -> bool:
        return self.show_if_question is not None

    def option_labels(self) -> dict[uuid.UUID, str]:
        return {o.key: o.label for o in self.options.all()}


class Option(models.Model):
    """One choice in a choice question. `key` stays the same across versions."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    label = models.CharField(_("option"), max_length=300)
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ["question", "position"]
        constraints = [
            models.UniqueConstraint(fields=["question", "key"], name="forms_option_key_once"),
            models.UniqueConstraint(
                fields=["question", "position"],
                name="forms_option_position_once",
                deferrable=Deferrable.DEFERRED,
            ),
        ]

    def __str__(self) -> str:
        return self.label


class Response(models.Model):
    """One person's answers to one version of a form.

    `student` is whom the answers are about, and `answered_by` who gave them: the
    student, or a staff member booking for them. `appointment` is set for intake.
    Never changed: new answers are a new response, and the earlier one stays.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="responses")
    version = models.ForeignKey(FormVersion, on_delete=models.PROTECT, related_name="responses")
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="form_responses")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="form_responses",
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="form_responses",
    )
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["form", "student", "-created_at"], name="forms_response_student"),
            models.Index(
                fields=["appointment", "student", "-created_at"], name="forms_response_appointment"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.version} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def by_staff(self) -> bool:
        return self.student_id is not None and self.answered_by_id != self.student_id


class Answer(models.Model):
    """The answer to one question in a response.

    `key` is the question's permanent key, so answers to every version of a form
    can be counted together. Choices are stored as the options' permanent keys.
    """

    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="answers")
    key = models.UUIDField(db_index=True)
    text = models.TextField(blank=True)
    number = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    date = models.DateField(null=True, blank=True)
    options = ArrayField(models.UUIDField(), default=list, blank=True)

    class Meta:
        ordering = ["response", "question__position"]
        constraints = [
            models.UniqueConstraint(fields=["response", "key"], name="forms_answer_once"),
        ]

    def __str__(self) -> str:
        return f"{self.key} in {self.response_id}"

    @property
    def value(self) -> Any:
        """The answer as Python: text, a Decimal, a date or a list of option keys."""
        kind = self.question.kind
        if kind in CHOICE_KINDS:
            return list(self.options)
        if kind == QuestionKind.NUMBER:
            return self.number
        if kind == QuestionKind.DATE:
            return self.date
        return self.text


def plain_number(value: Decimal) -> str:
    """12.5000 as "12.5", 3.0000 as "3"."""
    return format(value.normalize(), "f")
