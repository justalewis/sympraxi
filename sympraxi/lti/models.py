"""A learning management system (Canvas, Moodle, D2L) registered to launch into a center.

Only a site administrator registers one, in the admin: a platform's word about who
someone is is trusted to sign them in, so the registration is a host decision, not
a center director's.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center


class LtiPlatform(models.Model):
    center = models.ForeignKey(Center, on_delete=models.CASCADE, related_name="lti_platforms")
    name = models.CharField(_("name"), max_length=100, help_text=_("For example: Canvas"))
    issuer = models.URLField(_("issuer"), max_length=300)
    client_id = models.CharField(_("client ID"), max_length=200)
    login_url = models.URLField(_("authorization address"), max_length=500)
    jwks_url = models.URLField(_("public keys address"), max_length=500)
    deployment_ids = models.JSONField(
        _("deployment IDs"),
        default=list,
        help_text=_("A list of the deployment IDs the platform gives this tool."),
    )
    is_active = models.BooleanField(_("on"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["issuer", "client_id"], name="lti_platform_unique")
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.center})"


class LtiLink(models.Model):
    """Ties an account to the identifier one platform gives the person (the `sub` claim)."""

    platform = models.ForeignKey(LtiPlatform, on_delete=models.CASCADE, related_name="links")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    subject = models.CharField(max_length=255)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["platform", "subject"], name="lti_link_subject"),
            models.UniqueConstraint(fields=["platform", "user"], name="lti_link_user"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} at {self.platform}"


class LtiNonce(models.Model):
    """A launch's one-time value. Used up when the launch arrives, so it can't be replayed."""

    nonce = models.CharField(max_length=64, primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.nonce
