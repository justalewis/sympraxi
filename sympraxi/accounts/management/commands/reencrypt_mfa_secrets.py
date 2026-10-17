"""Rewrite the stored two-factor secrets under the current SECRET_KEY."""

from typing import Any

from allauth.mfa.models import Authenticator
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from sympraxi.accounts import encryption
from sympraxi.privacy.audit import AuditAction, record


def under_current_key(value: str) -> str:
    if encryption.is_encrypted(value):
        return encryption.reencrypt(value)
    return encryption.encrypt(value)


class Command(BaseCommand):
    help = (
        "Re-encrypt every authenticator app secret and recovery-code seed under "
        "SYMPRAXI_SECRET_KEY. Run it after changing the key, with the old one in "
        "SYMPRAXI_SECRET_KEY_FALLBACKS; once it has finished, the fallback can go."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--remove-unreadable",
            action="store_true",
            help=(
                "Delete authenticators that no key can read, so their owners set up "
                "two-factor login again. Only for when the old key is lost."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        remove = options["remove_unreadable"]
        rewritten = 0
        unreadable = []
        with transaction.atomic():
            authenticators = (
                Authenticator.objects.filter(type__in=encryption.SECRET_FIELDS)
                .select_related("user")
                .select_for_update(of=("self",))
                .order_by("pk")
            )
            for authenticator in authenticators:
                try:
                    data = encryption.map_secrets(
                        authenticator.type, authenticator.data, under_current_key
                    )
                except encryption.UnreadableSecretError:
                    unreadable.append(authenticator)
                    continue
                authenticator.data = data
                authenticator.save(update_fields=["data"])
                rewritten += 1
            for authenticator in unreadable if remove else []:
                authenticator.delete()
                record(
                    AuditAction.MFA_REMOVED,
                    actor_label="manage.py reencrypt_mfa_secrets",
                    target=authenticator.user,
                    kind=authenticator.type,
                    reason="unreadable",
                )
        self.stdout.write(f"Re-encrypted {rewritten} under the current key.")
        for authenticator in unreadable:
            what = "Removed" if remove else "Can't read"
            self.stdout.write(f"{what}: {authenticator.type} for {authenticator.user.email}")
        if unreadable and not remove:
            raise CommandError(
                f"{len(unreadable)} couldn't be read. Put the key they were written under "
                "in SYMPRAXI_SECRET_KEY_FALLBACKS and run this again. If that key is lost, "
                "run it with --remove-unreadable, and those people set up two-factor "
                "login again."
            )
