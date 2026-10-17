"""Encrypt the TOTP secrets and recovery-code seeds stored before MFAAdapter.

Values that are already Fernet tokens are left alone, so running against a
database that holds both kinds is safe. Reversing it writes them back as they
were, which needs the key they were encrypted under.
"""

from django.db import migrations

from sympraxi.accounts import encryption


def encrypt_secrets(apps, schema_editor):
    Authenticator = apps.get_model("mfa", "Authenticator")
    for authenticator in Authenticator.objects.filter(type__in=encryption.SECRET_FIELDS):
        data = encryption.map_secrets(
            authenticator.type,
            authenticator.data,
            lambda value: value if encryption.is_encrypted(value) else encryption.encrypt(value),
        )
        if data != authenticator.data:
            authenticator.data = data
            authenticator.save(update_fields=["data"])


def decrypt_secrets(apps, schema_editor):
    Authenticator = apps.get_model("mfa", "Authenticator")
    for authenticator in Authenticator.objects.filter(type__in=encryption.SECRET_FIELDS):
        data = encryption.map_secrets(
            authenticator.type,
            authenticator.data,
            lambda value: encryption.decrypt(value) if encryption.is_encrypted(value) else value,
        )
        if data != authenticator.data:
            authenticator.data = data
            authenticator.save(update_fields=["data"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_survey_emails"),
        ("mfa", "0003_authenticator_type_uniq"),
    ]

    operations = [migrations.RunPython(encrypt_secrets, decrypt_secrets)]
