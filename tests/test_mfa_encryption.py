"""Two-factor secrets are encrypted in the database (sympraxi/accounts/encryption.py)."""

import importlib

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import (
    SECRET_SESSION_KEY,
    TOTP,
    format_hotp_value,
    hotp_value,
    yield_hotp_counters_from_time,
)
from django.apps import apps
from django.core.management import CommandError, call_command
from django.urls import reverse

from sympraxi.accounts import encryption
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests.factories import PASSWORD, TOTP_SECRET, UserFactory

pytestmark = pytest.mark.django_db

OLD_KEY = "the-key-before"
NEW_KEY = "the-key-after"

migration = importlib.import_module("sympraxi.accounts.migrations.0004_encrypt_mfa_secrets")


def current_code(secret: str) -> str:
    counters = list(yield_hotp_counters_from_time())
    counter = counters[len(counters) // 2]
    return format_hotp_value(hotp_value(secret, counter))


def sign_in_with_password(client, user) -> None:
    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})


def test_setting_up_an_authenticator_app_stores_its_secret_encrypted(client) -> None:
    user = UserFactory()
    sign_in_with_password(client, user)
    assert client.get(reverse("mfa_activate_totp")).status_code == 200
    secret = client.session[SECRET_SESSION_KEY]
    response = client.post(reverse("mfa_activate_totp"), {"code": current_code(secret)})
    assert response.status_code == 302

    totp = Authenticator.objects.get(user=user, type=Authenticator.Type.TOTP)
    assert secret not in str(totp.data)
    assert encryption.decrypt(totp.data["secret"]) == secret
    # allauth makes recovery codes alongside; their seed is encrypted too.
    codes = Authenticator.objects.get(user=user, type=Authenticator.Type.RECOVERY_CODES)
    assert encryption.is_encrypted(codes.data["seed"])
    assert len(RecoveryCodes(codes).get_unused_codes()) == 10


def test_a_code_from_the_app_signs_in(client) -> None:
    user = UserFactory()
    TOTP.activate(user, TOTP_SECRET)
    response = client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})
    assert response.url == reverse("mfa_authenticate")
    response = client.post(reverse("mfa_authenticate"), {"code": current_code(TOTP_SECRET)})
    assert response.status_code == 302
    assert client.get(reverse("core:home")).wsgi_request.user == user


def test_a_recovery_code_signs_in(client) -> None:
    user = UserFactory()
    TOTP.activate(user, TOTP_SECRET)
    code = RecoveryCodes(RecoveryCodes.activate(user).instance).get_unused_codes()[0]
    sign_in_with_password(client, user)
    response = client.post(reverse("mfa_authenticate"), {"code": code})
    assert response.status_code == 302
    assert client.get(reverse("core:home")).wsgi_request.user == user


def test_secrets_are_kept_from_the_django_admin(client) -> None:
    admin = UserFactory(is_superuser=True, is_staff=True)
    totp = TOTP.activate(admin, TOTP_SECRET).instance
    client.force_login(admin)
    page = client.get(reverse("admin:mfa_authenticator_change", args=[totp.pk]))
    assert page.status_code == 200
    assert TOTP_SECRET not in page.content.decode()


def test_a_fallback_key_still_reads_secrets_written_under_it(settings) -> None:
    settings.SECRET_KEY = OLD_KEY
    token = encryption.encrypt(TOTP_SECRET)
    settings.SECRET_KEY = NEW_KEY
    settings.SECRET_KEY_FALLBACKS = [OLD_KEY]
    assert encryption.decrypt(token) == TOTP_SECRET
    settings.SECRET_KEY_FALLBACKS = []
    with pytest.raises(encryption.UnreadableSecretError):
        encryption.decrypt(token)


def test_the_key_is_derived_for_this_purpose_only() -> None:
    assert encryption.fernet_key(OLD_KEY) != encryption.fernet_key(NEW_KEY)
    assert OLD_KEY.encode() not in encryption.fernet_key(OLD_KEY)


def test_reencrypting_moves_every_secret_to_the_new_key(settings, client) -> None:
    settings.SECRET_KEY = OLD_KEY
    user = UserFactory()
    TOTP.activate(user, TOTP_SECRET)
    code = RecoveryCodes.activate(user).generate_codes()[0]

    settings.SECRET_KEY = NEW_KEY
    settings.SECRET_KEY_FALLBACKS = [OLD_KEY]
    call_command("reencrypt_mfa_secrets")
    settings.SECRET_KEY_FALLBACKS = []

    totp = Authenticator.objects.get(user=user, type=Authenticator.Type.TOTP)
    assert encryption.decrypt(totp.data["secret"]) == TOTP_SECRET
    codes = Authenticator.objects.get(user=user, type=Authenticator.Type.RECOVERY_CODES)
    assert RecoveryCodes(codes).generate_codes()[0] == code
    sign_in_with_password(client, user)
    response = client.post(reverse("mfa_authenticate"), {"code": current_code(TOTP_SECRET)})
    assert response.status_code == 302


def test_reencrypting_stops_at_secrets_no_key_can_read(settings) -> None:
    settings.SECRET_KEY = OLD_KEY
    user = UserFactory()
    TOTP.activate(user, TOTP_SECRET)
    before = Authenticator.objects.get(user=user).data

    settings.SECRET_KEY = NEW_KEY
    with pytest.raises(CommandError, match="1 couldn't be read"):
        call_command("reencrypt_mfa_secrets")
    assert Authenticator.objects.get(user=user).data == before
    assert not AuditEvent.objects.filter(action=AuditAction.MFA_REMOVED).exists()


def test_unreadable_authenticators_can_be_removed_when_the_key_is_lost(settings) -> None:
    settings.SECRET_KEY = OLD_KEY
    user = UserFactory()
    TOTP.activate(user, TOTP_SECRET)
    readable = UserFactory()

    settings.SECRET_KEY = NEW_KEY
    TOTP.activate(readable, TOTP_SECRET)
    call_command("reencrypt_mfa_secrets", "--remove-unreadable")

    assert not Authenticator.objects.filter(user=user).exists()
    assert Authenticator.objects.filter(user=readable).exists()
    event = AuditEvent.objects.get(action=AuditAction.MFA_REMOVED)
    assert event.target_id == str(user.pk)
    assert event.actor_label == "manage.py reencrypt_mfa_secrets"
    assert event.details == {"kind": "totp", "reason": "unreadable"}


def test_the_migration_encrypts_secrets_stored_before_it_once() -> None:
    user = UserFactory()
    seed = "ab" * 40
    totp = Authenticator.objects.create(
        user=user, type=Authenticator.Type.TOTP, data={"secret": TOTP_SECRET}
    )
    codes = Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.RECOVERY_CODES,
        data={"seed": seed, "used_mask": 1, "migrated_codes": ["12345678"]},
    )
    passkey = Authenticator.objects.create(
        user=user, type=Authenticator.Type.WEBAUTHN, data={"credential": {"id": "abc"}}
    )

    migration.encrypt_secrets(apps, None)
    totp.refresh_from_db()
    codes.refresh_from_db()
    assert encryption.decrypt(totp.data["secret"]) == TOTP_SECRET
    assert encryption.decrypt(codes.data["seed"]) == seed
    assert [encryption.decrypt(c) for c in codes.data["migrated_codes"]] == ["12345678"]
    assert codes.data["used_mask"] == 1
    passkey.refresh_from_db()
    assert passkey.data == {"credential": {"id": "abc"}}
    assert TOTP(totp).validate_code(current_code(TOTP_SECRET))

    encrypted = totp.data
    migration.encrypt_secrets(apps, None)
    totp.refresh_from_db()
    assert totp.data == encrypted

    migration.decrypt_secrets(apps, None)
    totp.refresh_from_db()
    codes.refresh_from_db()
    assert totp.data == {"secret": TOTP_SECRET}
    assert codes.data == {"seed": seed, "used_mask": 1, "migrated_codes": ["12345678"]}


def test_plain_secrets_never_look_encrypted() -> None:
    assert not encryption.is_encrypted(TOTP_SECRET)
    assert not encryption.is_encrypted("0123456789abcdef" * 5)
    assert not encryption.is_encrypted("12345678")
    assert encryption.is_encrypted(encryption.encrypt("anything"))
