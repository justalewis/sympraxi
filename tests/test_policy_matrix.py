"""Every role against every action.

The expected results are written out by hand on purpose. Deriving them from
`ROLE_GRANTS` would make the test agree with whatever the code says. When you add
an `Action`, this file fails until you decide, row by row, who gets it.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404

from sympraxi.centers.models import Center, Membership
from sympraxi.core import policy
from sympraxi.core.policy import Action
from sympraxi.scheduling.models import Appointment, Participant, Visibility
from tests.factories import MembershipFactory, UserFactory
from tests.scheduling_world import build_world, make_schedule

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

ACTIONS = CENTER_ACTIONS + SCHEDULING_ACTIONS

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
# fmt: on

CASES = [
    (role, action, bool(expected[i]))
    for table, actions in (
        (EXPECTED_IN_OWN_CENTER, CENTER_ACTIONS),
        (EXPECTED_SCHEDULING, SCHEDULING_ACTIONS),
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
    return {
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
