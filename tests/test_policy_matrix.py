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
from sympraxi.records.models import SessionRecord, WritingProject
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Block,
    BlockReason,
    Participant,
    Visibility,
)
from tests.factories import MembershipFactory, UserFactory
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

ACTIONS = CENTER_ACTIONS + SCHEDULING_ACTIONS + ATTENDANCE_ACTIONS + RECORDS_ACTIONS

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
# fmt: on

CASES = [
    (role, action, bool(expected[i]))
    for table, actions in (
        (EXPECTED_IN_OWN_CENTER, CENTER_ACTIONS),
        (EXPECTED_SCHEDULING, SCHEDULING_ACTIONS),
        (EXPECTED_ATTENDANCE, ATTENDANCE_ACTIONS),
        (EXPECTED_RECORDS, RECORDS_ACTIONS),
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
