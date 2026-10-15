import uuid
import zoneinfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


def validate_time_zone(value: str) -> None:
    if value not in zoneinfo.available_timezones():
        raise ValidationError(_("%(value)s is not a known time zone."), params={"value": value})


class Role(models.TextChoices):
    STUDENT = "student", _("Student")
    TUTOR = "tutor", _("Tutor")
    FRONT_DESK = "front_desk", _("Front desk")
    DIRECTOR = "director", _("Director")


STAFF_ROLES = frozenset({Role.TUTOR, Role.FRONT_DESK, Role.DIRECTOR})


class Center(models.Model):
    """A writing center (or any tutoring center) inside one installation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("short name for links"), unique=True)
    time_zone = models.CharField(
        _("time zone"), max_length=64, default="America/New_York", validators=[validate_time_zone]
    )
    contact_email = models.EmailField(_("contact email"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("centers:detail", kwargs={"slug": self.slug})


class Membership(models.Model):
    """A person's role in one center. A person may hold several roles in a center."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["center__name", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "center", "role"], name="centers_membership_unique_role"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.get_role_display()} · {self.center}"
