"""Every permission decision in Sympraxi goes through this module.

Views never check roles themselves. They ask `can(user, action, obj)`, or fetch
objects through `get_for_action()` / `centers_for()`, which apply the same rules
to querysets. Keeping the rules in one table makes them reviewable and lets
`tests/test_policy_matrix.py` check every role against every action.

Session notes are FERPA education records. Access must be limited to people with
a legitimate educational interest (34 CFR 99.31(a)(1)(ii)), which is why roles are
scoped to a center and why site administrators get only the operational actions in
`SITE_ADMIN_GRANTS`. Actions that read student records must never be added there.

Some grants reach only a person's own records: a student sees the appointments
they are in, a tutor the appointments they teach. Those limits are the
`OBJECT_RULES` below. Asked about a center, `can()` answers "may they do this to
anything here"; asked about an object, it also applies the rule.

Session records follow the same pattern. A tutor reads the records of students
they have (or had) an appointment with in that center, and writes records only
for appointments they teach. A student sees only their own final records, and
only the parts written for them. Front desk staff see whether a record exists,
never what it says. A record "about a student" in a center is checked against
the student's `Membership` there, which carries both the student and the center.

Attendance follows the appointments: a tutor marks who came to appointments
they teach, and front desk staff and directors mark any. Front desk staff and
directors waive late cancellations and no-shows. Only directors see and lift
booking blocks.

Forms: directors build them. Students answer their center's registration form
and the intake form for appointments they are in, and see their own answers.
Intake answers are read by the appointment's tutor, front desk staff and
directors. Registration answers are read in full only by directors; tutors (for
students they tutor) and front desk staff see the answers to questions a
director marked for staff, which `sympraxi/forms/display.py` filters.
Session-report answers are part of the session record, so they follow the
records actions; the student sees only the ones a director marked for students
(`sympraxi/forms/report.py`). Survey answers are anonymous; only directors see
the results, and only once enough people answered (`sympraxi/forms/surveys.py`).
The survey page itself opens from an emailed link, without signing in.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Model, QuerySet
from django.http import Http404

from sympraxi.centers.models import STAFF_ROLES, Center, Membership, Role
from sympraxi.forms.models import Response
from sympraxi.records.models import RecordStatus, SessionRecord, WritingProject
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Participant,
    Schedule,
    Visibility,
)

# `request.user`: a signed-in User or an AnonymousUser.
UserLike = Any


class Action(StrEnum):
    VIEW_CENTER = "center.view"
    MANAGE_CENTER = "center.manage"
    VIEW_MEMBERS = "center.view_members"
    MANAGE_MEMBERS = "center.manage_members"
    VIEW_AUDIT_LOG = "privacy.view_audit_log"
    # See schedules and their open times (no names).
    VIEW_SCHEDULES = "scheduling.view_schedules"
    # Book for yourself, under the schedule's limits.
    BOOK_OWN = "scheduling.book_own"
    # Book for any student in the center, mark walk-ins and placeholders, confirm
    # tentative bookings, and override limits.
    BOOK_FOR_OTHERS = "scheduling.book_for_others"
    VIEW_APPOINTMENT = "scheduling.view_appointment"
    CANCEL_APPOINTMENT = "scheduling.cancel_appointment"
    # Move an appointment to another open time on its schedule.
    CHANGE_APPOINTMENT = "scheduling.change_appointment"
    # The day sheet: every appointment on a schedule, with students' names.
    VIEW_ALL_APPOINTMENTS = "scheduling.view_all_appointments"
    VIEW_OWN_TUTORING = "scheduling.view_own_tutoring"
    # Terms, closures, rooms, schedules, tutor assignments and hours.
    MANAGE_SCHEDULES = "scheduling.manage_schedules"
    # Exemptions often record accommodations, so only directors see them.
    MANAGE_EXEMPTIONS = "scheduling.manage_exemptions"
    # Mark students as here or a no-show, and confirm or clear possible no-shows.
    MARK_ATTENDANCE = "scheduling.mark_attendance"
    # Waive a late cancellation or no-show, so it doesn't count toward a block.
    WAIVE_ATTENDANCE = "scheduling.waive_attendance"
    # See and lift booking blocks, and set the center's block limits.
    MANAGE_BLOCKS = "scheduling.manage_blocks"
    # Whether a session record exists and is final, without what it says.
    VIEW_RECORD_STATUS = "records.view_status"
    # Write, finalize and amend session records; start and edit writing projects.
    WRITE_RECORDS = "records.write"
    # Read whole session records, staff notes included, and a student's history.
    VIEW_RECORDS = "records.view"
    # A student's own "My writing" page and their own final records.
    VIEW_OWN_WRITING = "records.view_own_writing"
    # Everything the center keeps about one student, for a FERPA inspection request.
    EXPORT_STUDENT_RECORD = "records.export_student"
    # The center's own link-only resources and its session-record settings.
    MANAGE_RECORDS = "records.manage"
    # Build, preview and publish the center's forms: registration, intake,
    # session-report questions and surveys; and say when surveys go out.
    MANAGE_FORMS = "forms.manage"
    # Answer the center's registration form and the intake form for your own
    # appointments, and see your own answers.
    ANSWER_OWN_FORMS = "forms.answer_own"
    # Read the intake answers students gave for appointments.
    VIEW_INTAKE = "forms.view_intake"
    # Read a student's registration answers to questions marked for staff.
    VIEW_REGISTRATION = "forms.view_registration"
    # Read every registration answer, including questions not marked for staff.
    VIEW_ALL_REGISTRATION = "forms.view_all_registration"
    # Read a survey's anonymous results: counts for each option and the written
    # answers, once enough people answered in the term.
    VIEW_SURVEY_RESULTS = "forms.view_survey_results"


ROLE_GRANTS: dict[Role, frozenset[Action]] = {
    Role.STUDENT: frozenset(
        {
            Action.VIEW_CENTER,
            Action.VIEW_SCHEDULES,
            Action.BOOK_OWN,
            Action.VIEW_APPOINTMENT,
            Action.CANCEL_APPOINTMENT,
            Action.CHANGE_APPOINTMENT,
            Action.VIEW_OWN_WRITING,
            Action.ANSWER_OWN_FORMS,
        }
    ),
    Role.TUTOR: frozenset(
        {
            Action.VIEW_CENTER,
            Action.VIEW_SCHEDULES,
            Action.VIEW_APPOINTMENT,
            Action.VIEW_OWN_TUTORING,
            Action.MARK_ATTENDANCE,
            Action.VIEW_RECORD_STATUS,
            Action.WRITE_RECORDS,
            Action.VIEW_RECORDS,
            Action.VIEW_INTAKE,
            Action.VIEW_REGISTRATION,
        }
    ),
    Role.FRONT_DESK: frozenset(
        {
            Action.VIEW_CENTER,
            Action.VIEW_MEMBERS,
            Action.VIEW_SCHEDULES,
            Action.BOOK_FOR_OTHERS,
            Action.VIEW_APPOINTMENT,
            Action.CANCEL_APPOINTMENT,
            Action.CHANGE_APPOINTMENT,
            Action.VIEW_ALL_APPOINTMENTS,
            Action.MARK_ATTENDANCE,
            Action.WAIVE_ATTENDANCE,
            Action.VIEW_RECORD_STATUS,
            Action.VIEW_INTAKE,
            Action.VIEW_REGISTRATION,
        }
    ),
    Role.DIRECTOR: frozenset(
        {
            Action.VIEW_CENTER,
            Action.MANAGE_CENTER,
            Action.VIEW_MEMBERS,
            Action.MANAGE_MEMBERS,
            Action.VIEW_AUDIT_LOG,
            Action.VIEW_SCHEDULES,
            Action.BOOK_FOR_OTHERS,
            Action.VIEW_APPOINTMENT,
            Action.CANCEL_APPOINTMENT,
            Action.CHANGE_APPOINTMENT,
            Action.VIEW_ALL_APPOINTMENTS,
            Action.MANAGE_SCHEDULES,
            Action.MANAGE_EXEMPTIONS,
            Action.MARK_ATTENDANCE,
            Action.WAIVE_ATTENDANCE,
            Action.MANAGE_BLOCKS,
            Action.VIEW_RECORD_STATUS,
            Action.WRITE_RECORDS,
            Action.VIEW_RECORDS,
            Action.EXPORT_STUDENT_RECORD,
            Action.MANAGE_RECORDS,
            Action.MANAGE_FORMS,
            Action.VIEW_INTAKE,
            Action.VIEW_REGISTRATION,
            Action.VIEW_ALL_REGISTRATION,
            Action.VIEW_SURVEY_RESULTS,
        }
    ),
}


def _schedule(obj: Model) -> Schedule:
    if isinstance(obj, Schedule):
        return obj
    schedule = getattr(obj, "schedule", None)
    if isinstance(schedule, Schedule):
        return schedule
    raise TypeError(f"{type(obj).__name__} has no schedule")


def _appointment(obj: Model) -> Appointment:
    if isinstance(obj, Appointment):
        return obj
    raise TypeError(f"Expected an appointment, got {type(obj).__name__}")


def _published(user: UserLike, obj: Model) -> bool:
    return _schedule(obj).visibility == Visibility.EVERYONE


def _not_hidden(user: UserLike, obj: Model) -> bool:
    return _schedule(obj).visibility != Visibility.HIDDEN


def _participant(user: UserLike, obj: Model) -> bool:
    return Participant.objects.filter(appointment=_appointment(obj), student=user).exists()


def _tutor_of(user: UserLike, obj: Model) -> bool:
    return _appointment(obj).tutor_id == user.pk


def has_tutored(user: UserLike, student_id: Any, center_id: Any) -> bool:
    """Whether the user teaches or taught an appointment the student is in, in the center.

    Upcoming appointments count, so the tutor can read the student's history
    before the session. Canceled appointments, and groups the student left, don't.
    """
    return (
        Participant.objects.filter(
            student_id=student_id,
            canceled_at__isnull=True,
            appointment__tutor_id=user.pk,
            appointment__schedule__center_id=center_id,
        )
        .exclude(appointment__status=AppointmentStatus.CANCELED)
        .exists()
    )


def _reads_student(user: UserLike, obj: Model) -> bool:
    """A tutor reads what's kept about students they tutor, and the records they wrote."""
    if isinstance(obj, SessionRecord):
        return obj.tutor_id == user.pk or has_tutored(user, obj.student_id, obj.center_id)
    if isinstance(obj, Membership):
        return obj.role == Role.STUDENT and has_tutored(user, obj.user_id, obj.center_id)
    if isinstance(obj, WritingProject):
        return has_tutored(user, obj.student_id, obj.center_id)
    return False


def _writes_for(user: UserLike, obj: Model) -> bool:
    """A tutor writes records for appointments they teach, and edits only their own records."""
    if isinstance(obj, Appointment):
        return obj.tutor_id == user.pk
    if isinstance(obj, SessionRecord):
        return obj.tutor_id == user.pk
    if isinstance(obj, WritingProject):
        return has_tutored(user, obj.student_id, obj.center_id)
    return False


def _own_final_record(user: UserLike, obj: Model) -> bool:
    return (
        isinstance(obj, SessionRecord)
        and obj.student_id == user.pk
        and obj.status == RecordStatus.FINAL
    )


def _answers_own(user: UserLike, obj: Model) -> bool:
    """A student answers for appointments they're still in, and about themselves."""
    if isinstance(obj, Appointment):
        return Participant.objects.filter(
            appointment=obj, student=user, canceled_at__isnull=True
        ).exists()
    if isinstance(obj, Response):
        return obj.student_id == user.pk
    if isinstance(obj, Membership):
        return obj.user_id == user.pk and obj.role == Role.STUDENT
    return False


def _intake_of_own_tutoring(user: UserLike, obj: Model) -> bool:
    if isinstance(obj, Appointment):
        return obj.tutor_id == user.pk
    if isinstance(obj, Response):
        return obj.appointment is not None and obj.appointment.tutor_id == user.pk
    return False


def _registration_of_tutored(user: UserLike, obj: Model) -> bool:
    if isinstance(obj, Membership):
        return obj.role == Role.STUDENT and has_tutored(user, obj.user_id, obj.center_id)
    if isinstance(obj, Response):
        return has_tutored(user, obj.student_id, obj.center_id)
    return False


# Limits on a role's grant when the object isn't the center itself. A role with
# no entry here gets the action on everything in its center.
OBJECT_RULES: dict[tuple[Role, Action], Callable[[UserLike, Model], bool]] = {
    (Role.STUDENT, Action.VIEW_SCHEDULES): _published,
    (Role.STUDENT, Action.BOOK_OWN): _published,
    (Role.STUDENT, Action.VIEW_APPOINTMENT): _participant,
    (Role.STUDENT, Action.CANCEL_APPOINTMENT): _participant,
    (Role.STUDENT, Action.CHANGE_APPOINTMENT): _participant,
    (Role.TUTOR, Action.VIEW_SCHEDULES): _published,
    (Role.TUTOR, Action.VIEW_APPOINTMENT): _tutor_of,
    (Role.TUTOR, Action.MARK_ATTENDANCE): _tutor_of,
    (Role.TUTOR, Action.VIEW_RECORD_STATUS): _tutor_of,
    (Role.TUTOR, Action.WRITE_RECORDS): _writes_for,
    (Role.TUTOR, Action.VIEW_RECORDS): _reads_student,
    (Role.STUDENT, Action.VIEW_OWN_WRITING): _own_final_record,
    (Role.STUDENT, Action.ANSWER_OWN_FORMS): _answers_own,
    (Role.TUTOR, Action.VIEW_INTAKE): _intake_of_own_tutoring,
    (Role.TUTOR, Action.VIEW_REGISTRATION): _registration_of_tutored,
    (Role.FRONT_DESK, Action.VIEW_SCHEDULES): _not_hidden,
    (Role.FRONT_DESK, Action.BOOK_FOR_OTHERS): _not_hidden,
    (Role.DIRECTOR, Action.BOOK_FOR_OTHERS): _not_hidden,
}

# Site administrators keep the installation running: they create centers, appoint
# directors and investigate security events. They are not a back door to records:
# none of the records actions belong here.
SITE_ADMIN_GRANTS: frozenset[Action] = frozenset(
    {
        Action.VIEW_CENTER,
        Action.MANAGE_CENTER,
        Action.VIEW_MEMBERS,
        Action.MANAGE_MEMBERS,
        Action.VIEW_AUDIT_LOG,
    }
)


def _is_site_admin(user: UserLike) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def roles_in(user: UserLike, center: Center) -> frozenset[Role]:
    """The user's active roles in a center, cached on the user object per request."""
    if not user.is_authenticated or not user.is_active:
        return frozenset()
    cache: dict[object, frozenset[Role]] = user.__dict__.setdefault("_sympraxi_roles", {})
    if center.pk not in cache:
        cache[center.pk] = frozenset(
            Role(r)
            for r in Membership.objects.filter(
                user=user, center=center, is_active=True
            ).values_list("role", flat=True)
        )
    return cache[center.pk]


def center_of(obj: Model) -> Center:
    """The center an object belongs to. Every protected object must have one."""
    if isinstance(obj, Center):
        return obj
    center = getattr(obj, "center", None)
    if isinstance(center, Center):
        return center
    raise TypeError(f"{type(obj).__name__} has no center; the policy cannot decide access to it")


def can(user: UserLike, action: Action, obj: Model) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    center = center_of(obj)
    if _is_site_admin(user) and action in SITE_ADMIN_GRANTS:
        return True
    if not center.is_active:
        return False
    return any(
        action in ROLE_GRANTS[role] and _within_rule(user, role, action, obj)
        for role in roles_in(user, center)
    )


def _within_rule(user: UserLike, role: Role, action: Action, obj: Model) -> bool:
    rule = OBJECT_RULES.get((role, action))
    return rule is None or isinstance(obj, Center) or rule(user, obj)


def centers_for(user: UserLike, action: Action) -> QuerySet[Center]:
    """All centers where the user may perform the action."""
    if not user.is_authenticated or not user.is_active:
        return Center.objects.none()
    if _is_site_admin(user) and action in SITE_ADMIN_GRANTS:
        return Center.objects.all()
    roles = [role for role, actions in ROLE_GRANTS.items() if action in actions]
    return Center.objects.filter(
        is_active=True,
        memberships__user=user,
        memberships__is_active=True,
        memberships__role__in=roles,
    ).distinct()


def get_for_action[M: Model](user: UserLike, action: Action, queryset: QuerySet[M], **lookup) -> M:
    """Fetch one object the user may act on.

    People outside the object's center get a 404, so IDs and slugs can't be used to
    probe which records exist. So do members whose grant covers only their own
    records, when the object isn't theirs. Members of the center who lack the
    permission altogether get a 403.
    """
    try:
        obj = queryset.get(**lookup)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    if can(user, action, obj):
        return obj
    center = center_of(obj)
    if not isinstance(obj, Center) and can(user, action, center):
        raise Http404
    if roles_in(user, center) or _is_site_admin(user):
        raise PermissionDenied
    raise Http404


def is_staff_member(user: UserLike) -> bool:
    """Site admins and anyone with an active staff role in any center."""
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return Membership.objects.filter(user=user, is_active=True, role__in=STAFF_ROLES).exists()
