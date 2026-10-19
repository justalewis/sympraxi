"""The asynchronous queue: students send a draft, a tutor writes back.

A `Submission` is one request for written feedback. The student says what they
want and when they need it; a tutor claims it (or the scheduler assigns it, or a
director does); the tutor writes the feedback as a session record, which is how
it reaches the student and the center's reports; and the submission is
returned when that record is final. The record has no appointment: nobody met.

The queue is anonymous until someone claims a submission. Tutors looking for
work see the title, what was asked for and the due date, not the student's name
or the draft, so a tutor never opens work they won't do. Once claimed, the
claimer and the directors see everything.

`QueueSettings` is a center's switch and its limits. A center that never opens
the queue has none of this on its pages.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.files.models import StoredFile
from sympraxi.records.models import SessionRecord


class QueueSettings(models.Model):
    center = models.OneToOneField(Center, on_delete=models.CASCADE, related_name="queue_settings")
    is_open = models.BooleanField(
        _("take submissions"),
        default=False,
        help_text=_("Students see the page for sending a draft only while this is on."),
    )
    lead_days = models.PositiveSmallIntegerField(
        _("shortest turnaround, in days"),
        default=2,
        help_text=_("Students can't ask for feedback sooner than this many days from now."),
    )
    max_files = models.PositiveSmallIntegerField(
        _("most files on a submission"),
        default=5,
        help_text=_("Counts the student's files, not the ones the tutor sends back."),
    )
    auto_assign = models.BooleanField(
        _("assign submissions to tutors automatically"),
        default=False,
        help_text=_(
            "Each new submission goes to the tutor with the fewest open ones, "
            "if tutors can take more. Off, tutors claim submissions themselves."
        ),
    )
    max_open_per_tutor = models.PositiveSmallIntegerField(
        _("most open submissions per tutor"),
        default=3,
        help_text=_("Automatic assignment stops giving a tutor more at this many."),
    )

    class Meta:
        verbose_name_plural = "queue settings"

    def __str__(self) -> str:
        return f"Queue settings for {self.center_id}"


class SubmissionStatus(models.TextChoices):
    WAITING = "waiting", _("Waiting for a tutor")
    CLAIMED = "claimed", _("A tutor is working on it")
    RETURNED = "returned", _("Feedback returned")
    CANCELED = "canceled", _("Canceled")


class Submission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="submissions")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submissions"
    )
    title = models.CharField(_("title"), max_length=200)
    course = models.CharField(_("course"), max_length=100, blank=True)
    wanted = models.TextField(
        _("what feedback do you want?"),
        help_text=_("For example: whether my argument holds, or help with the introduction."),
    )
    due_at = models.DateTimeField(_("needed by"))
    status = models.CharField(
        max_length=10, choices=SubmissionStatus.choices, default=SubmissionStatus.WAITING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # The tutor who has it, from the claim, the assignment or the director's choice.
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claimed_submissions",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    # The written feedback, a session record with no appointment.
    record = models.OneToOneField(
        SessionRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="submission"
    )
    returned_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    # Set when the overdue alert went out, so it goes out once.
    overdue_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_at", "created_at"]
        indexes = [
            models.Index(fields=["center", "status", "due_at"], name="feedback_queue"),
            models.Index(fields=["tutor", "status"], name="feedback_tutor_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["waiting", "canceled"]) | Q(tutor__isnull=False),
                name="feedback_claimed_has_tutor",
            ),
            models.CheckConstraint(
                condition=~Q(status="returned") | Q(returned_at__isnull=False),
                name="feedback_returned_has_time",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status in (SubmissionStatus.WAITING, SubmissionStatus.CLAIMED)


class FileRole(models.TextChoices):
    DRAFT = "draft", _("From the student")
    RETURNED = "returned", _("From the tutor")


class SubmissionFile(models.Model):
    """A file on a submission: the student's draft, or a commented copy sent back."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT, related_name="files")
    file = models.OneToOneField(StoredFile, on_delete=models.PROTECT, related_name="on_submission")
    role = models.CharField(max_length=10, choices=FileRole.choices)

    class Meta:
        ordering = ["file__uploaded_at"]

    def __str__(self) -> str:
        return f"{self.file} on {self.submission_id}"

    @property
    def center(self) -> Center:
        return self.submission.center

    @property
    def student_id(self) -> uuid.UUID:
        return self.submission.student_id
