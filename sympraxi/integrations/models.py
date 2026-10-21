"""API tokens and webhooks.

A token belongs to one center and acts as the director who made it, limited to the
scopes it was given. It is shown once, when it is made; only a hash is kept. A
webhook endpoint is a web address that is told, with a signed request, when
something happens in the center. Neither carries a student's name: a webhook says
which appointment changed, and the receiver asks the API for the rest.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center

# What a token may do. Each is a separate grant, so a reporting tool gets reads only.
SCOPES = {
    "schedules:read": _("See the center's schedules and tutors"),
    "appointments:read": _("See appointments, including which students are in them"),
    "appointments:write": _("Book and cancel appointments"),
    "students:read": _("See the center's students: names, emails and student IDs"),
    "workshops:read": _("See workshops and who registered"),
}


class ApiToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(_("what it's for"), max_length=100)
    # The first characters of the token, so a director can tell tokens apart.
    prefix = models.CharField(max_length=12)
    token_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(_("expires"), null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @property
    def is_usable(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class WebhookEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="webhooks")
    url = models.URLField(_("address"), max_length=500)
    description = models.CharField(_("what it's for"), max_length=200, blank=True)
    # The signing secret, encrypted with the site's secret key.
    secret_encrypted = models.TextField()
    events = models.JSONField(default=list)
    is_active = models.BooleanField(_("on"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.url


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to be sent")
    DELIVERED = "delivered", _("Delivered")
    FAILED = "failed", _("Gave up")


class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=50)
    payload = models.JSONField()
    status = models.CharField(
        max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["next_attempt_at"],
                condition=Q(status="pending"),
                name="integrations_delivery_due",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event} to {self.endpoint}"
