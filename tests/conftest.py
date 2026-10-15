import pytest

from sympraxi.centers.models import Center, Role
from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa


@pytest.fixture
def center(db) -> Center:
    return CenterFactory()


@pytest.fixture
def other_center(db) -> Center:
    return CenterFactory()


@pytest.fixture
def person_with_role(db):
    """Build a user for a role name: none, student, tutor, front_desk, director, site_admin."""

    def make(role_name: str, center: Center, *, mfa: bool = True):
        if role_name == "site_admin":
            user = UserFactory(is_superuser=True, is_staff=True)
        else:
            user = UserFactory()
            if role_name != "none":
                MembershipFactory(user=user, center=center, role=Role(role_name))
        if mfa and role_name not in ("none", "student"):
            give_mfa(user)
        return user

    return make
