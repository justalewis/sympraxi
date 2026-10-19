"""A center's choice to use video rooms.

The Jitsi server itself is the installation's (see `sympraxi.online.services`).
Each center decides whether its online appointments get a video room.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


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

    class Meta:
        verbose_name_plural = "video settings"

    def __str__(self) -> str:
        return f"Video settings for {self.center_id}"
