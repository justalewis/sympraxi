"""Text message reminders: who agreed, and what was sent.

Nobody gets a text without having asked. A person gives a phone number and ticks an
unchecked box that carries the exact wording they are agreeing to; that wording and the
time are kept. They then prove the number is theirs with a six-digit code sent to it.
Only a verified, unrevoked consent can be texted. A reply of STOP (or any of its usual
words) turns every consent on that number off, whatever site page the person last saw.
The phone number is kept encrypted, with a keyed hash beside it so an incoming STOP can
find it.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SmsConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sms_consents"
    )
    phone_encrypted = models.TextField()
    phone_hash = models.CharField(max_length=64, db_index=True)
    # The wording the person saw when they ticked the box, and which version of it.
    consent_text = models.TextField()
    consent_version = models.CharField(max_length=20)
    consented_at = models.DateTimeField(default=timezone.now)
    consent_ip = models.GenericIPAddressField(null=True, blank=True)
    code_hash = models.CharField(max_length=64, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    code_sent_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # How it ended: "web" (the person turned it off), "stop" (they texted STOP),
    # "carrier" (the provider said the number can't be texted).
    revoked_via = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["-consented_at"]
        constraints = [
            # One live consent per person; starting again replaces the pending one.
            models.UniqueConstraint(
                fields=["user"], condition=Q(revoked_at__isnull=True), name="sms_one_live_consent"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.consent_version})"

    @property
    def is_active(self) -> bool:
        return self.verified_at is not None and self.revoked_at is None


class MessageStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to be sent")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")
    SKIPPED = "skipped", _("Not sent")


class SmsMessage(models.Model):
    """A record that a text was sent, without its words."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consent = models.ForeignKey(SmsConsent, on_delete=models.CASCADE, related_name="messages")
    purpose = models.CharField(max_length=20)
    # What to say, kept only until it is sent.
    body = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=MessageStatus.choices, default=MessageStatus.PENDING
    )
    provider_id = models.CharField(max_length=60, blank=True)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.purpose} to {self.consent.user}"
