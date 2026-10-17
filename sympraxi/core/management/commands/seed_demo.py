"""Create a demo center with one person in each role, for development only."""

import secrets
import zoneinfo
from datetime import time, timedelta
from typing import Any
from uuid import UUID

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.forms import builder as form_builder
from sympraxi.forms import services as form_services
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import Form, FormKind, FormVersion
from sympraxi.records import services as records
from sympraxi.records.models import Resource, SessionRecord, Topic
from sympraxi.scheduling import attendance, services
from sympraxi.scheduling.models import (
    Appointment,
    Attendance,
    Block,
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
    ("student2@demo.test", "Robin Student", Role.STUDENT),
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
        self._attendance(center, assignment)
        self._forms(center)
        self.stdout.write(self.style.SUCCESS(f"Demo center ready at /centers/{center.slug}/"))
        self.stdout.write("Accounts: " + ", ".join(e for e, _, _ in DEMO_PEOPLE))
        if options["password"] is None:
            self.stdout.write(f"Password for new accounts: {password}")
        self.stdout.write(
            "Staff accounts will be asked to set up two-factor login on first sign-in."
        )
        self.stdout.write(
            "Run `manage.py scheduler` alongside the server to send reminders and flag "
            "possible no-shows."
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
                "late_cancel_minutes": 120,
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

    def _attendance(self, center: Center, assignment: TutorAssignment) -> None:
        """A late cancellation and a possible no-show for Sam, and three no-shows and a
        booking block for Robin, under the center's limit of three."""
        robin = User.objects.get(email="student2@demo.test")
        if Block.objects.filter(center=center, student=robin).exists():
            return
        center.no_show_block_limit = 3
        center.late_cancel_block_limit = 3
        center.save(update_fields=["no_show_block_limit", "late_cancel_block_limit"])
        schedule = assignment.schedule
        schedule.late_cancel_minutes = 120
        schedule.save(update_fields=["late_cancel_minutes"])
        sam = User.objects.get(email="student@demo.test")
        desk = User.objects.get(email="frontdesk@demo.test")
        tz = zoneinfo.ZoneInfo(center.time_zone)
        today = timezone.localdate(timezone=tz)

        def session(student: User, days_ago: int, hour: int) -> Appointment:
            start = first_instant(today - timedelta(days=days_ago), time(hour), tz)
            appointment = Appointment.objects.create(
                schedule=schedule,
                assignment=assignment,
                tutor=assignment.tutor,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                blocked_until=start + timedelta(minutes=30),
                modality=Modality.IN_PERSON,
            )
            Participant.objects.create(appointment=appointment, student=student)
            return appointment

        # Sam canceled 90 minutes before a session five days ago: late, since the
        # schedule counts anything within two hours.
        late = session(sam, 5, 15)
        services.cancel(
            actor=sam,
            appointment=late,
            as_staff=False,
            now=late.starts_at - timedelta(minutes=90),
            notify=False,
        )
        # Yesterday afternoon no one marked Sam, so it waits on "Attendance to check".
        missed = session(sam, 1, 14)
        Participant.objects.filter(appointment=missed).update(
            flagged_at=missed.starts_at + timedelta(minutes=15)
        )
        # Robin missed three sessions; the front desk confirmed each, and the third
        # paused Robin's online booking until the end of the term.
        for days_ago in (8, 6, 4):
            appointment = session(robin, days_ago, 13)
            attendance.mark(
                actor=desk,
                appointment=appointment,
                marks={robin.pk: Attendance.NO_SHOW},
                now=appointment.starts_at + timedelta(minutes=20),
                notify=False,
            )

    def _forms(self, center: Center) -> None:
        """A registration form and an intake form with a follow-up question. Sam answered
        both; the intake form was reworded after Sam's first answers, so version 1 and
        version 2 both have answers."""
        if Form.objects.filter(center=center).exists():
            return
        director = User.objects.get(email="director@demo.test")
        sam = User.objects.get(email="student@demo.test")

        def add(draft: FormVersion, label: str, kind: str = "short_text", **fields: Any) -> None:
            options: list[tuple[UUID | None, str]] = [(None, o) for o in fields.pop("options", [])]
            condition = None
            if "follows" in fields:
                source_label, option_label = fields.pop("follows")
                source = draft.questions.get(label=source_label)
                condition = (source.key, source.options.get(label=option_label).key)
            form_builder.save_question(
                draft=draft,
                question=None,
                data=QuestionData(
                    kind=kind, label=label, options=options, condition=condition, **fields
                ),
            )

        def answers(version: FormVersion, by_label: dict[str, Any]) -> dict[Any, Any]:
            found: dict[Any, Any] = {}
            for q in version.ordered_questions():
                if q.label in by_label:
                    value = by_label[q.label]
                    if q.is_choice:
                        labels = {o.label: o.key for o in q.options.all()}
                        value = [labels[v] for v in ([value] if isinstance(value, str) else value)]
                    found[q.key] = value
            return found

        registration = form_builder.create_form(
            actor=director, center=center, kind=FormKind.REGISTRATION
        )
        draft = registration.versions.get()  # the new form's empty draft
        draft.intro = (
            "A few questions the first time you book. Tutors and front desk staff see "
            "only your major and year."
        )
        draft.save(update_fields=["intro"])
        add(draft, "What is your major or program?", shown_to_staff=True)
        add(
            draft,
            "What year are you in?",
            "one_choice",
            options=["First year", "Second year", "Third year", "Fourth year or later", "Graduate"],
            shown_to_staff=True,
        )
        add(draft, "Is English your first language?", "yes_no", is_demographic=True)
        add(
            draft,
            "Are you the first in your family to go to college?",
            "yes_no",
            is_demographic=True,
        )
        reg_version = form_builder.publish(actor=director, draft=draft)

        intake = form_builder.create_form(actor=director, center=center, kind=FormKind.INTAKE)
        draft = intake.versions.get()
        add(draft, "What course is this for?", required=True, help_text="For example ENG 101.")
        add(
            draft,
            "What are you working on?",
            "one_choice",
            options=["An essay or paper", "A lab report", "An application", "Something else"],
            required=True,
        )
        add(draft, "What is it?", follows=("What are you working on?", "Something else"))
        add(
            draft,
            "What would you like to focus on?",
            "several_choices",
            options=["Ideas and argument", "Organization", "Using sources", "Citations"],
        )
        add(draft, "When is it due?", "date")
        add(draft, "Anything else your tutor should know?", "long_text")
        first_version = form_builder.publish(actor=director, draft=draft)

        form_services.save_response(
            version=reg_version,
            values=answers(
                reg_version,
                {
                    "What is your major or program?": "Environmental science",
                    "What year are you in?": "Second year",
                    "Is English your first language?": "Yes",
                },
            ),
            answered_by=sam,
            student=sam,
        )
        sessions = list(
            Appointment.objects.filter(schedule__center=center, participants__student=sam)
            .exclude(status="canceled")
            .order_by("starts_at")
        )
        if sessions:
            form_services.save_response(
                version=first_version,
                values=answers(
                    first_version,
                    {
                        "What course is this for?": "ENG 101",
                        "What are you working on?": "An essay or paper",
                        "What would you like to focus on?": ["Ideas and argument"],
                    },
                ),
                answered_by=sam,
                student=sam,
                appointment=sessions[0],
            )
        # The director reworded a question, so later answers use version 2.
        draft = form_builder.start_draft(actor=director, form=intake)
        working = draft.questions.get(label="What are you working on?")
        form_builder.save_question(
            draft=draft,
            question=working,
            data=QuestionData(
                kind=working.kind,
                label="What are you writing?",
                required=True,
                options=[(o.key, o.label) for o in working.options.all()],
            ),
        )
        second_version = form_builder.publish(actor=director, draft=draft)
        if len(sessions) > 1:
            form_services.save_response(
                version=second_version,
                values=answers(
                    second_version,
                    {
                        "What course is this for?": "ENG 101",
                        "What are you writing?": "An essay or paper",
                        "What would you like to focus on?": ["Using sources", "Citations"],
                        "Anything else your tutor should know?": "Same essay as last time.",
                    },
                ),
                answered_by=sam,
                student=sam,
                appointment=sessions[-1],
            )
