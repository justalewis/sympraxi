"""People change their own name, preferred name and pronouns under Account."""

import pytest
from django.urls import reverse

from sympraxi.accounts.models import User
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

URL = reverse("accounts:profile")


@pytest.fixture
def person(client) -> User:
    user = UserFactory(full_name="Sam Rivera", preferred_name="", pronouns="")
    client.force_login(user)
    return user


def test_the_header_and_account_menu_lead_here(client, person) -> None:
    page = client.get(URL).content.decode()
    assert f'href="{URL}"' in page
    assert 'aria-current="page">Name and pronouns' in page
    assert 'value="Sam Rivera"' in page


def test_a_person_changes_their_name_and_pronouns(client, person) -> None:
    response = client.post(
        URL, {"full_name": "Samuel Rivera", "preferred_name": "Sam", "pronouns": "he/him"}
    )
    assert response.status_code == 302
    person.refresh_from_db()
    assert (person.full_name, person.preferred_name, person.pronouns) == (
        "Samuel Rivera",
        "Sam",
        "he/him",
    )
    assert person.display_name == "Sam"
    assert "Saved." in client.get(URL).content.decode()


def test_each_change_is_audited_without_the_names(client, person) -> None:
    client.post(URL, {"full_name": "Sam Rivera", "preferred_name": "", "pronouns": "they/them"})
    event = AuditEvent.objects.get(action=AuditAction.PROFILE_CHANGED)
    assert event.actor == person
    assert event.target_id == str(person.pk)
    assert event.details == {"fields": ["pronouns"]}
    assert "they/them" not in str(event.details)


def test_saving_without_a_change_records_nothing(client, person) -> None:
    client.post(URL, {"full_name": "Sam Rivera", "preferred_name": "", "pronouns": ""})
    assert not AuditEvent.objects.filter(action=AuditAction.PROFILE_CHANGED).exists()


def test_a_full_name_is_required(client, person) -> None:
    response = client.post(URL, {"full_name": "  ", "preferred_name": "S", "pronouns": ""})
    page = response.content.decode()
    assert response.status_code == 200
    assert 'id="id_full_name_error"' in page
    assert 'aria-invalid="true"' in page
    person.refresh_from_db()
    assert (person.full_name, person.preferred_name) == ("Sam Rivera", "")


def test_nothing_else_about_the_account_can_change_here(client, person) -> None:
    client.post(
        URL,
        {
            "full_name": "Sam Rivera",
            "email": "someone-else@example.edu",
            "student_id": "999",
            "is_superuser": "on",
            "is_staff": "on",
        },
    )
    person.refresh_from_db()
    assert person.email != "someone-else@example.edu"
    assert person.student_id is None
    assert not person.is_superuser
    assert not person.is_staff
