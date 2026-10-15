import pytest
from django.core import mail
from django.db import IntegrityError, transaction
from django.urls import reverse

from sympraxi.accounts.models import User
from tests.factories import PASSWORD, UserFactory

SIGNUP = {
    "email": "new.student@example.edu",
    "full_name": "Alex Rivera",
    "preferred_name": "Lex",
    "pronouns": "they/them",
    "password1": "a-long-and-unusual-passphrase",
    "password2": "a-long-and-unusual-passphrase",
}


@pytest.mark.django_db
def test_signup_saves_names_and_sends_verification(client) -> None:
    response = client.post(reverse("account_signup"), SIGNUP)
    assert response.status_code == 302
    user = User.objects.get(email="new.student@example.edu")
    assert (user.full_name, user.preferred_name, user.pronouns) == (
        "Alex Rivera",
        "Lex",
        "they/them",
    )
    assert user.display_name == "Lex"
    assert len(mail.outbox) == 1
    assert "new.student@example.edu" in mail.outbox[0].to


@pytest.mark.django_db
def test_unverified_email_cannot_sign_in(client) -> None:
    UserFactory(email="pending@example.edu", verified=False)
    response = client.post(
        reverse("account_login"), {"login": "pending@example.edu", "password": PASSWORD}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("account_email_verification_sent")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_verified_user_signs_in(client) -> None:
    UserFactory(email="ready@example.edu")
    response = client.post(
        reverse("account_login"), {"login": "ready@example.edu", "password": PASSWORD}
    )
    assert response.status_code == 302
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_signup_can_be_limited_to_college_domains(client, settings) -> None:
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    response = client.post(reverse("account_signup"), {**SIGNUP, "email": "someone@gmail.com"})
    assert response.status_code == 200
    assert "college email address" in response.content.decode()
    assert not User.objects.filter(email="someone@gmail.com").exists()


@pytest.mark.django_db
def test_signup_can_be_closed(client, settings) -> None:
    settings.SYMPRAXI_SIGNUP_OPEN = False
    client.post(reverse("account_signup"), SIGNUP)
    assert not User.objects.filter(email=SIGNUP["email"]).exists()


@pytest.mark.django_db
def test_email_is_unique_regardless_of_case() -> None:
    UserFactory(email="Case@Example.edu", verified=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        UserFactory(email="case@example.edu", verified=False)


def test_passwords_are_hashed_with_argon2_in_production() -> None:
    from sympraxi import settings as production

    assert production.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
