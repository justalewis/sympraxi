"""Every role against every action.

The expected results are written out by hand on purpose. Deriving them from
`ROLE_GRANTS` would make the test agree with whatever the code says. When you add
an `Action`, this file fails until you decide, row by row, who gets it.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404

from sympraxi.centers.models import Center, Membership
from sympraxi.core import policy
from sympraxi.core.policy import Action
from tests.factories import UserFactory

ACTIONS = [
    Action.VIEW_CENTER,
    Action.MANAGE_CENTER,
    Action.VIEW_MEMBERS,
    Action.MANAGE_MEMBERS,
    Action.VIEW_AUDIT_LOG,
]

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
# fmt: on

CASES = [
    (role, action, bool(expected[i]))
    for role, expected in EXPECTED_IN_OWN_CENTER.items()
    for i, action in enumerate(ACTIONS)
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
