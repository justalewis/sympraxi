"""Calendar feeds, announcements and mass email: what Sympraxi tells people."""

import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


def feed_token_hash(token: str) -> str:
    """How a calendar feed's token is stored. Its own prefix keeps it from ever
    matching a survey link's hash (`sympraxi.forms.models.token_hash`)."""
    return hashlib.sha256(b"sympraxi-calendar:" + token.encode()).hexdigest()


class CalendarFeed(models.Model):
    """A person's private calendar feed: their appointments, at a secret address.

    Only a hash of the address's token is kept, so the address can't be read back
    from the database or a backup. Replacing the address makes a new token and
    the old one stops working; turning the feed off deletes the row.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_feed"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # When a calendar app last fetched it, to the hour, so people can tell
    # whether a subscription is still in use.
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Calendar feed for {self.user}"


class Audience(models.TextChoices):
    """Who sees an announcement in the center."""

    EVERYONE = "everyone", _("Everyone in the center")
    STUDENTS = "students", _("Students")
    STAFF = "staff", _("Tutors, front desk staff and directors")


class Announcement(models.Model):
    """A notice on the center page, and on the schedules page for students, between
    two days. Plain text: line breaks and web addresses are kept, nothing else."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="announcements")
    title = models.CharField(_("title"), max_length=120)
    body = models.TextField(_("what it says"), max_length=2000)
    audience = models.CharField(
        _("who sees it"), max_length=10, choices=Audience.choices, default=Audience.EVERYONE
    )
    starts_on = models.DateField(_("first day shown"))
    ends_on = models.DateField(_("last day shown"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_on", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")),
                name="notify_announcement_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class MassEmail(models.Model):
    """One email a director sent to many people in the center, kept as a log.

    The list of who it went to is kept too (`MassEmailRecipient`), so a director
    can answer "did they get it?" months later.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="mass_emails")
    subject = models.CharField(_("subject"), max_length=150)
    body = models.TextField(_("message"), max_length=10000)
    # Who it was for, in words, as the director chose it.
    audience = models.CharField(max_length=300)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Students who fit the audience but turned off center news.
    skipped = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.subject


class MassEmailRecipient(models.Model):
    mass_email = models.ForeignKey(MassEmail, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    # Set when the task worker queued this person's copy. Each is queued once.
    queued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mass_email", "user"], name="notify_mass_email_recipient_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.mass_email} to {self.user}"
