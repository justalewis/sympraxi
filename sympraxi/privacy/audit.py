"""Write entries to the audit log.

Call `record()` whenever someone signs in, changes access, or reads or changes a
sensitive record. Keep `details` free of student work and note text: record what
happened and to which object, not the content.
"""

from enum import StrEnum
from typing import Any

from django.conf import settings
from django.db.models import Model
from django.http import HttpRequest

from sympraxi.privacy.models import AuditEvent


class AuditAction(StrEnum):
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105 (an event name, not a secret)
    PASSWORD_RESET = "auth.password_reset"  # noqa: S105
    EMAIL_CHANGED = "auth.email_changed"
    PROFILE_CHANGED = "auth.profile_changed"
    MFA_ADDED = "auth.mfa_added"
    MFA_REMOVED = "auth.mfa_removed"
    MFA_RECOVERY_CODES_RESET = "auth.mfa_recovery_codes_reset"
    SSO_LINKED = "auth.sso_linked"
    SSO_UNLINKED = "auth.sso_unlinked"
    MEMBERSHIP_ADDED = "access.membership_added"
    MEMBERSHIP_CHANGED = "access.membership_changed"
    MEMBERSHIP_REMOVED = "access.membership_removed"
    ACCOUNT_CREATED = "access.account_created"
    PEOPLE_IMPORTED = "access.people_imported"
    ROLES_TURNED_OFF = "access.roles_turned_off"
    AUDIT_LOG_VIEWED = "privacy.audit_log_viewed"
    DISCLOSURE_RECORDED = "privacy.disclosure_recorded"
    DISCLOSURES_VIEWED = "privacy.disclosures_viewed"
    APPOINTMENT_BOOKED = "scheduling.appointment_booked"
    APPOINTMENT_JOINED = "scheduling.appointment_joined"
    APPOINTMENT_CHANGED = "scheduling.appointment_changed"
    APPOINTMENT_CONFIRMED = "scheduling.appointment_confirmed"
    APPOINTMENT_CANCELED = "scheduling.appointment_canceled"
    APPOINTMENT_LEFT = "scheduling.appointment_left"
    EXEMPTION_ADDED = "scheduling.exemption_added"
    EXEMPTION_REVOKED = "scheduling.exemption_revoked"
    ATTENDANCE_MARKED = "scheduling.attendance_marked"
    ATTENDANCE_WAIVED = "scheduling.attendance_waived"
    BLOCK_SET = "scheduling.block_set"
    BLOCK_LIFTED = "scheduling.block_lifted"
    ATTENDANCE_SETTINGS_CHANGED = "scheduling.attendance_settings_changed"
    RECORD_STARTED = "records.record_started"
    RECORD_SAVED = "records.record_saved"
    RECORD_FINALIZED = "records.record_finalized"
    RECORD_AMENDED = "records.record_amended"
    RECORD_VIEWED = "records.record_viewed"
    HISTORY_VIEWED = "records.history_viewed"
    STUDENT_RECORD_EXPORTED = "records.student_record_exported"
    PROJECT_SAVED = "records.project_saved"
    RESOURCE_SAVED = "records.resource_saved"
    RECORD_SETTINGS_CHANGED = "records.settings_changed"
    FORM_CREATED = "forms.form_created"
    FORM_CHANGED = "forms.form_changed"
    FORM_PUBLISHED = "forms.form_published"
    FORM_ANSWERED = "forms.answered"
    ANSWERS_VIEWED = "forms.answers_viewed"
    SURVEY_SETTINGS_CHANGED = "forms.survey_settings_changed"
    LIST_VIEWED = "reports.list_viewed"
    LIST_EXPORTED = "reports.list_exported"
    CENTER_EXPORT_REQUESTED = "reports.center_export_requested"
    CENTER_EXPORT_DOWNLOADED = "reports.center_export_downloaded"
    TIME_CLOCKED_IN = "staff.clocked_in"
    TIME_CLOCKED_OUT = "staff.clocked_out"
    TIME_CLOSED_AUTOMATICALLY = "staff.closed_automatically"
    TIME_ENTRY_ADDED = "staff.entry_added"
    TIME_ENTRY_CHANGED = "staff.entry_changed"
    TIME_ENTRY_REMOVED = "staff.entry_removed"
    CALENDAR_FEED_STARTED = "notify.calendar_feed_started"
    CALENDAR_FEED_REPLACED = "notify.calendar_feed_replaced"
    CALENDAR_FEED_TURNED_OFF = "notify.calendar_feed_turned_off"
    MASS_EMAIL_SENT = "notify.mass_email_sent"
    FILE_UPLOADED = "files.uploaded"
    FILE_DOWNLOADED = "files.downloaded"
    FILE_REMOVED = "files.removed"
    FILE_INFECTED = "files.infected"
    FILE_VIEWED = "files.viewed"
    SUBMISSION_SENT = "feedback.submission_sent"
    SUBMISSION_CLAIMED = "feedback.submission_claimed"
    SUBMISSION_ASSIGNED = "feedback.submission_assigned"
    SUBMISSION_RETURNED = "feedback.submission_returned"
    SUBMISSION_CANCELED = "feedback.submission_canceled"
    QUEUE_SETTINGS_CHANGED = "feedback.settings_changed"
    GOAL_ADDED = "records.goal_added"
    GOAL_CHANGED = "records.goal_changed"
    TIMELINE_VIEWED = "records.timeline_viewed"
    LINK_CHECK_RUN = "records.link_check_run"
    LINK_CHECKED = "records.link_checked"
    COMMENT_ADDED = "feedback.comment_added"
    COMMENT_REMOVED = "feedback.comment_removed"
    VIDEO_JOINED = "online.video_joined"
    ROOM_OPENED = "online.room_opened"
    ROOM_VERSION_SAVED = "online.room_version_saved"
    VIDEO_SETTINGS_CHANGED = "online.settings_changed"


def client_ip(request: HttpRequest) -> str | None:
    if settings.SYMPRAXI_TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # The proxy appends the address it saw; that last entry is the one we trust.
            return forwarded.split(",")[-1].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record(
    action: AuditAction,
    *,
    request: HttpRequest | None = None,
    actor: Any = None,
    actor_label: str = "",
    center: Any = None,
    target: Model | None = None,
    **details: Any,
) -> AuditEvent:
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            actor = user
    if actor is not None and not actor_label:
        actor_label = actor.email
    return AuditEvent.objects.create(
        action=action,
        actor=actor,
        actor_label=actor_label[:254],
        center=center,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        ip_address=client_ip(request) if request is not None else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else "",
        details=details,
    )
