"""Tutor training: modules made of pages, readings and video links, and short quizzes.

A director builds modules and publishes them; tutors (and front desk staff) work
through them in order. A page or link is done when the person marks it done; a quiz
is done when they pass it, and they may retake it as often as they like. A module is
complete when all its items are done. Hours of training come from the time clock's
training category, not from this app.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


class ItemKind(models.TextChoices):
    PAGE = "page", _("A page to read")
    LINK = "link", _("A reading or video elsewhere")
    QUIZ = "quiz", _("A short quiz")


class Module(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="training_modules")
    title = models.CharField(_("title"), max_length=200)
    summary = models.TextField(_("what it covers"), blank=True)
    position = models.PositiveSmallIntegerField(_("order"), default=0)
    is_published = models.BooleanField(
        _("published"), default=False, help_text=_("Tutors see only published modules.")
    )
    is_required = models.BooleanField(
        _("required for onboarding"),
        default=True,
        help_text=_("A new tutor has finished onboarding when every required module is done."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "title"]

    def __str__(self) -> str:
        return self.title


class Item(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(_("kind"), max_length=10, choices=ItemKind.choices)
    title = models.CharField(_("title"), max_length=200)
    # A page's text, or a note about a link.
    body = models.TextField(_("text"), blank=True)
    url = models.URLField(_("address"), blank=True)
    minutes = models.PositiveSmallIntegerField(_("about how many minutes"), null=True, blank=True)
    pass_percent = models.PositiveSmallIntegerField(
        _("score needed to pass"),
        default=80,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )

    class Meta:
        ordering = ["position", "title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(pass_percent__gte=1, pass_percent__lte=100),
                name="training_item_pass_percent_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def center(self) -> Center:
        return self.module.center

    @property
    def is_quiz(self) -> bool:
        return self.kind == ItemKind.QUIZ


class Question(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="questions")
    position = models.PositiveSmallIntegerField(default=0)
    prompt = models.TextField(_("question"))

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.prompt[:60]


class Choice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    position = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.text


class Completion(models.Model):
    """A person marked a page or link done."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="completions")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="training_completions"
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "person"], name="training_completion_unique")
        ]

    def __str__(self) -> str:
        return f"{self.person} finished {self.item}"


class Attempt(models.Model):
    """One try at a quiz."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="attempts")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="training_attempts"
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    correct = models.PositiveSmallIntegerField()
    total = models.PositiveSmallIntegerField()
    passed = models.BooleanField()
    # question id -> choice id, as chosen.
    answers = models.JSONField(default=dict)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["item", "person", "passed"], name="training_attempt_lookup")
        ]

    def __str__(self) -> str:
        return f"{self.person} on {self.item}: {self.correct}/{self.total}"

    @property
    def percent(self) -> int:
        return round(100 * self.correct / self.total) if self.total else 0


# --- Cohorts, mentors and observations ------------------------------------------------------------


class Cohort(models.Model):
    """A group of new staff onboarded together."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="cohorts")
    name = models.CharField(
        _("name"), max_length=100, help_text=_("For example, Fall 2026 tutors.")
    )
    starts_on = models.DateField(_("starts"))
    required_observations = models.PositiveSmallIntegerField(
        _("observations each person needs"),
        default=1,
        validators=[MaxValueValidator(20)],
        help_text=_("Observations of their tutoring, filled in by a mentor or director."),
    )
    is_active = models.BooleanField(_("still onboarding"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "name"]
        constraints = [
            models.UniqueConstraint(fields=["center", "name"], name="training_cohort_unique_name")
        ]

    def __str__(self) -> str:
        return self.name


class CohortMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cohort = models.ForeignKey(Cohort, on_delete=models.CASCADE, related_name="members")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cohort_memberships"
    )
    mentor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentees",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["person__full_name"]
        constraints = [
            models.UniqueConstraint(fields=["cohort", "person"], name="training_member_unique")
        ]

    def __str__(self) -> str:
        return f"{self.person} in {self.cohort}"

    @property
    def center(self) -> Center:
        return self.cohort.center


class SignOff(models.Model):
    """A mentor or director confirming that a person has done a module well."""

    member = models.ForeignKey(CohortMember, on_delete=models.CASCADE, related_name="sign_offs")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="sign_offs")
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    signed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["member", "module"], name="training_signoff_unique")
        ]

    def __str__(self) -> str:
        return f"{self.member} / {self.module}"


class Template(models.Model):
    """A checklist for observing a tutor: criteria, each rated one to four."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(
        Center, on_delete=models.CASCADE, related_name="observation_templates"
    )
    title = models.CharField(_("title"), max_length=200)
    is_active = models.BooleanField(_("in use"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class Criterion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="criteria")
    position = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(_("what to look for"), max_length=300)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.text


SCORES = [
    (1, _("Needs work")),
    (2, _("Developing")),
    (3, _("Solid")),
    (4, _("Strong")),
]


class ObservationStatus(models.TextChoices):
    DRAFT = "draft", _("Being filled in")
    SUBMITTED = "submitted", _("Submitted")


class Observation(models.Model):
    """One observer's notes on one tutor, from one template. Submitting it locks it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="observations")
    template = models.ForeignKey(Template, on_delete=models.PROTECT, related_name="observations")
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="observations_of"
    )
    observer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="observations_by",
    )
    observed_on = models.DateField(_("day observed"))
    notes = models.TextField(_("overall notes"), blank=True)
    status = models.CharField(
        max_length=10, choices=ObservationStatus.choices, default=ObservationStatus.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_on", "-created_at"]

    def __str__(self) -> str:
        return f"{self.tutor} observed {self.observed_on}"

    @property
    def is_submitted(self) -> bool:
        return self.status == ObservationStatus.SUBMITTED


class Rating(models.Model):
    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="ratings")
    criterion = models.ForeignKey(Criterion, on_delete=models.PROTECT, related_name="ratings")
    score = models.PositiveSmallIntegerField(choices=SCORES)
    comment = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["observation", "criterion"], name="training_rating_unique"
            ),
            models.CheckConstraint(
                condition=Q(score__gte=1, score__lte=4), name="training_rating_score_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.observation}: {self.score}"
