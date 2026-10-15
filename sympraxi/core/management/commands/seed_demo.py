"""Create a demo center with one person in each role, for development only."""

import secrets
import zoneinfo
from datetime import time, timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.scheduling.models import Schedule, Term, TutorAssignment, WeeklyHours

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
        self._schedule(center)
        self.stdout.write(self.style.SUCCESS(f"Demo center ready at /centers/{center.slug}/"))
        self.stdout.write("Accounts: " + ", ".join(e for e, _, _ in DEMO_PEOPLE))
        if options["password"] is None:
            self.stdout.write(f"Password for new accounts: {password}")
        self.stdout.write(
            "Staff accounts will be asked to set up two-factor login on first sign-in."
        )

    def _schedule(self, center: Center) -> None:
        """A drop-in schedule for the next three months, with the demo tutor on weekdays."""
        today = timezone.localdate(timezone=zoneinfo.ZoneInfo(center.time_zone))
        term, _ = Term.objects.get_or_create(
            center=center,
            name="Demo term",
            defaults={
                "starts_on": today - timedelta(days=7),
                "ends_on": today + timedelta(days=120),
            },
        )
        schedule, _ = Schedule.objects.get_or_create(
            center=center,
            name="Drop-in tutoring",
            defaults={
                "term": term,
                "starts_on": max(today, term.starts_on),
                "ends_on": min(today + timedelta(days=90), term.ends_on),
                "max_per_week": 2,
            },
        )
        tutor = User.objects.get(email="tutor@demo.test")
        assignment, created = TutorAssignment.objects.get_or_create(
            schedule=schedule,
            tutor=tutor,
            defaults={"offers_online": True, "meeting_url": "https://meet.example.invalid/demo"},
        )
        if created:
            for weekday in range(5):
                WeeklyHours.objects.create(
                    assignment=assignment, weekday=weekday, starts_at=time(10), ends_at=time(16)
                )
