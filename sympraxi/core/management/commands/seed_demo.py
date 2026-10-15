"""Create a demo center with one person in each role, for development only."""

import secrets
import zoneinfo
from datetime import time, timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.records import services as records
from sympraxi.records.models import Resource, SessionRecord, Topic
from sympraxi.scheduling.models import (
    Appointment,
    Modality,
    Participant,
    Schedule,
    Term,
    TutorAssignment,
    WeeklyHours,
)
from sympraxi.scheduling.timeutils import first_instant

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
        assignment = self._schedule(center)
        call_command("load_catalog", stdout=self.stdout)
        self._records(center, assignment)
        self.stdout.write(self.style.SUCCESS(f"Demo center ready at /centers/{center.slug}/"))
        self.stdout.write("Accounts: " + ", ".join(e for e, _, _ in DEMO_PEOPLE))
        if options["password"] is None:
            self.stdout.write(f"Password for new accounts: {password}")
        self.stdout.write(
            "Staff accounts will be asked to set up two-factor login on first sign-in."
        )

    def _schedule(self, center: Center) -> TutorAssignment:
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
        return assignment

    def _records(self, center: Center, assignment: TutorAssignment) -> None:
        """Three past sessions for the demo student: two written up, one waiting for its record."""
        student = User.objects.get(email="student@demo.test")
        if SessionRecord.objects.filter(center=center, student=student).exists():
            return
        tutor = assignment.tutor
        tz = zoneinfo.ZoneInfo(center.time_zone)
        today = timezone.localdate(timezone=tz)

        def past_session(days_ago: int) -> Appointment:
            start = first_instant(today - timedelta(days=days_ago), time(11), tz)
            appointment = Appointment.objects.create(
                schedule=assignment.schedule,
                assignment=assignment,
                tutor=tutor,
                starts_at=start,
                ends_at=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=45),
                modality=Modality.IN_PERSON,
            )
            Participant.objects.create(appointment=appointment, student=student)
            return appointment

        def topics(*slugs: str) -> list[Topic]:
            return list(Topic.objects.filter(slug__in=slugs))

        def resource(catalog_id: str, note: str = "") -> list[tuple[Resource, str]]:
            found = Resource.objects.filter(catalog_id=catalog_id).first()
            return [(found, note)] if found else []

        project = records.new_project(
            actor=tutor,
            center=center,
            student=student,
            title="Argument essay on campus food policy",
            course="ENG 101",
            instructor="Prof. Okafor",
            due_on=today + timedelta(days=10),
        )
        first = records.start_for_appointment(
            actor=tutor, appointment=past_session(9), student=student
        )
        records.finalize(
            actor=tutor,
            rec=first,
            content=records.Content(
                minutes=45,
                summary=(
                    "You came in with a first draft and a working thesis. We read the "
                    "introduction aloud and found that your claim appears in the last "
                    "paragraph instead of the first. You moved it up and tightened it."
                ),
                next_steps="Rewrite each topic sentence so it ties back to the new thesis.",
                staff_notes="Confident writer. Asked good questions about counterarguments.",
                topics=topics(
                    "argument:thesis", "organization:structure", "revising", "read-aloud"
                ),
                project=project,
                resources=resource(
                    "purdue-owl-thesis-statement-tips", "Look at the argumentative examples."
                ),
            ),
            notify=False,
        )
        second = records.start_for_appointment(
            actor=tutor, appointment=past_session(2), student=student
        )
        records.finalize(
            actor=tutor,
            rec=second,
            content=records.Content(
                minutes=45,
                summary=(
                    "We worked on bringing your sources into your paragraphs. You practiced "
                    "introducing two quotations with signal phrases and explaining each one."
                ),
                next_steps="Do the same for the three quotations in your second section.",
                topics=topics("evidence:integrating-sources", "evidence:quoting", "revising"),
                project=project,
                resources=resource("purdue-owl-signal-phrases")
                + resource("purdue-owl-apa-in-text-basics", "For the page numbers."),
            ),
            notify=False,
        )
        # Yesterday's session has no record yet, so it shows on "records to write".
        past_session(1)
