"""Create a demo center with one person in each role, for development only."""

import secrets
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role

DEMO_PEOPLE = [
    ("director@demo.test", "Dana Director", Role.DIRECTOR),
    ("frontdesk@demo.test", "Felix Frontdesk", Role.FRONT_DESK),
    ("tutor@demo.test", "Tomás Tutor", Role.TUTOR),
    ("student@demo.test", "Sam Student", Role.STUDENT),
]


class Command(BaseCommand):
    help = "Create a demo center and demo accounts (development only; refuses when DEBUG is off)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--password",
            help="Password for every demo account. A random one is generated if omitted.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with SYMPRAXI_DEBUG=true.")
        password = options["password"] or secrets.token_urlsafe(12)
        center, _ = Center.objects.get_or_create(
            slug="demo",
            defaults={"name": "Demo Writing Center", "contact_email": "writing@demo.test"},
        )
        for email, name, role in DEMO_PEOPLE:
            user, created = User.objects.get_or_create(email=email, defaults={"full_name": name})
            if created:
                user.set_password(password)
                user.save()
                EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
            Membership.objects.get_or_create(user=user, center=center, role=role)
        self.stdout.write(self.style.SUCCESS(f"Demo center ready at /centers/{center.slug}/"))
        self.stdout.write("Accounts: " + ", ".join(e for e, _, _ in DEMO_PEOPLE))
        if options["password"] is None:
            self.stdout.write(f"Password for new accounts: {password}")
        self.stdout.write(
            "Staff accounts will be asked to set up two-factor login on first sign-in."
        )
