"""A center's online-session settings, and the shared documents of its sessions.

The Jitsi server and the shared-editor service are the installation's (see
`sympraxi.online.services` and `sympraxi.online.room`). Each center decides whether its
appointments get video rooms and a shared document.

A `RoomDocument` is the document two or more people write in during a session:
the editor's content as a ProseMirror document (what Sympraxi shows and exports), the
chat, and the Yjs state the editor service needs to carry on where it left off.
It is part of the student's education record, like the session record it feeds.
`RoomVersion`s are its history.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.scheduling.models import Appointment


class VideoSettings(models.Model):
    center = models.OneToOneField(Center, on_delete=models.CASCADE, related_name="video_settings")
    is_on = models.BooleanField(
        _("give online appointments a video room"),
        default=False,
        help_text=_(
            "Each online appointment gets its own room, opened from the appointment page. "
            "A tutor's meeting link, if they have one, still works."
        ),
    )
    room_is_on = models.BooleanField(
        _("give appointments a shared document"),
        default=False,
        help_text=_(
            "The tutor and the students write in one document during the session, with a chat "
            "beside it. It is kept with the appointment, with its history."
        ),
    )

    class Meta:
        verbose_name_plural = "video settings"

    def __str__(self) -> str:
        return f"Video settings for {self.center_id}"


class RoomDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        Appointment, on_delete=models.PROTECT, related_name="shared_document"
    )
    # What the editor service needs to carry on: Yjs's encoded state, and the
    # content and chat as plain data for showing, searching and exporting.
    state = models.BinaryField(default=bytes)
    content = models.JSONField(default=dict)
    chat = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Shared document for {self.appointment_id}"

    @property
    def center(self) -> Center:
        return self.appointment.schedule.center


class RoomVersion(models.Model):
    """A saved copy of the document: automatic every few minutes, or by someone's choice."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(RoomDocument, on_delete=models.PROTECT, related_name="versions")
    created_at = models.DateTimeField(auto_now_add=True)
    # "Saved automatically", or the name of the person who saved it.
    label = models.CharField(max_length=120)
    content = models.JSONField(default=dict)
    chat = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.label} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def center(self) -> Center:
        return self.document.center
