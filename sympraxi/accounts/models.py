import uuid
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> "User":
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """A person who signs in: student, tutor, staff member or site administrator.

    Roles are not stored here. They come from memberships in centers
    (`sympraxi.centers.models.Membership`), so one person can be a student in one
    center and a tutor in another. `is_superuser` marks site administrators, who
    run the installation but do not automatically see student records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None  # type: ignore[assignment]
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = models.EmailField(_("email address"), unique=True)
    full_name = models.CharField(_("full name"), max_length=200)
    preferred_name = models.CharField(
        _("preferred name"),
        max_length=100,
        blank=True,
        help_text=_("Shown instead of the full name wherever possible."),
    )
    pronouns = models.CharField(_("pronouns"), max_length=50, blank=True)
    student_id = models.CharField(
        _("student ID"), max_length=64, blank=True, null=True, unique=True
    )
    # Reminder emails before appointments: on for students, off for tutors
    # unless they turn them on.
    reminders_for_appointments = models.BooleanField(
        _("email me before appointments I booked"), default=True
    )
    reminders_for_tutoring = models.BooleanField(
        _("email me before appointments I'm tutoring"), default=False
    )
    # The survey a center sends after a session the student came to.
    survey_emails = models.BooleanField(
        _("email me a short survey after my sessions"),
        default=True,
        help_text=_("The answers are anonymous: the center sees them without your name."),
    )
    # Emails a center sends to many of its students at once. Emails about the
    # person's own appointments go out either way, and staff always get them.
    center_news_emails = models.BooleanField(
        _("email me news from centers where I'm a student"),
        default=True,
        help_text=_(
            "Announcements a center emails to its students, such as new hours or "
            "workshops. Emails about your own appointments come either way."
        ),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["full_name"]

    objects = UserManager()  # type: ignore[assignment,misc]

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.preferred_name or self.full_name or self.email

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.display_name


class CasLink(models.Model):
    """Ties an account to the name a CAS server knows the person by.

    The first CAS sign-in links the name to the account with the person's address; later
    sign-ins match on the name, so an address handed to someone new doesn't hand over the
    old person's account.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="cas_link")
    cas_user = models.CharField(max_length=200, unique=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user} is {self.cas_user} at CAS"


class SamlLink(models.Model):
    """Ties an account to the NameID a SAML identity provider knows the person by.

    The first SAML sign-in links it to the account with the person's address; later
    sign-ins match on the NameID, so an address handed to someone new doesn't hand over
    the old person's account.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="saml_link")
    name_id = models.CharField(max_length=255, unique=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user} is {self.name_id} at the SAML provider"


class SamlRequest(models.Model):
    """A sign-in request Sympraxi sent. The response that cites its ID uses it up."""

    request_id = models.CharField(max_length=100, primary_key=True)
    next_url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.request_id
