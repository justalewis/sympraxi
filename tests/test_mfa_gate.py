import pytest
from django.urls import reverse

from tests.factories import give_mfa


@pytest.mark.parametrize("role", ["tutor", "front_desk", "director", "site_admin"])
def test_staff_without_2fa_are_sent_to_setup(client, person_with_role, center, role) -> None:
    client.force_login(person_with_role(role, center, mfa=False))
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa_index"))


@pytest.mark.parametrize("role", ["tutor", "director", "site_admin"])
def test_staff_with_2fa_get_through(client, person_with_role, center, role) -> None:
    user = person_with_role(role, center, mfa=False)
    give_mfa(user)
    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200


def test_students_are_not_forced(client, person_with_role, center) -> None:
    client.force_login(person_with_role("student", center))
    assert client.get(reverse("core:home")).status_code == 200


def test_admin_is_behind_the_gate(client, person_with_role, center) -> None:
    client.force_login(person_with_role("site_admin", center, mfa=False))
    response = client.get(reverse("admin:index"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa_index"))


def test_setup_page_itself_is_reachable(client, person_with_role, center) -> None:
    client.force_login(person_with_role("director", center, mfa=False))
    assert client.get(reverse("mfa_index")).status_code == 200


def test_gate_can_be_switched_off(client, settings, person_with_role, center) -> None:
    settings.SYMPRAXI_REQUIRE_STAFF_MFA = False
    client.force_login(person_with_role("director", center, mfa=False))
    assert client.get(reverse("core:home")).status_code == 200
