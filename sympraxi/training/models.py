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
