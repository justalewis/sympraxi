import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

ACTION_LABELS = {
    "auth.login": _("Signed in"),
    "auth.login_failed": _("Failed sign-in"),
    "auth.logout": _("Signed out"),
    "auth.password_changed": _("Changed password"),
    "auth.password_reset": _("Reset password"),
    "auth.email_changed": _("Changed email address"),
    "auth.profile_changed": _("Changed their name or pronouns"),
    "auth.mfa_added": _("Added a two-factor method"),
    "auth.mfa_removed": _("Removed a two-factor method"),
    "auth.mfa_recovery_codes_reset": _("Generated new recovery codes"),
    "auth.sso_linked": _("Linked a college sign-in account"),
    "auth.sso_unlinked": _("Unlinked a college sign-in account"),
    "access.membership_added": _("Added someone to the center"),
    "access.membership_changed": _("Changed someone's role"),
    "access.membership_removed": _("Removed someone from the center"),
    "access.account_created": _("Created an account for someone added to a center"),
    "access.people_imported": _("Added people from a CSV file"),
    "access.roles_turned_off": _("Turned off everyone's role of one kind"),
    "privacy.audit_log_viewed": _("Viewed the audit log"),
    "privacy.disclosure_recorded": _("Recorded a disclosure of a student's records"),
    "privacy.disclosures_viewed": _("Read a student's record of disclosures"),
    "scheduling.appointment_booked": _("Booked an appointment"),
    "scheduling.appointment_joined": _("Added a student to a group appointment"),
    "scheduling.appointment_changed": _("Changed an appointment"),
    "scheduling.appointment_confirmed": _("Confirmed a tentative appointment"),
    "scheduling.appointment_canceled": _("Canceled an appointment"),
    "scheduling.appointment_left": _("Took a student out of a group appointment"),
    "scheduling.exemption_added": _("Exempted a student from booking limits"),
    "scheduling.exemption_revoked": _("Ended a booking-limit exemption"),
    "scheduling.attendance_marked": _("Marked attendance"),
    "scheduling.attendance_waived": _("Waived a late cancellation or no-show"),
    "scheduling.block_set": _("Paused a student's online booking"),
    "scheduling.block_lifted": _("Lifted a pause on a student's online booking"),
    "scheduling.attendance_settings_changed": _("Changed the booking block limits"),
    "records.record_started": _("Started a session record"),
    "records.record_saved": _("Saved a draft session record"),
    "records.record_finalized": _("Finalized a session record"),
    "records.record_amended": _("Amended a final session record"),
    "records.record_viewed": _("Read a session record"),
    "records.history_viewed": _("Read a student's session history"),
    "records.student_record_exported": _("Exported a student's complete record"),
    "records.project_saved": _("Saved a writing project"),
    "records.resource_saved": _("Saved one of the center's resources"),
    "records.settings_changed": _("Changed the session record settings"),
    "forms.form_created": _("Added a form"),
    "forms.form_changed": _("Started or stopped asking a form's questions"),
    "forms.form_published": _("Published a new version of a form"),
    "forms.answered": _("Answered a registration or intake form"),
    "forms.answers_viewed": _("Read a student's registration or intake answers"),
    "forms.survey_settings_changed": _("Changed when the survey goes out"),
    "reports.list_viewed": _("Viewed a list of appointments or students"),
    "reports.list_exported": _("Downloaded a list of appointments or students"),
    "reports.center_export_requested": _("Asked for a full export of the center"),
    "reports.center_export_downloaded": _("Downloaded the full export of the center"),
    "staff.clocked_in": _("Clocked in"),
    "staff.clocked_out": _("Clocked out"),
    "staff.closed_automatically": _("Clocked out at midnight by the scheduler"),
    "staff.entry_added": _("Added time for someone"),
    "staff.entry_changed": _("Changed someone's time"),
    "staff.entry_removed": _("Removed someone's time"),
    "notify.calendar_feed_started": _("Turned on a calendar feed"),
    "notify.calendar_feed_replaced": _("Gave a calendar feed a new address"),
    "notify.calendar_feed_turned_off": _("Turned off a calendar feed"),
    "notify.mass_email_sent": _("Emailed a group of the center's people"),
}


class AuditEvent(models.Model):
    """One entry in the append-only audit log.

    A database trigger (migration 0002) rejects UPDATE and DELETE on this table,
    so entries can't be edited or quietly removed, even from a database shell.
    Retention purges will set `sympraxi.audit_purge` for their transaction.

    The actor and center are kept as IDs without foreign-key constraints, plus a
    label captured at the time, so deleting a user or center never rewrites history.
    """

    occurred_at = models.DateTimeField(_("when"), default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_label = models.CharField(_("who"), max_length=254, blank=True)
    action = models.CharField(_("action"), max_length=64, db_index=True)
    center = models.ForeignKey(
        "centers.Center",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["center", "-occurred_at"], name="privacy_audit_center_time")
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action} by {self.actor_label or '-'}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Audit events are append-only.")

    @property
    def action_label(self) -> str:
        return str(ACTION_LABELS.get(self.action, self.action))


class DisclosureBasis(models.TextChoices):
    """The FERPA provision a disclosure without consent relied on (34 CFR 99.31)."""

    OTHER_SCHOOL = "other_school", _("Another school the student is enrolling in (99.31(a)(2))")
    AUTHORITIES = "authorities", _("Federal, state or local education authorities (99.31(a)(3))")
    FINANCIAL_AID = "financial_aid", _("Financial aid the student applied for (99.31(a)(4))")
    STUDIES = "studies", _("An organization doing studies for the college (99.31(a)(6))")
    ACCREDITING = "accrediting", _("An accrediting organization (99.31(a)(7))")
    PARENTS = "parents", _("Parents of a dependent student (99.31(a)(8))")
    SUBPOENA = "subpoena", _("A court order or lawfully issued subpoena (99.31(a)(9))")
    EMERGENCY = "emergency", _("A health or safety emergency (99.31(a)(10))")
    CONSENT = "consent", _("The student's signed, written consent (99.30)")
    OTHER = "other", _("Something else, explained in the note")


class Disclosure(models.Model):
    """One entry in a student's record of disclosures (34 CFR 99.32).

    FERPA asks a school to record each request for, and each disclosure of, a
    student's education records to someone outside the school's own officials:
    who asked or received them and their legitimate interest. The student may
    inspect that record, and it is kept as long as the records are. Entries are
    append-only (migration 0004); a mistake is fixed by a new entry that
    `corrects` the old one, so the record shows both.

    Disclosures to the student, to the center's own staff, and of directory
    information don't need an entry (99.32(d)); recording one anyway does no harm.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    center = models.ForeignKey("centers.Center", on_delete=models.PROTECT, related_name="+")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="disclosures"
    )
    # False for a request that was refused or is still open.
    disclosed = models.BooleanField(_("records were disclosed"), default=True)
    requested_on = models.DateField(_("asked on"), null=True, blank=True)
    disclosed_on = models.DateField(_("disclosed on"), null=True, blank=True)
    party = models.CharField(_("who asked for or received them"), max_length=200)
    interest = models.CharField(_("their legitimate interest"), max_length=500)
    basis = models.CharField(_("under"), max_length=20, choices=DisclosureBasis.choices)
    records = models.CharField(_("which records"), max_length=300)
    note = models.CharField(_("note"), max_length=500, blank=True)
    corrects = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
        verbose_name=_("corrects the entry"),
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(disclosed=False) | models.Q(disclosed_on__isnull=False),
                name="privacy_disclosure_has_its_date",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_on__isnull=False)
                | models.Q(disclosed_on__isnull=False),
                name="privacy_disclosure_has_a_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.disclosed_on or self.requested_on}: {self.party}"
