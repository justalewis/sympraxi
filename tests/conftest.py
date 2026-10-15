import pytest

from sympraxi.accounts.adapter import SocialAccountAdapter
from sympraxi.centers.models import Center, Role
from tests import sso_idp
from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa


@pytest.fixture
def idp(monkeypatch) -> sso_idp.FakeIdP:
    """Answer allauth's requests to Entra and Google from memory."""
    fake = sso_idp.FakeIdP()
    monkeypatch.setattr(SocialAccountAdapter, "get_requests_session", lambda self: fake.session())
    return fake


@pytest.fixture
def sso(settings, idp) -> sso_idp.FakeIdP:
    """Entra ID and Google Workspace set up for example.edu, behind the fake provider."""
    settings.SOCIALACCOUNT_PROVIDERS = sso_idp.providers()
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    return idp


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
