"""Files people attach to appointments: drafts, assignment sheets, returned work.

A `StoredFile` is the file itself, kept in the private `uploads` storage under a
random name, with what Sympraxi learned about it: its type from its content, its
size and SHA-256, and whether ClamAV has scanned it. An `AppointmentFile` puts it
on an appointment and says which student it belongs to, so in a group each
student's drafts stay theirs.

A file can be opened only when it is `ready`: scanned clean, or not scanned
because the installation has no scanner. A removed file keeps its row, for the
record, and loses its content.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import Appointment


class FileKind(models.TextChoices):
    PDF = "pdf", _("PDF")
    DOCX = "docx", _("Word document")
    DOC = "doc", _("Older Word document")
    ODT = "odt", _("OpenDocument text")
    RTF = "rtf", _("Rich text")
    TXT = "txt", _("Plain text")


class ScanStatus(models.TextChoices):
    PENDING = "pending", _("Being checked for viruses")
    CLEAN = "clean", _("Checked")
    INFECTED = "infected", _("Removed: a virus scanner flagged it")
    # The scanner couldn't be reached; the scheduler tries again.
    ERROR = "error", _("Waiting for the virus check")
    NOT_SCANNED = "not_scanned", _("Not scanned")


READY = (ScanStatus.CLEAN, ScanStatus.NOT_SCANNED)


class PdfStatus(models.TextChoices):
    """Whether a Word, OpenDocument, rich text or plain text file has a PDF copy to view."""

    NONE = "", _("No viewable copy")
    PENDING = "pending", _("Making a viewable copy")
    READY = "ready", _("Viewable")
    FAILED = "failed", _("Couldn't make a viewable copy")


# The kinds the worker can turn into PDFs; a PDF is its own viewable copy.
CONVERTIBLE = (FileKind.DOCX, FileKind.DOC, FileKind.ODT, FileKind.RTF, FileKind.TXT)


class StoredFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.PROTECT, related_name="+")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    # The name the person's computer gave it, cleaned up. Never used as a path.
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=FileKind.choices)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    # Its name in the `uploads` storage; empty once removed.
    storage_name = models.CharField(max_length=200, blank=True)
    scan_status = models.CharField(max_length=12, choices=ScanStatus.choices)
    scan_detail = models.CharField(max_length=200, blank=True)
    scan_attempts = models.PositiveSmallIntegerField(default=0)
    scanned_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The PDF the worker made from a document, in the same private storage. Empty for
    # a PDF, which is viewed as it is, and until the copy is made.
    pdf_name = models.CharField(max_length=200, blank=True)
    pdf_status = models.CharField(max_length=10, choices=PdfStatus.choices, blank=True)
    pdf_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_ready(self) -> bool:
        return self.removed_at is None and self.scan_status in READY and bool(self.storage_name)

    @property
    def viewable_name(self) -> str:
        """The storage name of the PDF to show in the viewer, or empty if there is none."""
        if not self.is_ready:
            return ""
        if self.kind == FileKind.PDF:
            return self.storage_name
        return self.pdf_name if self.pdf_status == PdfStatus.READY else ""


class AppointmentFile(models.Model):
    """A file on an appointment, belonging to one of its students."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.ForeignKey(Appointment, on_delete=models.PROTECT, related_name="files")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appointment_files"
    )
    file = models.OneToOneField(StoredFile, on_delete=models.PROTECT, related_name="on_appointment")
    note = models.CharField(_("note"), max_length=300, blank=True)

    class Meta:
        ordering = ["file__uploaded_at"]

    def __str__(self) -> str:
        return f"{self.file} on {self.appointment_id}"

    @property
    def center(self) -> Center:
        return self.appointment.schedule.center
