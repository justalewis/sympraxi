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

Reports: directors see the center's statistics and the hours tutors offered and
students booked. Front desk staff and directors see the master lists, which
name students: a day's or a week's appointments, and the students who booked
in a period with their visits. Only directors export the whole center, and
site administrators never do: the export holds every session record. The same goes for the
de-identified research export, which a director makes only under a protocol they have recorded.

Time clock: tutors, front desk staff and directors clock themselves in and out
and see their own hours. Directors see everyone's timesheets and add, change
and remove entries, with a reason each time (`sympraxi/staff/timeclock.py`).
Site administrators don't: the hours are the center's employment records.

News: directors post announcements on the center's pages and email groups of
the center's people (`sympraxi/notify/news.py`). Site administrators do
neither: they run the installation, not the center.

Files: a student attaches drafts to appointments they're in, a tutor to
appointments they teach, and directors to any. Each file belongs to one
student: it is opened by that student, the appointment's tutor and directors,
never by other students in a group, and front desk staff don't see drafts
(`sympraxi/files/`).

The written-feedback queue (`sympraxi/feedback/`): a student sends a draft and
sees their own submissions. Tutors and directors see the waiting submissions
without the student's name or draft, and claim them; only the tutor who has a
submission, and directors, open it. Directors assign and release submissions,
set the queue up and see turnaround. Front desk staff and site administrators
have no part in it.

Goals: a student writes their own, a tutor who works with them (the same test as for
their records) writes them with the student, and directors do too. Front desk staff
don't. A goal is part of the education record, so it is read like one.

Video: a student joins the video room of an online appointment they are booked
in, and a tutor that of one they teach. Directors don't, since they aren't in
the session; they turn video on for the center with the schedule actions.
The shared document works the same way: the people in an appointment open it, and
no one else does, directors included.

Disclosures: directors keep each student's record of disclosures of their
records outside the college's own officials (34 CFR 99.32), and the student
reads their own on My writing. Site administrators don't: it is part of the
student's record.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Model, QuerySet
from django.http import Http404

from sympraxi.centers.models import STAFF_ROLES, Center, Membership, Role
from sympraxi.feedback.models import Submission
from sympraxi.forms.models import Response
from sympraxi.records.models import (
    Goal,
    RecordStatus,
    Resource,
    SessionRecord,
    WritingProject,
)
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
    # Read and add to a student's record of disclosures outside the college.
    RECORD_DISCLOSURES = "privacy.record_disclosures"
    # Attach a file to an appointment.
    ATTACH_FILES = "files.attach"
    # Open the files on an appointment.
    VIEW_FILES = "files.view"
    # Add a writing goal for a student, and mark goals met or dropped: the student for
    # themselves, a tutor who works with them, directors.
    SET_GOALS = "records.set_goals"
    # Send a draft to the center's written-feedback queue, and see your own.
    SUBMIT_WORK = "queue.submit"
    # See the waiting submissions, without the student's name, and claim one.
    WORK_QUEUE = "queue.work"
    # Open a submission: the student, the tutor who has it, and directors.
    VIEW_SUBMISSION = "queue.view"
    # Assign and release submissions, set the queue up, and see turnaround.
    MANAGE_QUEUE = "queue.manage"
    # Enter the video room of an online appointment you teach or are booked in.
    JOIN_VIDEO = "online.join"
    # Open the shared document of an appointment you teach or are booked in, and its history.
    USE_ROOM = "online.room"
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
    # Counts of appointments, visits and session topics, and the hours tutors
    # offered and students booked, by term or dates. Names tutors, not students.
    VIEW_STATISTICS = "reports.view_statistics"
    # The appointment list for a day or week and the list of students with their
    # visits, on the page or downloaded. Both name students.
    VIEW_MASTER_LISTS = "reports.view_master_lists"
    # One ZIP of everything the center keeps, session records included.
    EXPORT_CENTER = "reports.export_center"
    # A de-identified file of session data for an IRB-approved project, and the record of the
    # protocol it is made under.
    EXPORT_RESEARCH = "reports.export_research"
    # Clock yourself in and out, and see your own hours.
    CLOCK_OWN = "staff.clock_own"
    # Everyone's timesheets, and adding, changing and removing their entries.
    MANAGE_TIME = "staff.manage_time"
    # The check-in kiosk, and checking a student in from the waiting room.
    RUN_KIOSK = "staff.run_kiosk"
    # See who has checked in and is waiting, and mark them seen or gone.
    VIEW_WAITING_ROOM = "staff.view_waiting_room"
    # Post, change and delete the center's announcements.
    MANAGE_ANNOUNCEMENTS = "notify.manage_announcements"
    # Email a group of the center's people, and see what was sent to whom.
    SEND_MASS_EMAIL = "notify.send_mass_email"


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
            Action.ATTACH_FILES,
            Action.VIEW_FILES,
            Action.SUBMIT_WORK,
            Action.VIEW_SUBMISSION,
            Action.JOIN_VIDEO,
            Action.USE_ROOM,
            Action.SET_GOALS,
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
            Action.CLOCK_OWN,
            Action.VIEW_WAITING_ROOM,
            Action.ATTACH_FILES,
            Action.VIEW_FILES,
            Action.WORK_QUEUE,
            Action.VIEW_SUBMISSION,
            Action.JOIN_VIDEO,
            Action.USE_ROOM,
            Action.SET_GOALS,
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
            Action.VIEW_MASTER_LISTS,
            Action.CLOCK_OWN,
            Action.RUN_KIOSK,
            Action.VIEW_WAITING_ROOM,
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
            Action.VIEW_STATISTICS,
            Action.VIEW_MASTER_LISTS,
            Action.EXPORT_CENTER,
            Action.EXPORT_RESEARCH,
            Action.CLOCK_OWN,
            Action.RUN_KIOSK,
            Action.VIEW_WAITING_ROOM,
            Action.MANAGE_TIME,
            Action.MANAGE_ANNOUNCEMENTS,
            Action.SEND_MASS_EMAIL,
            Action.RECORD_DISCLOSURES,
            Action.ATTACH_FILES,
            Action.VIEW_FILES,
            Action.WORK_QUEUE,
            Action.VIEW_SUBMISSION,
            Action.MANAGE_QUEUE,
            Action.SET_GOALS,
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
    if isinstance(obj, WritingProject | Goal):
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
    # A resource isn't about anyone: a tutor may star any the center offers.
    return isinstance(obj, Resource)


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


def _own_file(user: UserLike, obj: Model) -> bool:
    """A student opens files that belong to them, on appointments they're in."""
    student_id = getattr(obj, "student_id", None)
    if isinstance(obj, Appointment):
        return _participant(user, obj)
    return student_id == user.pk


def _file_of_own_tutoring(user: UserLike, obj: Model) -> bool:
    """A tutor opens files on appointments they teach."""
    if isinstance(obj, Appointment):
        return obj.tutor_id == user.pk
    appointment = getattr(obj, "appointment", None)
    return isinstance(appointment, Appointment) and appointment.tutor_id == user.pk


def _own_submission(user: UserLike, obj: Model) -> bool:
    """A student works with their own submissions, and the files on them."""
    return getattr(obj, "student_id", None) == user.pk


def _own_goal(user: UserLike, obj: Model) -> bool:
    """A student writes goals for themselves: their own goals, and their own membership."""
    if isinstance(obj, Membership):
        return obj.user_id == user.pk and obj.role == Role.STUDENT
    return getattr(obj, "student_id", None) == user.pk


def _claimed_submission(user: UserLike, obj: Model) -> bool:
    """A tutor opens the submissions they have, and the files on them."""
    submission = getattr(obj, "submission", obj)
    return isinstance(submission, Submission) and submission.tutor_id == user.pk


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
    (Role.STUDENT, Action.ATTACH_FILES): _answers_own,
    (Role.STUDENT, Action.VIEW_FILES): _own_file,
    (Role.TUTOR, Action.ATTACH_FILES): _tutor_of,
    (Role.TUTOR, Action.VIEW_FILES): _file_of_own_tutoring,
    (Role.STUDENT, Action.SET_GOALS): _own_goal,
    (Role.TUTOR, Action.SET_GOALS): _reads_student,
    (Role.STUDENT, Action.JOIN_VIDEO): _participant,
    (Role.TUTOR, Action.JOIN_VIDEO): _tutor_of,
    (Role.STUDENT, Action.USE_ROOM): _participant,
    (Role.TUTOR, Action.USE_ROOM): _tutor_of,
    (Role.STUDENT, Action.SUBMIT_WORK): _own_submission,
    (Role.STUDENT, Action.VIEW_SUBMISSION): _own_submission,
    (Role.TUTOR, Action.VIEW_SUBMISSION): _claimed_submission,
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
