"""The rest of the demo center: reports, surveys, alerts, training, the time clock and more.

Runs after `history.build`, so the people and sessions exist. Each part checks whether it
has already been added, so running the seed twice doesn't double anything.
"""

import hashlib
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.alerts.models import Alert, AlertKind, AlertStatus, VisitShare
from sympraxi.centers.models import Center, Role
from sympraxi.core.demo_world import cast, library
from sympraxi.core.demo_world.history import World, answers_by_label
from sympraxi.feedback.models import QueueSettings, Submission, SubmissionStatus
from sympraxi.forms import services as form_services
from sympraxi.integrations import tokens
from sympraxi.integrations.models import ApiToken
from sympraxi.notify.models import Announcement, Audience, MassEmail
from sympraxi.reports import demographics, research
from sympraxi.scheduling import waitlist
from sympraxi.scheduling.models import (
    Appointment,
    Exemption,
    Modality,
    Participant,
    WaitingEntry,
)
from sympraxi.scheduling.timeutils import first_instant
from sympraxi.staff.models import Category, PayrollDetail, TimeEntry, TimesheetApproval
from sympraxi.training.models import (
    Attempt,
    Choice,
    Cohort,
    CohortMember,
    Completion,
    Item,
    ItemKind,
    Module,
    Observation,
    ObservationStatus,
    Question,
    Rating,
    SignOff,
    Template,
)
from sympraxi.workshops.models import Attendance, Registration, Workshop, WorkshopStatus


def add_extras(world: World) -> None:
    _settings(world)
    _survey_more(world)
    _alerts_more(world)
    _shares_more(world)
    _waiting_more(world)
    _flagged_sessions(world)
    _time_clock(world)
    _training_more(world)
    _announcements(world)
    _workshops_more(world)
    _api_token(world)
    _feedback_queue(world)
    _exemption(world)


def _settings(world: World) -> None:
    center = world.center
    center.sms_enabled = True
    center.survey_timing = center.survey_timing  # the default is fine
    center.save(update_fields=["sms_enabled"])
    keys = demographics.settings_for(center)
    if world.registration_version:
        year = world.registration_version.questions.filter(label="What year are you in?").first()
        keys.standing_key = year.key if year else None
    if world.intake_version:
        course = world.intake_version.questions.filter(label="What course is this for?").first()
        keys.course_key = course.key if course else None
    keys.save()
    protocol = research.settings_for(center)
    if not protocol.protocol_number:
        protocol.protocol_number = "IRB-2026-114"
        protocol.investigator = "Dr. Dana Director"
        protocol.approved_on = world.today - timedelta(days=60)
        protocol.expires_on = world.today + timedelta(days=300)
        protocol.save()


def _survey_more(world: World) -> None:
    version = world.survey_version
    if version is None or version.form.versions.count() == 0:
        return
    existing = version.responses.count() if hasattr(version, "responses") else 0
    if existing > 12:
        return
    rng = world.rng
    helpful = ["Very helpful"] * 7 + ["Somewhat helpful"] * 2 + ["Not very helpful"]
    helped = ["Talking it through", "The tutor's questions", "A handout or link"]
    for n in range(34):
        values: dict[str, Any] = {"How helpful was the session?": rng.choice(helpful)}
        if rng.random() < 0.7:
            values["What helped most?"] = rng.sample(helped, rng.randint(1, 2))
        comment = rng.choice(library.SURVEY_COMMENTS)
        if comment:
            values["What could we do better?"] = comment
        form_services.create_response(
            version=version,
            values=answers_by_label(version, values),
            answered_by=None,
            student=None,
            answered_on=world.today - timedelta(days=rng.randint(1, 60)),
        )
        del n


def _alerts_more(world: World) -> None:
    center = world.center
    if Alert.objects.filter(center=center).count() > 2:
        return
    now = timezone.now()
    lena, aisha = world.user("lena.fischer@demo.test"), world.user("aisha.bello@demo.test")
    dana = world.user("director@demo.test")
    rows = (
        (
            "tyler.brooks@demo.test",
            lena,
            AlertKind.WELLBEING,
            "Said they haven't slept before a deadline and feel they can't keep up. Gave the counseling center's number.",
            AlertStatus.ACKNOWLEDGED,
            2,
            "",
        ),
        (
            "liam.oconnor@demo.test",
            world.user("rosa.delgado@demo.test"),
            AlertKind.ATTENDANCE,
            "Two sessions booked and missed in two weeks, and no reply to the reminder email.",
            AlertStatus.OPEN,
            1,
            "",
        ),
        (
            "lucia.gomez@demo.test",
            aisha,
            AlertKind.ENGAGEMENT,
            "Is unsure whether the course is right for them and talked about withdrawing. Referred to their advisor.",
            AlertStatus.CLOSED,
            12,
            "The director spoke with the student and the advisor met them on Friday.",
        ),
    )
    for email, by, kind, reason, status, days_ago, outcome in rows:
        created = now - timedelta(days=days_ago)
        fields: dict[str, Any] = {}
        if status != AlertStatus.OPEN:
            fields.update(acknowledged_by=dana, acknowledged_at=created + timedelta(hours=5))
        if status == AlertStatus.CLOSED:
            fields.update(closed_by=dana, closed_at=created + timedelta(days=2), outcome=outcome)
        alert = Alert.objects.create(
            center=center,
            student=world.user(email),
            raised_by=by,
            kind=kind,
            reason=reason,
            student_informed=True,
            status=status,
            **fields,
        )
        Alert.objects.filter(pk=alert.pk).update(created_at=created)


def _shares_more(world: World) -> None:
    center = world.center
    if VisitShare.objects.filter(center=center).count() > 1:
        return
    rows = (
        ("hannah.berg@demo.test", "dr.osei@demo.test", "EDU 305", None, 20),
        ("diego.martinez@demo.test", "prof.okafor@demo.test", "ENG 101", None, 20),
        ("noah.kim@demo.test", "dr.tran@demo.test", "CHEM 310", "revoked", 20),
        ("amara.okafor@demo.test", "dr.ferreira@demo.test", "BIO 301", "expired", -3),
    )
    for email, instructor, course, state, days in rows:
        student = world.user(email)
        participant = (
            Participant.objects.filter(student=student, appointment__starts_at__lt=timezone.now())
            .order_by("-appointment__starts_at")
            .first()
        )
        if participant is None:
            continue
        share = VisitShare.objects.create(
            participant=participant,
            center=center,
            student=student,
            instructor_email=instructor,
            course=course,
            token_hash=hashlib.sha256(secrets.token_urlsafe(24).encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(days=days),
        )
        if state == "revoked":
            VisitShare.objects.filter(pk=share.pk).update(
                revoked_at=timezone.now() - timedelta(days=1)
            )
        elif state is None:
            VisitShare.objects.filter(pk=share.pk).update(
                opened_count=2, last_opened_at=timezone.now() - timedelta(days=2)
            )


def _waiting_more(world: World) -> None:
    if WaitingEntry.objects.count() > 1:
        return
    schedule = world.assignments["tutor@demo.test"].schedule
    schedule.refresh_from_db()  # the waiting list was switched on after this object was read
    day = world.today + timedelta(days=3)
    while day.weekday() > 4:
        day += timedelta(days=1)
    for email, early, late, minutes in (
        ("tyler.brooks@demo.test", time(11), time(14), 30),
        ("diego.martinez@demo.test", time(12), time(16), 60),
    ):
        try:
            waitlist.join(
                world.user(email),
                schedule,
                day=day,
                earliest=early,
                latest=late,
                minutes=minutes,
                modality=Modality.IN_PERSON,
                tutor=None,
            )
        except waitlist.WaitlistError:
            continue


def _flagged_sessions(world: World) -> None:
    """Sessions from the last few days where nobody marked attendance and the flag is up,
    so the front desk's "Attendance to check" has real work in it."""
    from sympraxi.core.demo_world import history

    center = world.center
    if (
        Participant.objects.filter(
            appointment__schedule__center=center,
            flagged_at__isnull=False,
            flag_cleared_at__isnull=True,
        ).count()
        > 2
    ):
        return
    for email, days_ago, key in (
        ("tyler.brooks@demo.test", 1, "getting_started"),
        ("liam.oconnor@demo.test", 2, "assignment"),
        ("lucia.gomez@demo.test", 3, "paragraphs"),
    ):
        day = world.today - timedelta(days=days_ago)
        while day.weekday() > 4:
            day -= timedelta(days=1)
        tutor_email = history.tutor_for(world, key, day)
        if tutor_email is None:
            continue
        slot = history.find_slot(world, tutor_email, day, 30)
        if slot is None:
            continue
        assignment = world.assignments[tutor_email]
        appointment = Appointment.objects.create(
            schedule=assignment.schedule,
            assignment=assignment,
            tutor=assignment.tutor,
            starts_at=slot[0],
            ends_at=slot[1],
            blocked_until=slot[1],
            modality=Modality.IN_PERSON,
            room=assignment.room,
        )
        participant = Participant.objects.create(appointment=appointment, student=world.user(email))
        Participant.objects.filter(pk=participant.pk).update(
            flagged_at=slot[0] + timedelta(minutes=20)
        )


def _time_clock(world: World) -> None:
    center = world.center
    if TimeEntry.objects.filter(center=center).count() > 10:
        return
    rng = world.rng
    people = [(e, Category.TUTORING) for e in cast.TUTOR_HOURS if e != "tutor@demo.test"] + [
        ("tutor@demo.test", Category.TUTORING)
    ]
    TimeEntry.objects.filter(center=center).delete()
    for email, category in people:
        person = world.user(email)
        _kind, hours = cast.TUTOR_HOURS[email]
        for n in range(1, 64):
            day = world.today - timedelta(days=n)
            window = hours.get(day.weekday())
            if window is None or rng.random() < 0.06:
                continue
            start = first_instant(day, time(window[0]), world.tz) - timedelta(
                minutes=rng.randint(0, 8)
            )
            end = first_instant(day, time(window[1]), world.tz) + timedelta(
                minutes=rng.randint(0, 10)
            )
            TimeEntry.objects.create(
                center=center, person=person, category=category, started_at=start, ended_at=end
            )
            if rng.random() < 0.12:
                meet = first_instant(day, time(window[1]), world.tz) + timedelta(minutes=15)
                TimeEntry.objects.create(
                    center=center,
                    person=person,
                    category=Category.MEETING,
                    started_at=meet,
                    ended_at=meet + timedelta(minutes=45),
                    note="Staff meeting",
                )
    for email, start_hour in (("frontdesk@demo.test", 9), ("rosa.delgado@demo.test", 14)):
        person = world.user(email)
        for n in range(1, 64):
            day = world.today - timedelta(days=n)
            if day.weekday() > 4 or rng.random() < 0.05:
                continue
            start = first_instant(day, time(start_hour), world.tz)
            TimeEntry.objects.create(
                center=center,
                person=person,
                category=Category.OFFICE,
                started_at=start,
                ended_at=start + timedelta(hours=5, minutes=rng.randint(0, 30)),
            )
    # Pay periods before the last two weeks are approved; the recent ones wait for a director.
    dana = world.user("director@demo.test")
    cutoff = world.today - timedelta(days=14)
    first = world.today - timedelta(days=63)
    TimesheetApproval.objects.filter(center=center).delete()
    for email in (*cast.TUTOR_HOURS, "frontdesk@demo.test", "rosa.delgado@demo.test"):
        person = world.user(email)
        period_start = first
        while period_start + timedelta(days=13) < cutoff:
            period_end = period_start + timedelta(days=13)
            total = sum(
                (
                    (e.ended_at - e.started_at).total_seconds()
                    for e in TimeEntry.objects.filter(
                        center=center,
                        person=person,
                        started_at__gte=first_instant(period_start, time(0), world.tz),
                        started_at__lt=first_instant(
                            period_end + timedelta(days=1), time(0), world.tz
                        ),
                    )
                    if e.ended_at
                ),
                0.0,
            )
            TimesheetApproval.objects.create(
                center=center,
                person=person,
                first_day=period_start,
                last_day=period_end,
                hours=Decimal(str(round(total / 3600, 2))),
                approved_by=dana,
                approved_at=first_instant(period_end + timedelta(days=2), time(10), world.tz),
            )
            period_start = period_end + timedelta(days=1)
        PayrollDetail.objects.get_or_create(
            center=center,
            person=person,
            defaults={"payroll_id": f"E{abs(hash(email)) % 90000 + 10000}", "pay_code": "PEER"},
        )
    # One tutor is clocked in right now, so the time clock shows a live shift.
    clocked = world.user("tutor@demo.test")
    now = timezone.now()
    if not TimeEntry.objects.filter(person=clocked, ended_at__isnull=True).exists():
        TimeEntry.objects.create(
            center=center,
            person=clocked,
            category=Category.TUTORING,
            started_at=now - timedelta(minutes=40),
        )


def _module(
    center: Center, title: str, summary: str, items: list[dict[str, Any]], position: int
) -> Module:
    module, created = Module.objects.get_or_create(
        center=center,
        title=title,
        defaults={"summary": summary, "is_published": True, "position": position},
    )
    if created:
        for n, spec in enumerate(items, 1):
            quiz = spec.pop("quiz", None)
            item = Item.objects.create(module=module, position=n, **spec)
            for q, prompt in enumerate(quiz or []):
                question = Question.objects.create(item=item, prompt=prompt[0], position=q)
                for c, (text, ok) in enumerate(prompt[1]):
                    Choice.objects.create(question=question, text=text, is_correct=ok, position=c)
    return module


def _training_more(world: World) -> None:
    center = world.center
    dana = world.user("director@demo.test")
    modules = [
        Module.objects.filter(center=center).order_by("position", "created_at").first(),
        _module(
            center,
            "Working with multilingual writers",
            "How to support writers who are learning English without rewriting their work.",
            [
                {
                    "kind": ItemKind.PAGE,
                    "title": "What helps, and what doesn't",
                    "body": "Ask what the writer wants. Mark patterns, not every error. Let the writer make the changes.",
                    "minutes": 10,
                },
                {
                    "kind": ItemKind.LINK,
                    "title": "OpenStax: Spotlight on multilingual writers",
                    "url": "https://openstax.org/books/writing-guide/pages/4-8-spotlight-on-multilingual-writers",
                    "minutes": 15,
                },
                {
                    "kind": ItemKind.QUIZ,
                    "title": "Check yourself",
                    "pass_percent": 60,
                    "quiz": [
                        (
                            "What should a tutor do first?",
                            [
                                ("Ask what the writer wants to work on", True),
                                ("Fix every error", False),
                            ],
                        ),
                        (
                            "How should errors be handled?",
                            [
                                ("Mark patterns, one at a time", True),
                                ("Rewrite the paragraph", False),
                            ],
                        ),
                    ],
                },
            ],
            2,
        ),
        _module(
            center,
            "When a writer is struggling",
            "Recognizing distress, referring well, and raising an early alert.",
            [
                {
                    "kind": ItemKind.PAGE,
                    "title": "Listening first",
                    "body": "You are not a counselor. Listen, say what you can do, and share the right resource.",
                    "minutes": 8,
                },
                {
                    "kind": ItemKind.QUIZ,
                    "title": "Check yourself",
                    "pass_percent": 50,
                    "quiz": [
                        (
                            "A writer says they haven't slept. What do you do?",
                            [
                                ("Listen, offer resources, and tell the director if needed", True),
                                ("Ignore it", False),
                            ],
                        )
                    ],
                },
            ],
            3,
        ),
    ]
    tutors = [e for e in cast.TUTOR_HOURS]
    for idx, email in enumerate(tutors):
        person = world.user(email)
        for module in modules:
            if module is None:
                continue
            for item in module.items.all():
                if (
                    world.rng.random() < (0.95 if idx < 3 else 0.5)
                    and not Completion.objects.filter(item=item, person=person).exists()
                ):
                    if item.kind == ItemKind.QUIZ:
                        Attempt.objects.create(
                            item=item, person=person, correct=2, total=2, passed=True, answers={}
                        )
                    else:
                        Completion.objects.create(item=item, person=person)
    cohort = Cohort.objects.filter(center=center).first()
    if cohort and cohort.members.count() < 3:
        for name, email, mentor in (
            ("Ben Alvarez", "ben.alvarez@demo.test", "aisha.bello@demo.test"),
            ("Chloe Dubois", "chloe.dubois@demo.test", "marcus.chen@demo.test"),
        ):
            user, created = User.objects.get_or_create(email=email, defaults={"full_name": name})
            if created:
                user.set_unusable_password()
                user.save()
            from sympraxi.centers.models import Membership

            Membership.objects.get_or_create(user=user, center=center, role=Role.TUTOR)
            CohortMember.objects.get_or_create(
                cohort=cohort, person=user, defaults={"mentor": world.user(mentor)}
            )
    if cohort:
        first = cohort.members.order_by("id").first()
        if first and modules[0] and not SignOff.objects.filter(member=first).exists():
            SignOff.objects.create(
                member=first, module=modules[0], signed_by=dana, note="Completed with her mentor."
            )
        template = Template.objects.filter(center=center).first()
        if (
            template
            and Observation.objects.filter(
                center=center, status=ObservationStatus.SUBMITTED
            ).count()
            == 0
        ):
            member = cohort.members.first()
            if member is None:
                return
            newbie = member.person
            observation = Observation.objects.create(
                center=center,
                template=template,
                tutor=newbie,
                observer=world.user("tutor@demo.test"),
                observed_on=world.today - timedelta(days=6),
                notes="Warm opening and good questions. Next: end each session with a plan the writer states.",
            )
            for criterion in template.criteria.all():
                Rating.objects.create(
                    observation=observation,
                    criterion=criterion,
                    score=world.rng.choice([3, 4, 4]),
                    comment=world.rng.choice(
                        ["Clear example.", "", "Try this again with a longer session."]
                    ),
                )
            # Submitted last: the database refuses changes to the ratings after that.
            Observation.objects.filter(pk=observation.pk).update(
                status=ObservationStatus.SUBMITTED,
                submitted_at=timezone.now() - timedelta(days=5),
            )


def _announcements(world: World) -> None:
    center = world.center
    if Announcement.objects.filter(center=center).exists():
        return
    dana = world.user("director@demo.test")
    today = world.today
    for n, (title, body) in enumerate(library.ANNOUNCEMENTS):
        Announcement.objects.create(
            center=center,
            title=title,
            body=body,
            audience=Audience.EVERYONE,
            starts_on=today - timedelta(days=10 - n * 4),
            ends_on=today + timedelta(days=21),
            created_by=dana,
        )
    Announcement.objects.create(
        center=center,
        title="Staff: timesheets are due Friday",
        body="Please clock out at the end of every shift. Directors approve timesheets on Friday afternoon.",
        audience=Audience.STAFF,
        starts_on=today - timedelta(days=2),
        ends_on=today + timedelta(days=7),
        created_by=dana,
    )
    MassEmail.objects.create(
        center=center,
        subject="Fall writing center hours",
        body="We are open Monday to Friday, with online evenings. Book at the center page.",
        audience=Audience.STUDENTS,
        sent_by=dana,
        skipped=0,
    )


def _workshops_more(world: World) -> None:
    center = world.center
    if Workshop.objects.filter(center=center).count() > 3:
        return
    rng = world.rng
    leader = world.user("aisha.bello@demo.test")
    dana = world.user("director@demo.test")
    plan = (
        (
            "Writing your personal statement",
            "A hands-on session for application essays.",
            -20,
            "Library 214",
            12,
        ),
        (
            "APA in an hour",
            "A tour of in-text citations and the reference list.",
            -13,
            "Studio B",
            25,
        ),
        ("Reading like a writer", "Annotating a text to find what it does.", 16, "Library 214", 15),
        (
            "Writing a literature review",
            "Build a map of the conversation before you write.",
            21,
            "Studio B",
            20,
        ),
    )
    students = [s for s in cast.STUDENTS if s.visits]
    for title, description, offset, place, seats in plan:
        day = world.today + timedelta(days=offset)
        start = first_instant(day, time(15), world.tz)
        workshop = Workshop.objects.create(
            center=center,
            title=title,
            description=description,
            location=place,
            capacity=seats,
            leader=leader,
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            status=WorkshopStatus.PUBLISHED,
            created_by=dana,
        )
        # Sam registers on camera for the later ones, so he isn't already on their lists.
        pool = [s for s in students if offset < 0 or s.email != "student@demo.test"]
        for profile in rng.sample(pool, min(len(pool), rng.randint(5, 10))):
            mark = ""
            if offset < 0:
                mark = Attendance.ATTENDED if rng.random() < 0.8 else Attendance.NO_SHOW
            Registration.objects.create(
                workshop=workshop,
                student=world.user(profile.email),
                registered_by=world.user(profile.email),
                registered_at=start - timedelta(days=rng.randint(3, 14)),
                attendance=mark,
                marked_by=leader if mark else None,
                marked_at=start + timedelta(hours=1) if mark else None,
            )


def _api_token(world: World) -> None:
    if ApiToken.objects.filter(center=world.center).exists():
        return
    tokens.create(
        world.center,
        world.user("director@demo.test"),
        name="Weekly reporting dashboard",
        scopes=["schedules:read", "appointments:read", "workshops:read"],
        days=90,
    )


def _feedback_queue(world: World) -> None:
    center = world.center
    QueueSettings.objects.update_or_create(center=center, defaults={"is_open": True})
    if Submission.objects.filter(center=center).exists():
        return
    now = timezone.now()
    rows = (
        (
            "kofi.mensah@demo.test",
            "Statement of purpose, draft 2",
            "ECON 499",
            "Is my opening too general?",
            SubmissionStatus.WAITING,
            1,
        ),
        (
            "priya.raman@demo.test",
            "Literature review, sections 1 to 2",
            "PSY 330",
            "Organization and synthesis.",
            SubmissionStatus.WAITING,
            2,
        ),
        (
            "omar.farouk@demo.test",
            "Journal article, introduction",
            "ME 780",
            "Clarity and article use.",
            SubmissionStatus.CLAIMED,
            3,
        ),
        (
            "hannah.berg@demo.test",
            "Teaching philosophy",
            "EDU 305",
            "Flow and tone.",
            SubmissionStatus.RETURNED,
            8,
        ),
    )
    for email, title, course, wanted, status, days_ago in rows:
        fields: dict[str, Any] = {}
        if status != SubmissionStatus.WAITING:
            fields.update(
                tutor=world.user("aisha.bello@demo.test"),
                claimed_at=now - timedelta(days=days_ago - 1),
            )
        if status == SubmissionStatus.RETURNED:
            fields["returned_at"] = now - timedelta(days=days_ago - 3)
        submission = Submission.objects.create(
            center=center,
            student=world.user(email),
            title=title,
            course=course,
            wanted=wanted,
            due_at=now + timedelta(days=4),
            status=status,
            **fields,
        )
        Submission.objects.filter(pk=submission.pk).update(
            created_at=now - timedelta(days=days_ago)
        )


def _exemption(world: World) -> None:
    if Exemption.objects.filter(center=world.center).exists():
        return
    Exemption.objects.create(
        center=world.center,
        student=world.user("sofia.rossi@demo.test"),
        schedule=world.assignments["aisha.bello@demo.test"].schedule,
        expires_on=world.today + timedelta(days=60),
        note="Doctoral student on a dissertation timeline: no weekly limit this term.",
        created_by=world.user("assistant.director@demo.test"),
    )


_unused: tuple[Any, ...] = (date, datetime)
