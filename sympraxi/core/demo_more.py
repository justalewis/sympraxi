"""The later features' sample data for the demo center: workshops, training, onboarding,
early alerts, a shared visit, the waiting list and the time clock.

Called by `seed_demo` after the scheduling, records and forms data exist. Every date is
relative to today, so a demo reset each night always looks current. Safe to run twice:
each piece checks whether it is already there.
"""

import contextlib
import hashlib
import secrets
import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.alerts.models import Alert, AlertKind, AlertStatus, VisitShare
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.scheduling import waitlist
from sympraxi.scheduling.models import Modality, Participant, TutorAssignment
from sympraxi.scheduling.timeutils import first_instant
from sympraxi.staff.models import Category, TimeEntry
from sympraxi.training.models import (
    Choice,
    Cohort,
    CohortMember,
    Criterion,
    Item,
    ItemKind,
    Module,
    Observation,
    Question,
    Template,
)
from sympraxi.workshops.models import Attendance, Registration, Workshop, WorkshopStatus


def add_more(center: Center, assignment: TutorAssignment) -> dict[str, str]:
    """Add the sample data. Returns anything worth printing, such as the shared-visit link."""
    tz = zoneinfo.ZoneInfo(center.time_zone)
    today = timezone.localdate(timezone=tz)
    director = User.objects.get(email="director@demo.test")
    tutor = User.objects.get(email="tutor@demo.test")
    sam = User.objects.get(email="student@demo.test")
    robin = User.objects.get(email="student2@demo.test")
    printed: dict[str, str] = {}

    center.alert_email = "alerts@demo.test"
    center.visit_sharing_enabled = True
    center.clock_networks = ""
    center.save(update_fields=["alert_email", "visit_sharing_enabled", "clock_networks"])

    _workshops(center, tutor, director, sam, robin, today, tz)
    _training(center)
    _onboarding(center, tutor, today)
    _alerts(center, tutor, director, sam, robin)
    printed.update(_visit_share(center, sam))
    _waiting_list(assignment, robin, today)
    _time_clock(center, tutor, today, tz)
    return printed


def _workshops(
    center: Center,
    tutor: User,
    director: User,
    sam: User,
    robin: User,
    today: date,
    tz: Any,
) -> None:
    if Workshop.objects.filter(center=center).exists():
        return

    def at(day: date, hour: int) -> datetime:
        return first_instant(day, time(hour), tz)

    upcoming = Workshop.objects.create(
        center=center,
        title="Thesis statements that can be argued with",
        description="Bring a draft claim. We will test it against a skeptical reader.",
        location="Library 214",
        capacity=12,
        leader=tutor,
        starts_at=at(today + timedelta(days=4), 15),
        ends_at=at(today + timedelta(days=4), 16),
        status=WorkshopStatus.PUBLISHED,
        created_by=director,
    )
    Registration.objects.create(workshop=upcoming, student=sam, registered_by=sam)
    Registration.objects.create(workshop=upcoming, student=robin, registered_by=robin)
    Workshop.objects.create(
        center=center,
        title="Citing sources without panic",
        description="A short tour of how to cite, and when you need to.",
        meeting_url="https://meet.example.invalid/citing",
        capacity=None,
        leader=tutor,
        starts_at=at(today + timedelta(days=9), 13),
        ends_at=at(today + timedelta(days=9), 14),
        status=WorkshopStatus.PUBLISHED,
        created_by=director,
    )
    past = Workshop.objects.create(
        center=center,
        title="Revising for reverse outlines",
        description="Last week's workshop, with attendance recorded.",
        location="Library 214",
        capacity=10,
        leader=tutor,
        starts_at=at(today - timedelta(days=6), 14),
        ends_at=at(today - timedelta(days=6), 15),
        status=WorkshopStatus.PUBLISHED,
        created_by=director,
    )
    for student, mark in ((sam, Attendance.ATTENDED), (robin, Attendance.NO_SHOW)):
        Registration.objects.create(
            workshop=past,
            student=student,
            registered_by=student,
            registered_at=timezone.now() - timedelta(days=9),
            attendance=mark,
            marked_by=tutor,
            marked_at=timezone.now() - timedelta(days=6),
        )


def _training(center: Center) -> None:
    if Module.objects.filter(center=center).exists():
        return
    module = Module.objects.create(
        center=center,
        title="Welcome to the writing center",
        summary="Start here: what we believe and how a session goes.",
        is_published=True,
    )
    Item.objects.create(
        module=module,
        kind=ItemKind.PAGE,
        title="How a session goes",
        body="We begin by asking what the writer wants. We read together. We leave with a plan.",
        position=1,
    )
    Item.objects.create(
        module=module,
        kind=ItemKind.LINK,
        title="A reading on peer tutoring",
        url="https://example.org/peer-tutoring",
        position=2,
    )
    quiz = Item.objects.create(
        module=module, kind=ItemKind.QUIZ, title="Check yourself", position=3, pass_percent=50
    )
    for n, prompt in enumerate(
        ["Who decides what a session covers?", "What should a writer leave with?"]
    ):
        question = Question.objects.create(item=quiz, prompt=prompt, position=n)
        Choice.objects.create(
            question=question, text="The writer, with the tutor", is_correct=True, position=0
        )
        Choice.objects.create(
            question=question, text="The tutor alone", is_correct=False, position=1
        )


def _onboarding(center: Center, tutor: User, today: date) -> None:
    if Cohort.objects.filter(center=center).exists():
        return
    newbie, _created = User.objects.get_or_create(
        email="newtutor@demo.test", defaults={"full_name": "Noor New-Tutor"}
    )
    Membership.objects.get_or_create(user=newbie, center=center, role=Role.TUTOR)
    cohort = Cohort.objects.create(center=center, name="Fall tutors", starts_on=today)
    CohortMember.objects.create(cohort=cohort, person=newbie, mentor=tutor)
    template = Template.objects.create(center=center, title="Session observation")
    for n, text in enumerate(
        ["Opens warmly and asks what the writer wants", "Asks before telling", "Ends with a plan"]
    ):
        Criterion.objects.create(template=template, text=text, position=n)
    Observation.objects.create(
        center=center, template=template, tutor=newbie, observer=tutor, observed_on=today
    )


def _alerts(center: Center, tutor: User, director: User, sam: User, robin: User) -> None:
    if Alert.objects.filter(center=center).exists():
        return
    Alert.objects.create(
        center=center,
        student=robin,
        raised_by=tutor,
        kind=AlertKind.ATTENDANCE,
        reason="Booked three sessions and has missed the last two without a message.",
        student_informed=True,
    )
    Alert.objects.create(
        center=center,
        student=sam,
        raised_by=tutor,
        kind=AlertKind.ENGAGEMENT,
        reason="Said the course is going badly and they are thinking of dropping it.",
        student_informed=True,
        status=AlertStatus.CLOSED,
        acknowledged_by=director,
        acknowledged_at=timezone.now() - timedelta(days=4),
        closed_by=director,
        closed_at=timezone.now() - timedelta(days=3),
        outcome="Spoke with the student and pointed them to their advisor.",
    )


def _visit_share(center: Center, sam: User) -> dict[str, str]:
    if VisitShare.objects.filter(center=center).exists():
        return {}
    participant = (
        Participant.objects.filter(student=sam, appointment__starts_at__lt=timezone.now())
        .order_by("-appointment__starts_at")
        .first()
    )
    if participant is None:
        return {}
    token = secrets.token_urlsafe(24)
    VisitShare.objects.create(
        participant=participant,
        center=center,
        student=sam,
        instructor_email="professor@demo.test",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=30),
    )
    return {"Shared-visit link for the instructor (no sign-in)": f"/visit/{token}/"}


def _waiting_list(assignment: TutorAssignment, robin: User, today: date) -> None:
    schedule = assignment.schedule
    if not schedule.waitlist_enabled:
        schedule.waitlist_enabled = True
        schedule.save(update_fields=["waitlist_enabled"])
    from sympraxi.scheduling.models import WaitingEntry

    if WaitingEntry.objects.filter(schedule=schedule, student=robin).exists():
        return
    day = today + timedelta(days=2)
    while day.weekday() > 4:
        day += timedelta(days=1)
    # If the schedule's own rules say no, the demo goes on without a waiting entry.
    with contextlib.suppress(waitlist.WaitlistError):
        waitlist.join(
            robin,
            schedule,
            day=day,
            earliest=time(10),
            latest=time(15),
            minutes=60,
            modality=Modality.IN_PERSON,
            tutor=None,
        )


def _time_clock(center: Center, tutor: User, today: date, tz: Any) -> None:
    if TimeEntry.objects.filter(center=center).exists():
        return
    shifts = [(1, Category.TUTORING, 4), (2, Category.TUTORING, 3), (3, Category.TRAINING, 2)]
    for days_ago, category, hours in shifts:
        day = today - timedelta(days=days_ago)
        while day.weekday() > 4:
            day -= timedelta(days=1)
        start = first_instant(day, time(10), tz)
        TimeEntry.objects.create(
            center=center,
            person=tutor,
            category=category,
            started_at=start,
            ended_at=start + timedelta(hours=hours),
        )
