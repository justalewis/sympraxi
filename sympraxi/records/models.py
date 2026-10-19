"""Session records, writing projects, the topic vocabulary and the resource catalog.

A session record is written by the tutor after a session: notes for staff, a
summary addressed to the student, next steps, topic tags on three axes, the
length of the session and any resources attached. Each record is about one
student. A group appointment gets one record per student (see
`services.copy_to_group`), so no student ever sees another student's record.

Records are FERPA education records. Once a record is final it can't be changed
without an `Amendment` that says who changed it, when and why. A database
trigger (migration 0002) enforces that: an UPDATE of a final record, or any
change to its topics or attached resources, is refused unless the transaction
has opened an amendment for that record (`services.amend`).

Topics and the shared resources come from `catalog/` and are loaded by
`manage.py load_catalog`, keyed on their permanent slugs and IDs. Centers add
their own link-only resources, which have a center and no catalog ID.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import Appointment


class Axis(models.TextChoices):
    CONCERN = "concern", _("Concern")
    STAGE = "stage", _("Writing stage")
    ACTION = "action", _("Tutor action")


class Topic(models.Model):
    """One term in the shared topic vocabulary (catalog/vocabulary.yaml).

    Slugs are permanent. A retired term stays, marked deprecated, so old records
    keep their tags.
    """

    slug = models.CharField(max_length=100, unique=True)
    axis = models.CharField(max_length=10, choices=Axis.choices)
    label = models.CharField(max_length=200)
    definition = models.TextField(blank=True)
    # Concern terms such as "citation:apa" have a parent ("citation").
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    # Order in the vocabulary file, which groups related terms.
    position = models.PositiveIntegerField(default=0)
    is_deprecated = models.BooleanField(default=False)
    replaced_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["position"]

    def __str__(self) -> str:
        return self.label


class LinkCheck(models.TextChoices):
    AUTO = "auto", _("Checked automatically")
    MANUAL = "manual", _("Checked by hand")
    OFF = "off", _("Not checked")


class LinkState(models.TextChoices):
    """What the link checker last found out about a resource's address."""

    UNCHECKED = "", _("Not checked yet")
    OK = "ok", _("Works")
    MOVED = "moved", _("Moved")
    # Failed once. A link is only called broken once a later check fails too.
    UNREACHABLE = "unreachable", _("Didn't answer")
    BROKEN = "broken", _("Broken")
    # The site asked not to be checked, or turned the checker away.
    BLOCKED = "blocked", _("Couldn't be checked")


class ResourceQuerySet(models.QuerySet["Resource"]):
    def available_to(self, center: Center) -> "ResourceQuerySet":
        """Active links a tutor in this center can attach: the shared catalog plus its own."""
        return self.filter(Q(center__isnull=True) | Q(center=center), is_active=True)


class Resource(models.Model):
    """A writing resource tutors can attach to a record. Always a link, never a copy.

    Shared entries come from catalog/resources/ and have a `catalog_id`. A center's
    own links have a `center` instead. The license and publisher are kept with
    each entry so the student and the center can see whose page it is.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog_id = models.CharField(max_length=200, unique=True, null=True, blank=True)
    center = models.ForeignKey(
        Center, on_delete=models.CASCADE, null=True, blank=True, related_name="resources"
    )
    title = models.CharField(_("title"), max_length=300)
    url = models.URLField(_("address (URL)"), max_length=500)
    publisher = models.CharField(_("publisher"), max_length=200)
    license = models.CharField(
        _("license"),
        max_length=100,
        default="all-rights-reserved",
        help_text=_(
            "The license the page states, as a short code such as CC-BY-4.0. "
            "Use all-rights-reserved if it states none."
        ),
    )
    license_note = models.TextField(blank=True)
    can_vendor = models.BooleanField(default=False)
    vendor_source_url = models.URLField(max_length=500, blank=True)
    vendor_source_license = models.CharField(max_length=100, blank=True)
    link_check = models.CharField(max_length=10, choices=LinkCheck.choices, default=LinkCheck.AUTO)
    summary = models.TextField(
        _("summary"),
        help_text=_("In your own words, what the page helps a student do. Don't copy its text."),
    )
    topics = models.ManyToManyField(Topic, related_name="resources", blank=True)
    audience = models.JSONField(default=list, blank=True)
    language = models.CharField(_("language"), max_length=10, default="en")
    last_checked = models.DateField(null=True, blank=True)
    last_status = models.PositiveSmallIntegerField(null=True, blank=True)
    # What the link checker (`sympraxi.records.linkcheck`) found at the last check.
    link_state = models.CharField(
        max_length=12, choices=LinkState.choices, blank=True, default=LinkState.UNCHECKED
    )
    link_moved_to = models.URLField(max_length=500, blank=True)
    link_detail = models.CharField(max_length=200, blank=True)
    # The day the link first failed in a row of failed checks.
    link_failing_since = models.DateField(null=True, blank=True)
    # A shared entry that leaves the catalog stays here, inactive, because
    # records still point to it.
    is_active = models.BooleanField(_("offer it to tutors"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ResourceQuerySet.as_manager()

    class Meta:
        ordering = ["title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(catalog_id__isnull=False, center__isnull=True)
                | Q(catalog_id__isnull=True, center__isnull=False),
                name="records_resource_shared_or_local",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_local(self) -> bool:
        return self.center_id is not None


class WritingProject(models.Model):
    """One piece of writing a student brings to several sessions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="writing_projects")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="writing_projects"
    )
    title = models.CharField(
        _("title"), max_length=200, help_text=_("For example, Research paper on urban gardens.")
    )
    course = models.CharField(_("course"), max_length=100, blank=True)
    instructor = models.CharField(_("instructor"), max_length=200, blank=True)
    due_on = models.DateField(_("due date"), null=True, blank=True)
    is_finished = models.BooleanField(
        _("finished"),
        default=False,
        help_text=_("Finished projects stay in the student's history but leave the open list."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_finished", "due_on", "title"]

    def __str__(self) -> str:
        return self.title


class RecordStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    FINAL = "final", _("Final")


class SessionRecord(models.Model):
    """What a tutor writes after a session with one student.

    `appointment` is empty for a meeting that wasn't booked. `staff_notes` are for
    center staff; `summary` and `next_steps` are written to the student. All of it
    is part of the student's education record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="session_records")
    appointment = models.ForeignKey(
        Appointment, on_delete=models.PROTECT, null=True, blank=True, related_name="records"
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="session_records"
    )
    # The tutor who held the session and writes the record.
    tutor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="records_written"
    )
    held_at = models.DateTimeField(_("when the session started"))
    minutes = models.PositiveSmallIntegerField(
        _("length in minutes"),
        validators=[MinValueValidator(1), MaxValueValidator(600)],
        help_text=_("How long the session actually lasted."),
    )
    staff_notes = models.TextField(_("notes for staff"), blank=True)
    summary = models.TextField(_("summary for the student"), blank=True)
    next_steps = models.TextField(_("next steps"), blank=True)
    topics = models.ManyToManyField(Topic, through="RecordTopic", related_name="records")
    project = models.ForeignKey(
        WritingProject, on_delete=models.PROTECT, null=True, blank=True, related_name="records"
    )
    status = models.CharField(
        max_length=10, choices=RecordStatus.choices, default=RecordStatus.DRAFT
    )
    # Set when this draft was started from another student's record in the same group.
    copied_from = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-held_at"]
        indexes = [
            models.Index(fields=["center", "student", "-held_at"], name="records_center_student"),
            models.Index(fields=["tutor", "status"], name="records_tutor_status"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "student"],
                condition=Q(appointment__isnull=False),
                name="records_one_per_student_per_appointment",
            ),
            models.CheckConstraint(
                condition=Q(status=RecordStatus.DRAFT) | Q(finalized_at__isnull=False),
                name="records_final_has_time",
            ),
            models.CheckConstraint(condition=Q(minutes__gte=1), name="records_minutes_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.student} {self.held_at:%Y-%m-%d}"

    @property
    def is_final(self) -> bool:
        return self.status == RecordStatus.FINAL


class RecordTopic(models.Model):
    """A topic tag on a record. A table of its own so the trigger can guard it."""

    record = models.ForeignKey(SessionRecord, on_delete=models.CASCADE, related_name="+")
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["record", "topic"], name="records_topic_once"),
        ]

    def __str__(self) -> str:
        return f"{self.topic_id} on {self.record_id}"


class AttachedResource(models.Model):
    """A resource attached to a record, with an optional note to the student."""

    record = models.ForeignKey(SessionRecord, on_delete=models.CASCADE, related_name="attachments")
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="attachments")
    note = models.TextField(_("note for the student"), blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "added_at"]
        constraints = [
            models.UniqueConstraint(fields=["record", "resource"], name="records_resource_once"),
        ]

    def __str__(self) -> str:
        return f"{self.resource_id} on {self.record_id}"


class Amendment(models.Model):
    """A change to a final record: who, when, why, and each field before and after.

    `changes` maps a field name to {"before": ..., "after": ...}. Text fields hold
    the text; topics are lists of slugs; resources are lists of {id, title, note}.
    Append-only, like the audit log.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey(SessionRecord, on_delete=models.CASCADE, related_name="amendments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(_("reason for the change"))
    changes = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Amendment to {self.record_id} at {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def fields_changed(self) -> list[str]:
        return list(self.changes)


class RecordReminder(models.Model):
    """A tutor was reminded about one appointment's missing records.

    One per appointment and tutor, ever: the unique constraint stops two
    schedulers, or a sweep run twice, from sending the same reminder
    (`sympraxi/records/reminders.py`).
    """

    appointment = models.ForeignKey(
        Appointment, on_delete=models.CASCADE, related_name="record_reminders"
    )
    tutor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    sent_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["appointment", "tutor"], name="records_reminder_once"),
        ]

    def __str__(self) -> str:
        return f"Record reminder for {self.appointment_id} to {self.tutor_id}"


class LinkCheckRun(models.Model):
    """One pass of the link checker over the links that were due."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    checked = models.PositiveIntegerField(default=0)
    ok = models.PositiveIntegerField(default=0)
    moved = models.PositiveIntegerField(default=0)
    broken = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Link check {self.started_at:%Y-%m-%d %H:%M}"


class ResourceFavorite(models.Model):
    """A resource a tutor keeps close: it shows first in their resource panel.

    A favorite belongs to a person, not a center. A center's own link is offered only
    in that center, so a favorite of one is simply not shown elsewhere.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorite_resources"
    )
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="favorites")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "resource"], name="records_favorite_once"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} likes {self.resource_id}"


class GoalStatus(models.TextChoices):
    OPEN = "open", _("Open")
    MET = "met", _("Met")
    DROPPED = "dropped", _("Dropped")


class Goal(models.Model):
    """Something a student wants to get better at, kept until it is met or dropped.

    A student writes their own; a tutor who works with them, or a director, can write
    one with them. Open goals show in the tutor's brief before a session. A goal is part
    of the student's education record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="goals")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="goals"
    )
    text = models.CharField(
        _("goal"),
        max_length=300,
        help_text=_("For example: use evidence in every body paragraph."),
    )
    target_on = models.DateField(_("by when"), null=True, blank=True)
    status = models.CharField(max_length=10, choices=GoalStatus.choices, default=GoalStatus.OPEN)
    project = models.ForeignKey(
        WritingProject, on_delete=models.SET_NULL, null=True, blank=True, related_name="goals"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["center", "student", "status"], name="records_goal_student")
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="open") | Q(closed_at__isnull=False),
                name="records_goal_closed_has_time",
            ),
        ]

    def __str__(self) -> str:
        return self.text
