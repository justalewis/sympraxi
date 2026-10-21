"""Every role against every action.

The expected results are written out by hand on purpose. Deriving them from
`ROLE_GRANTS` would make the test agree with whatever the code says. When you add
an `Action`, this file fails until you decide, row by row, who gets it.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404

from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core import policy
from sympraxi.core.policy import Action
from sympraxi.forms.models import FormKind
from sympraxi.records.models import SessionRecord, WritingProject
from sympraxi.reports.models import CenterExport
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Block,
    BlockReason,
    Participant,
    Visibility,
)
from tests.factories import MembershipFactory, UserFactory
from tests.forms_world import (
    INTAKE_ANSWERS,
    REGISTRATION,
    REGISTRATION_ANSWERS,
    REPORT,
    SURVEY,
    answer,
    make_form,
)
from tests.records_world import appointment_for, membership, standalone
from tests.scheduling_world import build_world, make_member, make_schedule

CENTER_ACTIONS = [
    Action.VIEW_CENTER,
    Action.MANAGE_CENTER,
    Action.VIEW_MEMBERS,
    Action.MANAGE_MEMBERS,
    Action.VIEW_AUDIT_LOG,
]

SCHEDULING_ACTIONS = [
    Action.VIEW_SCHEDULES,
    Action.BOOK_OWN,
    Action.BOOK_FOR_OTHERS,
    Action.VIEW_APPOINTMENT,
    Action.CANCEL_APPOINTMENT,
    Action.CHANGE_APPOINTMENT,
    Action.VIEW_ALL_APPOINTMENTS,
    Action.VIEW_OWN_TUTORING,
    Action.MANAGE_SCHEDULES,
    Action.MANAGE_EXEMPTIONS,
]

ATTENDANCE_ACTIONS = [
    Action.MARK_ATTENDANCE,
    Action.WAIVE_ATTENDANCE,
    Action.MANAGE_BLOCKS,
]

RECORDS_ACTIONS = [
    Action.VIEW_RECORD_STATUS,
    Action.WRITE_RECORDS,
    Action.VIEW_RECORDS,
    Action.VIEW_OWN_WRITING,
    Action.EXPORT_STUDENT_RECORD,
    Action.MANAGE_RECORDS,
]

FORMS_ACTIONS = [
    Action.MANAGE_FORMS,
    Action.ANSWER_OWN_FORMS,
    Action.VIEW_INTAKE,
    Action.VIEW_REGISTRATION,
    Action.VIEW_ALL_REGISTRATION,
    Action.VIEW_SURVEY_RESULTS,
]

REPORTS_ACTIONS = [
    Action.VIEW_STATISTICS,
    Action.VIEW_MASTER_LISTS,
    Action.EXPORT_CENTER,
    Action.EXPORT_RESEARCH,
]

STAFF_ACTIONS = [
    Action.CLOCK_OWN,
    Action.MANAGE_TIME,
    Action.RUN_KIOSK,
    Action.VIEW_WAITING_ROOM,
]

WORKSHOP_ACTIONS = [
    Action.VIEW_WORKSHOPS,
    Action.REGISTER_WORKSHOP,
    Action.MANAGE_WORKSHOPS,
    Action.TAKE_WORKSHOP_ATTENDANCE,
]

NEWS_ACTIONS = [
    Action.MANAGE_ANNOUNCEMENTS,
    Action.SEND_MASS_EMAIL,
]

DISCLOSURE_ACTIONS = [
    Action.RECORD_DISCLOSURES,
]

FILES_ACTIONS = [
    Action.ATTACH_FILES,
    Action.VIEW_FILES,
]

GOAL_ACTIONS = [
    Action.SET_GOALS,
]

ONLINE_ACTIONS = [
    Action.JOIN_VIDEO,
    Action.USE_ROOM,
]

QUEUE_ACTIONS = [
    Action.SUBMIT_WORK,
    Action.WORK_QUEUE,
    Action.VIEW_SUBMISSION,
    Action.MANAGE_QUEUE,
]

ACTIONS = (
    CENTER_ACTIONS
    + SCHEDULING_ACTIONS
    + ATTENDANCE_ACTIONS
    + RECORDS_ACTIONS
    + FORMS_ACTIONS
    + REPORTS_ACTIONS
    + STAFF_ACTIONS
    + WORKSHOP_ACTIONS
    + NEWS_ACTIONS
    + DISCLOSURE_ACTIONS
    + FILES_ACTIONS
    + QUEUE_ACTIONS
    + ONLINE_ACTIONS
    + GOAL_ACTIONS
)

# fmt: off
EXPECTED_IN_OWN_CENTER = {
    #              view   manage  members  manage   audit
    #              center center  view     members  log
    "none":        (0,     0,      0,       0,       0),
    "student":     (1,     0,      0,       0,       0),
    "tutor":       (1,     0,      0,       0,       0),
    "front_desk":  (1,     0,      1,       0,       0),
    "director":    (1,     1,      1,       1,       1),
    "site_admin":  (1,     1,      1,       1,       1),
}

# Asked about the center itself. "View", "cancel" and "change appointment" mean
# "their own" for students and tutors; OWNERSHIP_CASES below checks that part.
# Site administrators get none of these: appointments are student records.
EXPECTED_SCHEDULING = {
    #              view    book  book    view   cancel  change  view all  own       manage  manage
    #              sched.  own   others  appt.  appt.   appt.   appts.    tutoring  sched.  exempt.
    "none":        (0,     0,    0,      0,     0,      0,      0,        0,        0,         0),
    "student":     (1,     1,    0,      1,     1,      1,      0,        0,        0,         0),
    "tutor":       (1,     0,    0,      1,     0,      0,      0,        1,        0,         0),
    "front_desk":  (1,     0,    1,      1,     1,      1,      1,        0,        0,         0),
    "director":    (1,     0,    1,      1,     1,      1,      1,        0,        1,         1),
    "site_admin":  (0,     0,    0,      0,     0,      0,      0,        0,        0,         0),
}

# Attendance, asked about the center. "Mark attendance" means "for appointments
# they teach" for tutors; OWNERSHIP_CASES checks that part. Front desk staff waive
# late cancellations and no-shows; only directors see and lift booking blocks.
# Site administrators get none: attendance is part of the student's record.
EXPECTED_ATTENDANCE = {
    #              mark        waive  manage
    #              attendance         blocks
    "none":        (0,         0,     0),
    "student":     (0,         0,     0),
    "tutor":       (1,         0,     0),
    "front_desk":  (1,         1,     0),
    "director":    (1,         1,     1),
    "site_admin":  (0,         0,     0),
}

# Session records, asked about the center. "Write records" and "view records"
# mean "for the students they tutor" for tutors, and "own writing" means a
# student's own final records; RECORD_CASES below checks those parts. Front desk
# staff see only whether a record exists. Site administrators get nothing:
# session records are student records.
EXPECTED_RECORDS = {
    #              record  write    view     own      export   manage
    #              status  records  records  writing  student  records
    "none":        (0,     0,       0,       0,       0,       0),
    "student":     (0,     0,       0,       1,       0,       0),
    "tutor":       (1,     1,       1,       0,       0,       0),
    "front_desk":  (1,     0,       0,       0,       0,       0),
    "director":    (1,     1,       1,       0,       1,       1),
    "site_admin":  (0,     0,       0,       0,       0,       0),
}

# Forms, asked about the center. "Answer own" means a student's own registration
# and their own appointments' intake (FORM_CASES checks that part); "view intake"
# means appointments they teach, and "view registration" students they tutor, for
# tutors. Registration answers in full are for directors only; tutors and front
# desk staff see the questions marked for staff. Survey results, anonymous and
# counted by term, are for directors only. Site administrators get nothing: the
# answers are student records, and survey results are the center's.
EXPECTED_FORMS = {
    #              manage  answer  view    view    view all  survey
    #              forms   own     intake  regist. regist.   results
    "none":        (0,     0,      0,      0,      0,        0),
    "student":     (0,     1,      0,      0,      0,        0),
    "tutor":       (0,     0,      1,      1,      0,        0),
    "front_desk":  (0,     0,      1,      1,      0,        0),
    "director":    (1,     0,      1,      1,      1,        1),
    "site_admin":  (0,     0,      0,      0,      0,        0),
}

# Reports. Statistics and hours name tutors, not students, and are for directors.
# The master lists name students and are for the front desk and directors. Only
# directors export the whole center. Site administrators get none: the export
# holds every session record, and the lists and statistics are about students.
EXPECTED_REPORTS = {
    #              statistics  master  export  research
    #                          lists   center  export
    "none":        (0,         0,      0,       0),
    "student":     (0,         0,      0,       0),
    "tutor":       (0,         0,      0,       0),
    "front_desk":  (0,         1,      0,       0),
    "director":    (1,         1,      1,       1),
    "site_admin":  (0,         0,      0,       0),
}

# Time clock and front desk. Staff clock themselves in and out; directors see and
# correct everyone's hours. The front desk runs the check-in kiosk; tutors can see
# the waiting room too. Students have no time clock, and site administrators get
# nothing: the hours are the center's employment records.
EXPECTED_STAFF = {
    #              clock  manage  run    waiting
    #              own    time    kiosk  room
    "none":        (0,    0,      0,     0),
    "student":     (0,    0,      0,     0),
    "tutor":       (1,    0,      0,     1),
    "front_desk":  (1,    0,      1,     1),
    "director":    (1,    1,      1,     1),
    "site_admin":  (0,    0,      0,     0),
}

# Workshops, asked about the center. Students see and register for published
# workshops (WORKSHOP_CASES checks that part); tutors see them too and run the
# roster of one they lead; the front desk runs any roster; directors set them up.
# Site administrators get nothing: a roster names students.
EXPECTED_WORKSHOPS = {
    #              view   register  manage  take
    #                                       attendance
    "none":        (0,    0,        0,      0),
    "student":     (1,    1,        0,      0),
    "tutor":       (1,    0,        0,      1),
    "front_desk":  (1,    0,        0,      1),
    "director":    (1,    0,        1,      1),
    "site_admin":  (0,    0,        0,      0),
}

# News. Directors post announcements and email groups of the center's people.
# Site administrators get neither: they run the installation, not the center.
EXPECTED_NEWS = {
    #              announce-  mass
    #              ments      email
    "none":        (0,        0),
    "student":     (0,        0),
    "tutor":       (0,        0),
    "front_desk":  (0,        0),
    "director":    (1,        1),
    "site_admin":  (0,        0),
}

# The record of disclosures is part of each student's record, kept by directors.
# Students read their own through VIEW_OWN_WRITING. Site administrators get none.
EXPECTED_DISCLOSURES = {
    #              record
    #              disclosures
    "none":        (0,),
    "student":     (0,),
    "tutor":       (0,),
    "front_desk":  (0,),
    "director":    (1,),
    "site_admin":  (0,),
}

# Files on appointments, asked about the center. For students that means their
# own appointments and files, and for tutors appointments they teach;
# tests/test_files.py checks those parts. Front desk staff don't open students'
# drafts. Site administrators get nothing: the files are student work.
EXPECTED_FILES = {
    #              attach  view
    "none":        (0,     0),
    "student":     (1,     1),
    "tutor":       (1,     1),
    "front_desk":  (0,     0),
    "director":    (1,     1),
    "site_admin":  (0,     0),
}

# The written-feedback queue, asked about the center. Students send drafts and
# open their own; tutors work the queue and open what they've claimed;
# directors do both and run it. Front desk staff and site administrators have
# no part in it: the drafts are student work.
EXPECTED_QUEUE = {
    #              submit  work  view  manage
    "none":        (0,     0,    0,    0),
    "student":     (1,     0,    1,    0),
    "tutor":       (0,     1,    1,    0),
    "front_desk":  (0,     0,    0,    0),
    "director":    (0,     1,    1,    1),
    "site_admin":  (0,     0,    0,    0),
}

# Video rooms and shared documents, asked about the center. Students and tutors
# may join the rooms and open the documents of
# appointments they're in; tests/test_video.py checks that part. Directors and
# front desk staff aren't in the sessions, so they don't join.
EXPECTED_ONLINE = {
    #              join  room
    "none":        (0,    0),
    "student":     (1,    1),
    "tutor":       (1,    1),
    "front_desk":  (0,    0),
    "director":    (0,    0),
    "site_admin":  (0,    0),
}

# Writing goals, asked about the center. Students write their own and tutors those of
# students they work with (tests/test_goals.py checks those parts); directors write
# anyone's. Front desk staff and site administrators don't.
EXPECTED_GOALS = {
    #              set
    "none":        (0,),
    "student":     (1,),
    "tutor":       (1,),
    "front_desk":  (0,),
    "director":    (1,),
    "site_admin":  (0,),
}
# fmt: on

CASES = [
    (role, action, bool(expected[i]))
    for table, actions in (
        (EXPECTED_IN_OWN_CENTER, CENTER_ACTIONS),
        (EXPECTED_SCHEDULING, SCHEDULING_ACTIONS),
        (EXPECTED_ATTENDANCE, ATTENDANCE_ACTIONS),
        (EXPECTED_RECORDS, RECORDS_ACTIONS),
        (EXPECTED_FORMS, FORMS_ACTIONS),
        (EXPECTED_REPORTS, REPORTS_ACTIONS),
        (EXPECTED_STAFF, STAFF_ACTIONS),
        (EXPECTED_WORKSHOPS, WORKSHOP_ACTIONS),
        (EXPECTED_NEWS, NEWS_ACTIONS),
        (EXPECTED_DISCLOSURES, DISCLOSURE_ACTIONS),
        (EXPECTED_FILES, FILES_ACTIONS),
        (EXPECTED_QUEUE, QUEUE_ACTIONS),
        (EXPECTED_ONLINE, ONLINE_ACTIONS),
        (EXPECTED_GOALS, GOAL_ACTIONS),
    )
    for role, expected in table.items()
    for i, action in enumerate(actions)
]


def test_matrix_covers_every_action() -> None:
    assert set(ACTIONS) == set(Action), "Add the new action to ACTIONS and the matrix"


@pytest.mark.parametrize(("role", "action", "expected"), CASES)
def test_own_center(person_with_role, center, role, action, expected) -> None:
    user = person_with_role(role, center)
    assert policy.can(user, action, center) is expected
    assert (center in policy.centers_for(user, action)) is expected


@pytest.mark.parametrize(("role", "action", "expected"), CASES)
def test_other_center(person_with_role, center, other_center, role, action, expected) -> None:
    user = person_with_role(role, center)
    # A role in one center grants nothing in another. Only site admins span centers.
    assert policy.can(user, action, other_center) is (expected and role == "site_admin")


@pytest.mark.parametrize("action", ACTIONS)
def test_inactive_membership_grants_nothing(person_with_role, center, action) -> None:
    user = person_with_role("director", center)
    Membership.objects.filter(user=user).update(is_active=False)
    user = type(user).objects.get(pk=user.pk)  # drop the per-request role cache
    assert not policy.can(user, action, center)


@pytest.mark.parametrize("role", ["student", "director", "site_admin"])
def test_deactivated_user_gets_nothing(person_with_role, center, role) -> None:
    user = person_with_role(role, center)
    user.is_active = False
    assert not any(policy.can(user, action, center) for action in ACTIONS)
    assert not policy.centers_for(user, Action.VIEW_CENTER).exists()


@pytest.mark.parametrize("role", ["student", "director"])
def test_inactive_center_is_closed_to_members(person_with_role, center, role) -> None:
    user = person_with_role(role, center)
    center.is_active = False
    center.save()
    assert not policy.can(user, Action.VIEW_CENTER, center)


def test_inactive_center_stays_open_to_site_admins(person_with_role, center) -> None:
    admin = person_with_role("site_admin", center)
    center.is_active = False
    center.save()
    assert policy.can(admin, Action.MANAGE_CENTER, center)


def test_get_for_action_hides_other_centers(person_with_role, center, other_center) -> None:
    student = person_with_role("student", center)
    with pytest.raises(Http404):
        policy.get_for_action(student, Action.VIEW_CENTER, Center.objects.all(), pk=other_center.pk)


def test_get_for_action_forbids_members_without_permission(person_with_role, center) -> None:
    student = person_with_role("student", center)
    with pytest.raises(PermissionDenied):
        policy.get_for_action(student, Action.VIEW_MEMBERS, Center.objects.all(), pk=center.pk)


def test_get_for_action_returns_permitted_object(person_with_role, center) -> None:
    director = person_with_role("director", center)
    found = policy.get_for_action(
        director, Action.VIEW_AUDIT_LOG, Center.objects.all(), pk=center.pk
    )
    assert found == center


def test_get_for_action_missing_object_is_404(person_with_role, center) -> None:
    director = person_with_role("director", center)
    with pytest.raises(Http404):
        policy.get_for_action(director, Action.VIEW_CENTER, Center.objects.all(), slug="nope")


def test_objects_without_a_center_are_refused(db) -> None:
    with pytest.raises(TypeError):
        policy.can(UserFactory(), Action.VIEW_CENTER, UserFactory())


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("none", False),
        ("student", False),
        ("tutor", True),
        ("front_desk", True),
        ("director", True),
        ("site_admin", True),
    ],
)
def test_is_staff_member(person_with_role, center, role, expected) -> None:
    assert policy.is_staff_member(person_with_role(role, center)) is expected


# --- Grants limited to a person's own records ------------------------------------

# (role, action, object, expected). "own" means the person is in the appointment
# (students) or teaches it (tutors); "other" belongs to someone else in the center.
# fmt: off
OWNERSHIP_CASES = [
    ("student",    Action.VIEW_APPOINTMENT,   "own_appointment",   True),
    ("student",    Action.VIEW_APPOINTMENT,   "other_appointment", False),
    ("student",    Action.CANCEL_APPOINTMENT, "own_appointment",   True),
    ("student",    Action.CANCEL_APPOINTMENT, "other_appointment", False),
    ("student",    Action.CHANGE_APPOINTMENT, "own_appointment",   True),
    ("student",    Action.CHANGE_APPOINTMENT, "other_appointment", False),
    ("student",    Action.VIEW_SCHEDULES,     "published",         True),
    ("student",    Action.VIEW_SCHEDULES,     "staff_only",        False),
    ("student",    Action.VIEW_SCHEDULES,     "hidden",            False),
    ("student",    Action.BOOK_OWN,           "published",         True),
    ("student",    Action.BOOK_OWN,           "staff_only",        False),
    ("tutor",      Action.VIEW_APPOINTMENT,   "own_tutoring",      True),
    ("tutor",      Action.VIEW_APPOINTMENT,   "other_appointment", False),
    ("tutor",      Action.CANCEL_APPOINTMENT, "own_tutoring",      False),
    ("tutor",      Action.CHANGE_APPOINTMENT, "own_tutoring",      False),
    ("tutor",      Action.VIEW_SCHEDULES,     "published",         True),
    ("tutor",      Action.VIEW_SCHEDULES,     "staff_only",        False),
    ("front_desk", Action.VIEW_APPOINTMENT,   "other_appointment", True),
    ("front_desk", Action.CANCEL_APPOINTMENT, "other_appointment", True),
    ("front_desk", Action.CHANGE_APPOINTMENT, "other_appointment", True),
    ("front_desk", Action.VIEW_SCHEDULES,     "staff_only",        True),
    ("front_desk", Action.VIEW_SCHEDULES,     "hidden",            False),
    ("front_desk", Action.BOOK_FOR_OTHERS,    "staff_only",        True),
    ("front_desk", Action.BOOK_FOR_OTHERS,    "hidden",            False),
    ("director",   Action.VIEW_APPOINTMENT,   "other_appointment", True),
    ("director",   Action.VIEW_SCHEDULES,     "hidden",            True),
    ("director",   Action.MANAGE_SCHEDULES,   "hidden",            True),
    ("director",   Action.BOOK_FOR_OTHERS,    "hidden",            False),
    ("site_admin", Action.VIEW_APPOINTMENT,   "other_appointment", False),
    ("site_admin", Action.MANAGE_SCHEDULES,   "published",         False),
    ("student",    Action.MARK_ATTENDANCE,    "own_appointment",   False),
    ("student",    Action.WAIVE_ATTENDANCE,   "own_appointment",   False),
    ("tutor",      Action.MARK_ATTENDANCE,    "own_tutoring",      True),
    ("tutor",      Action.MARK_ATTENDANCE,    "other_appointment", False),
    ("tutor",      Action.WAIVE_ATTENDANCE,   "own_tutoring",      False),
    ("front_desk", Action.MARK_ATTENDANCE,    "other_appointment", True),
    ("front_desk", Action.MARK_ATTENDANCE,    "hidden_appointment", True),
    ("front_desk", Action.WAIVE_ATTENDANCE,   "other_appointment", True),
    ("front_desk", Action.MANAGE_BLOCKS,      "block",             False),
    ("director",   Action.MARK_ATTENDANCE,    "other_appointment", True),
    ("director",   Action.WAIVE_ATTENDANCE,   "other_appointment", True),
    ("director",   Action.MANAGE_BLOCKS,      "block",             True),
    ("site_admin", Action.MARK_ATTENDANCE,    "other_appointment", False),
    ("site_admin", Action.MANAGE_BLOCKS,      "block",             False),
]
# fmt: on


def _objects(center, user):
    """One of each object, with `user` in or teaching the "own" appointments."""
    world = build_world(center)
    schedule = world.schedule
    other = world.appointment

    def appointment_with(**fields):
        start = other.starts_at + timedelta(hours=fields.pop("hours"))
        values = {
            "schedule": schedule,
            "assignment": world.assignment,
            "tutor": world.tutor,
            "starts_at": start,
            "ends_at": start + timedelta(minutes=30),
            "blocked_until": start + timedelta(minutes=30),
            "modality": "in_person",
        }
        values.update(fields)
        return Appointment.objects.create(**values)

    own_appointment = appointment_with(hours=1)
    Participant.objects.create(appointment=own_appointment, student=user)
    own_tutoring = appointment_with(hours=2, tutor=user)
    hidden = make_schedule(
        center, schedule.starts_on, schedule.ends_on, visibility=Visibility.HIDDEN
    )
    hidden_appointment = appointment_with(hours=3, schedule=hidden)
    return {
        "hidden_appointment": hidden_appointment,
        "block": Block.objects.create(
            center=center,
            student=world.student,
            reason=BlockReason.NO_SHOWS,
            count=2,
            limit=2,
            ends_on=schedule.ends_on,
        ),
        "own_appointment": own_appointment,
        "own_tutoring": own_tutoring,
        "other_appointment": other,
        "published": schedule,
        "staff_only": make_schedule(
            center, schedule.starts_on, schedule.ends_on, visibility=Visibility.STAFF
        ),
        "hidden": make_schedule(
            center, schedule.starts_on, schedule.ends_on, visibility=Visibility.HIDDEN
        ),
    }


@pytest.mark.parametrize(("role", "action", "name", "expected"), OWNERSHIP_CASES)
def test_own_records(person_with_role, center, role, action, name, expected) -> None:
    user = person_with_role(role, center)
    obj = _objects(center, user)[name]
    assert policy.can(user, action, obj) is expected


def test_someone_elses_record_is_hidden_not_forbidden(person_with_role, center) -> None:
    """A student who may see their own appointments gets a 404 for anyone else's."""
    student = person_with_role("student", center)
    other = _objects(center, student)["other_appointment"]
    with pytest.raises(Http404):
        policy.get_for_action(
            student, Action.VIEW_APPOINTMENT, Appointment.objects.all(), pk=other.pk
        )
    tutor = person_with_role("tutor", center)
    with pytest.raises(PermissionDenied):
        policy.get_for_action(
            tutor, Action.CANCEL_APPOINTMENT, Appointment.objects.all(), pk=other.pk
        )


def test_roles_combine(person_with_role, center) -> None:
    """A tutor who is also a student here sees both what they teach and what they attend."""
    user = person_with_role("tutor", center)
    MembershipFactory(user=user, center=center, role="student")
    user = type(user).objects.get(pk=user.pk)
    objects = _objects(center, user)
    assert policy.can(user, Action.VIEW_APPOINTMENT, objects["own_tutoring"])
    assert policy.can(user, Action.VIEW_APPOINTMENT, objects["own_appointment"])
    assert policy.can(user, Action.CANCEL_APPOINTMENT, objects["own_appointment"])
    assert not policy.can(user, Action.CANCEL_APPOINTMENT, objects["own_tutoring"])
    assert not policy.can(user, Action.VIEW_APPOINTMENT, objects["other_appointment"])


# --- Session records ---------------------------------------------------------------

# (role, action, object, expected). For a tutor, "my student" is a student they
# have an appointment with, and the records are written by another tutor unless
# named "own". For a student, "own" records are about them.
# fmt: off
RECORD_CASES = [
    ("student",    Action.VIEW_OWN_WRITING,      "own_final_record",        True),
    ("student",    Action.VIEW_OWN_WRITING,      "own_draft_record",        False),
    ("student",    Action.VIEW_OWN_WRITING,      "other_record",            False),
    ("student",    Action.VIEW_OWN_WRITING,      "group_mate_record",       False),
    ("student",    Action.VIEW_RECORDS,          "own_final_record",        False),
    ("student",    Action.VIEW_RECORD_STATUS,    "own_appointment",         False),
    ("tutor",      Action.VIEW_RECORDS,          "record_of_my_student",    True),
    ("tutor",      Action.VIEW_RECORDS,          "draft_of_my_student",     True),
    ("tutor",      Action.VIEW_RECORDS,          "own_written_record",      True),
    ("tutor",      Action.VIEW_RECORDS,          "other_record",            False),
    ("tutor",      Action.VIEW_RECORDS,          "canceled_student_record", False),
    ("tutor",      Action.VIEW_RECORDS,          "left_group_record",       False),
    ("tutor",      Action.VIEW_RECORDS,          "my_student",              True),
    ("tutor",      Action.VIEW_RECORDS,          "upcoming_student",        True),
    ("tutor",      Action.VIEW_RECORDS,          "other_student",           False),
    ("tutor",      Action.VIEW_RECORDS,          "my_student_project",      True),
    ("tutor",      Action.VIEW_RECORDS,          "other_project",           False),
    ("tutor",      Action.WRITE_RECORDS,         "own_tutoring",            True),
    ("tutor",      Action.WRITE_RECORDS,         "other_appointment",       False),
    ("tutor",      Action.WRITE_RECORDS,         "own_written_record",      True),
    ("tutor",      Action.WRITE_RECORDS,         "record_of_my_student",    False),
    ("tutor",      Action.WRITE_RECORDS,         "my_student_project",      True),
    ("tutor",      Action.WRITE_RECORDS,         "other_project",           False),
    ("tutor",      Action.VIEW_RECORD_STATUS,    "own_tutoring",            True),
    ("tutor",      Action.VIEW_RECORD_STATUS,    "other_appointment",       False),
    ("tutor",      Action.EXPORT_STUDENT_RECORD, "my_student",              False),
    ("front_desk", Action.VIEW_RECORD_STATUS,    "other_appointment",       True),
    ("front_desk", Action.VIEW_RECORDS,          "other_record",            False),
    ("front_desk", Action.VIEW_RECORDS,          "other_student",           False),
    ("front_desk", Action.WRITE_RECORDS,         "other_appointment",       False),
    ("director",   Action.VIEW_RECORDS,          "other_record",            True),
    ("director",   Action.VIEW_RECORDS,          "draft_of_my_student",     True),
    ("director",   Action.VIEW_RECORDS,          "other_student",           True),
    ("director",   Action.WRITE_RECORDS,         "other_record",            True),
    ("director",   Action.WRITE_RECORDS,         "other_appointment",       True),
    ("director",   Action.EXPORT_STUDENT_RECORD, "other_student",           True),
    ("director",   Action.VIEW_OWN_WRITING,      "other_record",            False),
    ("site_admin", Action.VIEW_RECORDS,          "other_record",            False),
    ("site_admin", Action.VIEW_RECORDS,          "other_student",           False),
    ("site_admin", Action.EXPORT_STUDENT_RECORD, "other_student",           False),
    ("site_admin", Action.VIEW_RECORD_STATUS,    "other_appointment",       False),
    ("site_admin", Action.WRITE_RECORDS,         "other_appointment",       False),
]
# fmt: on


def _record_objects(center, user):
    """Records, students, projects and appointments around `user`, one of each kind."""
    world = build_world(center)
    other_tutor = world.tutor

    def student():
        return make_member(center, Role.STUDENT)

    # The user's own sessions as a student: one written up, one still a draft.
    own_final = standalone(center, user, other_tutor, final=True)
    own_draft = standalone(center, user, other_tutor, days=-3)
    own_appointment = appointment_for(world, user, days=-4)

    # A group the user was in. The other student's record is theirs alone.
    mate = student()
    group = appointment_for(world, [user, mate], days=-5, capacity=4)
    group_mate_record = standalone(center, mate, other_tutor, final=True, appointment=group)

    # Students the user tutors: past, upcoming, canceled, and one who left a group.
    mine = student()
    appointment_for(world, mine, days=-6, tutor=user)
    upcoming = student()
    appointment_for(world, upcoming, days=3, tutor=user)
    canceled = student()
    appointment_for(world, canceled, days=-7, tutor=user, status=AppointmentStatus.CANCELED)
    left = student()
    left_group = appointment_for(world, [left, student()], days=-8, tutor=user, capacity=4)
    Participant.objects.filter(appointment=left_group, student=left).update(
        canceled_at=left_group.starts_at
    )
    own_tutoring = appointment_for(world, mine, days=-9, tutor=user)

    stranger = world.student
    return {
        "own_final_record": own_final,
        "own_draft_record": own_draft,
        "own_appointment": own_appointment,
        "group_mate_record": group_mate_record,
        "record_of_my_student": standalone(center, mine, other_tutor, final=True),
        "draft_of_my_student": standalone(center, mine, other_tutor),
        "own_written_record": standalone(center, student(), user, final=True),
        "other_record": standalone(center, stranger, other_tutor, final=True),
        "canceled_student_record": standalone(center, canceled, other_tutor, final=True),
        "left_group_record": standalone(center, left, other_tutor, final=True),
        "my_student": membership(center, mine),
        "upcoming_student": membership(center, upcoming),
        "other_student": membership(center, stranger),
        "my_student_project": WritingProject.objects.create(
            center=center, student=mine, title="Mine"
        ),
        "other_project": WritingProject.objects.create(
            center=center, student=stranger, title="Theirs"
        ),
        "own_tutoring": own_tutoring,
        "other_appointment": world.appointment,
    }


@pytest.mark.parametrize(("role", "action", "name", "expected"), RECORD_CASES)
def test_record_access(person_with_role, center, role, action, name, expected) -> None:
    user = person_with_role(role, center)
    obj = _record_objects(center, user)[name]
    assert policy.can(user, action, obj) is expected


def test_someone_elses_session_record_is_hidden_not_forbidden(person_with_role, center) -> None:
    """Students and tutors who may read some records get a 404 for anyone else's."""
    student = person_with_role("student", center)
    other = _record_objects(center, student)["other_record"]
    with pytest.raises(Http404):
        policy.get_for_action(
            student, Action.VIEW_OWN_WRITING, SessionRecord.objects.all(), pk=other.pk
        )
    tutor = person_with_role("tutor", center)
    with pytest.raises(Http404):
        policy.get_for_action(tutor, Action.VIEW_RECORDS, SessionRecord.objects.all(), pk=other.pk)
    # Front desk staff have no grant to read records at all, so it's a 403.
    desk = person_with_role("front_desk", center)
    with pytest.raises(PermissionDenied):
        policy.get_for_action(desk, Action.VIEW_RECORDS, SessionRecord.objects.all(), pk=other.pk)


def test_record_access_ends_when_the_appointment_is_canceled(person_with_role, center) -> None:
    tutor = person_with_role("tutor", center)
    world = build_world(center)
    student = make_member(center, Role.STUDENT)
    appointment = appointment_for(world, student, days=-2, tutor=tutor)
    rec = standalone(center, student, world.tutor, final=True)
    assert policy.can(tutor, Action.VIEW_RECORDS, rec)
    Appointment.objects.filter(pk=appointment.pk).update(status=AppointmentStatus.CANCELED)
    assert not policy.can(tutor, Action.VIEW_RECORDS, rec)
    assert not policy.can(tutor, Action.VIEW_RECORDS, membership(center, student))


# --- Forms ----------------------------------------------------------------------------

# (role, action, object, expected). For a student, "own" things are about them and
# "left" is a group they left. For a tutor, "my student" is a student they have an
# appointment with, and "own tutoring" an appointment they teach.
# fmt: off
FORM_CASES = [
    ("student",    Action.ANSWER_OWN_FORMS,      "own_appointment",            True),
    ("student",    Action.ANSWER_OWN_FORMS,      "left_appointment",           False),
    ("student",    Action.ANSWER_OWN_FORMS,      "other_appointment",          False),
    ("student",    Action.ANSWER_OWN_FORMS,      "own_registration",           True),
    ("student",    Action.ANSWER_OWN_FORMS,      "other_registration",         False),
    ("student",    Action.ANSWER_OWN_FORMS,      "own_membership",             True),
    ("student",    Action.ANSWER_OWN_FORMS,      "other_student",              False),
    ("student",    Action.VIEW_INTAKE,           "own_appointment",            False),
    ("student",    Action.VIEW_REGISTRATION,     "own_membership",             False),
    ("tutor",      Action.VIEW_INTAKE,           "own_tutoring",               True),
    ("tutor",      Action.VIEW_INTAKE,           "other_appointment",          False),
    ("tutor",      Action.VIEW_INTAKE,           "intake_of_own_tutoring",     True),
    ("tutor",      Action.VIEW_INTAKE,           "other_intake",               False),
    ("tutor",      Action.VIEW_REGISTRATION,     "my_student",                 True),
    ("tutor",      Action.VIEW_REGISTRATION,     "other_student",              False),
    ("tutor",      Action.VIEW_REGISTRATION,     "registration_of_my_student", True),
    ("tutor",      Action.VIEW_REGISTRATION,     "other_registration",         False),
    ("tutor",      Action.VIEW_ALL_REGISTRATION, "my_student",                 False),
    ("tutor",      Action.MANAGE_FORMS,          "form",                       False),
    ("front_desk", Action.VIEW_INTAKE,           "other_appointment",          True),
    ("front_desk", Action.VIEW_INTAKE,           "other_intake",               True),
    ("front_desk", Action.VIEW_REGISTRATION,     "other_student",              True),
    ("front_desk", Action.VIEW_ALL_REGISTRATION, "other_student",              False),
    ("front_desk", Action.ANSWER_OWN_FORMS,      "other_appointment",          False),
    ("front_desk", Action.MANAGE_FORMS,          "form",                       False),
    ("director",   Action.MANAGE_FORMS,          "form",                       True),
    ("director",   Action.VIEW_INTAKE,           "other_intake",               True),
    ("director",   Action.VIEW_REGISTRATION,     "other_student",              True),
    ("director",   Action.VIEW_ALL_REGISTRATION, "other_student",              True),
    ("director",   Action.VIEW_ALL_REGISTRATION, "other_registration",         True),
    ("director",   Action.ANSWER_OWN_FORMS,      "other_appointment",          False),
    ("site_admin", Action.MANAGE_FORMS,          "form",                       False),
    ("site_admin", Action.VIEW_INTAKE,           "other_intake",               False),
    ("site_admin", Action.VIEW_ALL_REGISTRATION, "other_student",              False),
    ("director",   Action.MANAGE_FORMS,          "survey",                     True),
    ("director",   Action.MANAGE_FORMS,          "report",                     True),
    ("director",   Action.VIEW_SURVEY_RESULTS,   "survey",                     True),
    ("front_desk", Action.VIEW_SURVEY_RESULTS,   "survey",                     False),
    ("tutor",      Action.VIEW_SURVEY_RESULTS,   "survey",                     False),
    ("tutor",      Action.MANAGE_FORMS,          "report",                     False),
    ("student",    Action.VIEW_SURVEY_RESULTS,   "survey",                     False),
    ("student",    Action.ANSWER_OWN_FORMS,      "survey",                     False),
    ("site_admin", Action.VIEW_SURVEY_RESULTS,   "survey",                     False),
]
# fmt: on


def _form_objects(center, user):
    """Forms, answers, appointments and students around `user`, one of each kind."""
    world = build_world(center)
    registration = make_form(center, FormKind.REGISTRATION, questions=REGISTRATION)
    intake = make_form(center)
    other_registration = answer(registration, world.student, REGISTRATION_ANSWERS)
    other_intake = answer(intake, world.student, INTAKE_ANSWERS, appointment=world.appointment)

    # The user's own appointments as a student: one they're in, one group they left.
    own_appointment = appointment_for(world, user, days=2)
    left = appointment_for(world, [user, make_member(center, Role.STUDENT)], days=3, capacity=4)
    Participant.objects.filter(appointment=left, student=user).update(canceled_at=left.starts_at)

    # A student the user tutors, with intake and registration answers.
    mine = make_member(center, Role.STUDENT)
    own_tutoring = appointment_for(world, mine, days=4, tutor=user)
    objects = {
        "form": intake,
        "survey": make_form(center, FormKind.SURVEY, questions=SURVEY),
        "report": make_form(center, FormKind.REPORT, questions=REPORT),
        "own_appointment": own_appointment,
        "left_appointment": left,
        "other_appointment": world.appointment,
        "own_tutoring": own_tutoring,
        "other_registration": other_registration,
        "other_intake": other_intake,
        "intake_of_own_tutoring": answer(intake, mine, INTAKE_ANSWERS, appointment=own_tutoring),
        "registration_of_my_student": answer(registration, mine, REGISTRATION_ANSWERS),
        "my_student": membership(center, mine),
        "other_student": membership(center, world.student),
    }
    own = Membership.objects.filter(user=user, center=center, role=Role.STUDENT).first()
    if own is not None:
        objects["own_membership"] = own
        objects["own_registration"] = answer(registration, user, REGISTRATION_ANSWERS)
    return objects


@pytest.mark.parametrize(("role", "action", "name", "expected"), FORM_CASES)
def test_form_access(person_with_role, center, role, action, name, expected) -> None:
    user = person_with_role(role, center)
    obj = _form_objects(center, user)[name]
    assert policy.can(user, action, obj) is expected


def test_someone_elses_answers_are_hidden_not_forbidden(person_with_role, center) -> None:
    """A tutor who may read some students' registration answers gets a 404 for others'."""
    tutor = person_with_role("tutor", center)
    other = _form_objects(center, tutor)["other_student"]
    with pytest.raises(Http404):
        policy.get_for_action(
            tutor, Action.VIEW_REGISTRATION, Membership.objects.all(), pk=other.pk
        )
    student = person_with_role("student", center)
    with pytest.raises(PermissionDenied):
        policy.get_for_action(
            student, Action.VIEW_REGISTRATION, Membership.objects.all(), pk=other.pk
        )


# --- Reports ----------------------------------------------------------------------------

# (role, action, expected) for one of the center's exports, asked for by another
# director. Any director of the center may download it; no one else may.
# fmt: off
EXPORT_CASES = [
    ("director",   Action.EXPORT_CENTER, True),
    ("front_desk", Action.EXPORT_CENTER, False),
    ("tutor",      Action.EXPORT_CENTER, False),
    ("student",    Action.EXPORT_CENTER, False),
    ("site_admin", Action.EXPORT_CENTER, False),
    ("none",       Action.EXPORT_CENTER, False),
]
# fmt: on


@pytest.mark.parametrize(("role", "action", "expected"), EXPORT_CASES)
def test_export_access(person_with_role, center, role, action, expected) -> None:
    export = CenterExport.objects.create(center=center, requested_by=UserFactory())
    user = person_with_role(role, center)
    assert policy.can(user, action, export) is expected


def test_an_export_from_another_center_is_hidden(person_with_role, center, other_center) -> None:
    export = CenterExport.objects.create(center=other_center)
    director = person_with_role("director", center)
    with pytest.raises(Http404):
        policy.get_for_action(
            director, Action.EXPORT_CENTER, CenterExport.objects.all(), pk=export.pk
        )
