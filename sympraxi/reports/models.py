"""A director's full export of a center, built by the task worker.

The export itself is a ZIP file in the `exports` storage
(`sympraxi/reports/center_export.py` writes it). This row says who asked for
it, how far along it is, and when its download link stops working. Once the
link expires the scheduler deletes the file and keeps the row, so the page can
still say who exported what and when; the audit log has the same story.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


class ExportStatus(models.TextChoices):
    QUEUED = "queued", _("Waiting to start")
    BUILDING = "building", _("Being built")
    READY = "ready", _("Ready to download")
    FAILED = "failed", _("Failed")
    EXPIRED = "expired", _("Expired")


# An export still on its way: a center has at most one of these at a time.
UNFINISHED = (ExportStatus.QUEUED, ExportStatus.BUILDING)


class CenterExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="exports")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, choices=ExportStatus.choices, default=ExportStatus.QUEUED
    )
    # The file's name in the `exports` storage, while there is a file.
    file_name = models.CharField(max_length=200, blank=True)
    size = models.PositiveBigIntegerField(null=True, blank=True)
    # How many of each thing the export holds, by file name.
    counts = models.JSONField(default=dict, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["center"],
                condition=Q(status__in=UNFINISHED),
                name="reports_one_unfinished_export_per_center",
            ),
            models.CheckConstraint(
                condition=~Q(status=ExportStatus.READY)
                | Q(ready_at__isnull=False, expires_at__isnull=False) & ~Q(file_name=""),
                name="reports_ready_export_has_file",
            ),
        ]

    def __str__(self) -> str:
        return f"Export of {self.center_id} at {self.requested_at:%Y-%m-%d %H:%M}"

    def can_download(self, now: object) -> bool:
        return (
            self.status == ExportStatus.READY
            and self.expires_at is not None
            and self.expires_at > now  # type: ignore[operator]
        )


class ReportQuestions(models.Model):
    """Which questions tell the reports a student's class standing and a session's course.

    A question is named by its permanent key, which every version of its form keeps, so
    the choice survives edits to the form. See `sympraxi.reports.demographics`.
    """

    center = models.OneToOneField(Center, on_delete=models.CASCADE, related_name="report_questions")
    standing_key = models.UUIDField(null=True, blank=True)
    course_key = models.UUIDField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "report questions"

    def __str__(self) -> str:
        return f"Report questions for {self.center_id}"


class ResearchSettings(models.Model):
    """The IRB protocol a center's research export is made under.

    The export (`sympraxi.reports.research`) works only while the approval dates cover
    today. These are facts a director records; Sympraxi can't check them with the IRB.
    """

    center = models.OneToOneField(
        Center, on_delete=models.CASCADE, related_name="research_settings"
    )
    protocol_number = models.CharField(_("IRB protocol number"), max_length=100, blank=True)
    investigator = models.CharField(_("investigator"), max_length=200, blank=True)
    approved_on = models.DateField(_("approved on"), null=True, blank=True)
    expires_on = models.DateField(_("approved through"), null=True, blank=True)

    class Meta:
        verbose_name_plural = "research settings"

    def __str__(self) -> str:
        return f"Research settings for {self.center_id}"
