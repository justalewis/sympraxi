"""People, schedules and about ten weeks of sessions for the demo center.

Everything is derived from today's date, so a nightly reset always shows a living
center: recent sessions with records, yesterday's unwritten ones, upcoming bookings.
A fixed random seed keeps the data the same shape every night.
"""

import random
import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.demo_world import cast, library
from sympraxi.forms import services as form_services
from sympraxi.forms.models import Form, FormKind, FormVersion
from sympraxi.forms.report import ReportAnswers
from sympraxi.records import services as records
from sympraxi.records.models import Goal, GoalStatus, Resource, Topic, WritingProject
from sympraxi.scheduling import attendance, services
from sympraxi.scheduling.models import (
    Appointment,
    Attendance,
    Closure,
    Modality,
    Participant,
    Room,
    Schedule,
    Term,
    TutorAssignment,
    WeeklyHours,
)
from sympraxi.scheduling.timeutils import first_instant

HISTORY_DAYS = 70
# Visits per student are scaled so the center reads as busy, as a real one is.
VISIT_FACTOR = 1.8
SPECIALTY = {  # which tutor a kind of session tends to go to (emails)
    "lab_report": ["marcus.chen@demo.test"],
    "personal_statement": ["lena.fischer@demo.test"],
    "creative": ["lena.fischer@demo.test"],
    "reflection": ["lena.fischer@demo.test", "tutor@demo.test"],
    "lit_review": ["aisha.bello@demo.test"],
    "dissertation": ["aisha.bello@demo.test"],
    "research": ["aisha.bello@demo.test", "tutor@demo.test"],
    "citations_apa": ["aisha.bello@demo.test", "tutor@demo.test"],
    "annotated_bib": ["aisha.bello@demo.test"],
    "articles": ["tutor@demo.test", "jordan.reyes@demo.test"],
    "grammar_boundaries": ["tutor@demo.test", "jordan.reyes@demo.test"],
    "getting_started": ["jordan.reyes@demo.test", "tutor@demo.test"],
    "anxiety": ["jordan.reyes@demo.test", "lena.fischer@demo.test"],
}
YEAR_OPTION = {"Fourth year": "Fourth year or later"}
STYLE_PATTERNS = (
    "Run-ons and comma splices",
    "Articles and prepositions",
    "Wordiness and filler phrases",
    "Verb tense shifts",
)


def answers_by_label(version: FormVersion, by_label: dict[str, Any]) -> dict[Any, Any]:
    """Answers by question label (and option labels for choices), as the services take them."""
    found: dict[Any, Any] = {}
    for q in version.ordered_questions():
        if q.label in by_label:
            value = by_label[q.label]
            if q.is_choice:
                labels = {o.label: o.key for o in q.options.all()}
                value = [labels[v] for v in ([value] if isinstance(value, str) else value)]
            found[q.key] = value
    return found


class World:
    """The shared state while building: the center, today, a random source and caches."""

    def __init__(self, center: Center) -> None:
        self.center = center
        self.tz = zoneinfo.ZoneInfo(center.time_zone)
        self.today: date = timezone.localdate(timezone=self.tz)
        self.rng = random.Random(20261022)  # noqa: S311 (sample data, not secrets)
        self.users: dict[str, User] = {}
        self.projects: dict[str, WritingProject] = {}
        self.assignments: dict[str, TutorAssignment] = {}
        self.topics = {t.slug: t for t in Topic.objects.all()}
        self.resources = {r.catalog_id: r for r in Resource.objects.exclude(catalog_id="")}
        self.report_version = form_services.report_version(center)
        self.survey_version: FormVersion | None = None
        self.intake_version: FormVersion | None = None
        self.registration_version: FormVersion | None = None

    def at(self, day: date, hour: int, minute: int = 0) -> datetime:
        return first_instant(day, time(hour, minute), self.tz)

    def user(self, email: str) -> User:
        return self.users[email]


def build(center: Center) -> World:
    world = World(center)
    _people(world)
    _forms_cache(world)
    _schedules(world)
    _projects(world)
    _goals(world)
    _registrations(world)
    _past_sessions(world)
    _future_sessions(world)
    return world


# --- People ----------------------------------------------------------------------------------


def _person(email: str, name: str, student_id: str = "") -> User:
    user, created = User.objects.get_or_create(email=email, defaults={"full_name": name})
    if created:
        user.set_unusable_password()  # Visitors come in through the picker, not a password.
        if student_id:
            user.student_id = student_id
        user.save()
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    elif student_id and not user.student_id:
        user.student_id = student_id
        user.save(update_fields=["student_id"])
    return user


def _people(world: World) -> None:
    for person in cast.STAFF:
        user = _person(person.email, person.name)
        Membership.objects.get_or_create(user=user, center=world.center, role=person.role)
        world.users[person.email] = user
    for student in cast.STUDENTS:
        user = _person(student.email, student.name, student.student_id)
        Membership.objects.get_or_create(user=user, center=world.center, role=Role.STUDENT)
        world.users[student.email] = user


def _forms_cache(world: World) -> None:
    def latest(kind: str) -> FormVersion | None:
        form = Form.objects.filter(center=world.center, kind=kind).first()
        if form is None:
            return None
        return form.versions.filter(published_at__isnull=False).order_by("-number").first()

    world.registration_version = latest(FormKind.REGISTRATION)
    world.intake_version = latest(FormKind.INTAKE)
    world.survey_version = latest(FormKind.SURVEY)
    world.report_version = form_services.report_version(world.center)


# --- Schedules --------------------------------------------------------------------------------


def _schedules(world: World) -> None:
    center, today = world.center, world.today
    term = Term.objects.filter(center=center).order_by("starts_on").first()
    if term is None:
        raise RuntimeError("seed the scheduling data first")
    term.name = "Fall term"
    term.starts_on = today - timedelta(days=HISTORY_DAYS)
    term.ends_on = today + timedelta(days=60)
    term.save()
    Closure.objects.get_or_create(
        term=term,
        label="Holiday closure",
        defaults={"starts_on": today + timedelta(days=24), "ends_on": today + timedelta(days=26)},
    )
    rooms = [
        Room.objects.get_or_create(center=center, name=name, defaults={"details": details})[0]
        for name, details in (
            ("Library 214", "The main tutoring room, with six tables."),
            ("Library 216", "A quiet room for longer consultations."),
            ("Studio B", "Whiteboards and a screen, for group sessions."),
        )
    ]
    dropin = Schedule.objects.get(center=center, name="Drop-in tutoring")
    dropin.term = term
    dropin.starts_on = term.starts_on
    dropin.ends_on = term.ends_on
    dropin.horizon_days = 21
    dropin.description = "Walk in or book a half hour to an hour with a peer tutor."
    dropin.save()
    consult, _ = Schedule.objects.get_or_create(
        center=center,
        name="Writing consultations",
        defaults={
            "term": term,
            "starts_on": term.starts_on,
            "ends_on": term.ends_on,
            "slot_minutes": 60,
            "max_per_week": 1,
            "horizon_days": 28,
            "late_cancel_minutes": 240,
            "description": "An hour with a graduate tutor, for longer projects. "
            "The center confirms each one.",
        },
    )
    online, _ = Schedule.objects.get_or_create(
        center=center,
        name="Online evenings",
        defaults={
            "term": term,
            "starts_on": term.starts_on,
            "ends_on": term.ends_on,
            "slot_minutes": 30,
            "max_per_week": 2,
            "horizon_days": 14,
            "late_cancel_minutes": 120,
            "description": "Video sessions in the evening, for students who can't come in.",
        },
    )
    for schedule, opens, closes in ((dropin, 9, 17), (consult, 9, 18), (online, 17, 22)):
        schedule.opens_at, schedule.closes_at = time(opens), time(closes)
        schedule.save(update_fields=["opens_at", "closes_at"])
    by_name = {"dropin": dropin, "consult": consult, "online": online}
    for email, (kind, hours) in cast.TUTOR_HOURS.items():
        tutor = world.user(email)
        schedule = by_name[kind]
        assignment, created = TutorAssignment.objects.get_or_create(
            schedule=schedule,
            tutor=tutor,
            defaults={
                "offers_online": kind == "online" or email == "tutor@demo.test",
                "offers_in_person": kind != "online",
                "meeting_url": "https://meet.example.invalid/" + email.split("@")[0],
                "needs_confirmation": kind == "consult",
                "room": rooms[1] if kind == "consult" else rooms[0],
                "min_minutes": 60 if kind == "consult" else 30,
                "max_minutes": 60,
            },
        )
        world.assignments[email] = assignment
        if email == "tutor@demo.test":
            # The first seed gave this tutor hours of its own; the cast's hours replace them.
            WeeklyHours.objects.filter(assignment=assignment).delete()
        if created or email == "tutor@demo.test":
            for weekday, (start, end) in hours.items():
                WeeklyHours.objects.create(
                    assignment=assignment, weekday=weekday, starts_at=time(start), ends_at=time(end)
                )
    world.assignments["tutor@demo.test"] = TutorAssignment.objects.get(
        schedule=dropin, tutor=world.user("tutor@demo.test")
    )


# --- Projects and registration answers ---------------------------------------------


def _projects(world: World) -> None:
    for profile in cast.STUDENTS:
        student = world.user(profile.email)
        existing = WritingProject.objects.filter(center=world.center, student=student).first()
        if existing is None:
            existing = records.new_project(
                actor=world.user("tutor@demo.test"),
                center=world.center,
                student=student,
                title=profile.project,
                course=profile.course,
                instructor=profile.instructor,
                due_on=world.today + timedelta(days=world.rng.randint(5, 25)),
            )
        world.projects[profile.email] = existing


def _goals(world: World) -> None:
    """One or two goals per student, so a tutor's brief before a session has something in it."""
    tutor = world.user("tutor@demo.test")
    for profile in cast.STUDENTS:
        student = world.user(profile.email)
        if Goal.objects.filter(center=world.center, student=student).exists():
            continue
        for n, key in enumerate(profile.wants[:2]):
            text = library.GOALS.get(key)
            if not text:
                continue
            goal = Goal.objects.create(
                center=world.center,
                student=student,
                text=text,
                target_on=world.today + timedelta(days=world.rng.randint(14, 45)),
                project=world.projects[profile.email],
                created_by=student if n == 0 else tutor,
            )
            if n == 1 and world.rng.random() < 0.3:
                Goal.objects.filter(pk=goal.pk).update(
                    status=GoalStatus.MET, closed_at=timezone.now(), closed_by=tutor
                )


def _registrations(world: World) -> None:
    version = world.registration_version
    if version is None:
        return
    for profile in cast.STUDENTS:
        student = world.user(profile.email)
        if student.email == "student@demo.test":
            continue  # Sam's answers come from the first seed.
        form_services.create_response(
            version=version,
            values=answers_by_label(
                version,
                {
                    "What is your major or program?": profile.major,
                    "What year are you in?": YEAR_OPTION.get(profile.year, profile.year),
                    "Is English your first language?": "No" if profile.multilingual else "Yes",
                    "Are you the first in your family to go to college?": "Yes"
                    if profile.first_gen
                    else "No",
                },
            ),
            answered_by=student,
            student=student,
        )


# --- Sessions ----------------------------------------------------------------------------------


def _free(world: World, tutor: User, start: datetime, end: datetime) -> bool:
    return not Appointment.objects.filter(
        tutor=tutor, starts_at__lt=end, blocked_until__gt=start
    ).exists()


def find_slot(
    world: World, email: str, day: date, minutes: int
) -> tuple[datetime, datetime] | None:
    _kind, hours = cast.TUTOR_HOURS[email]
    window = hours.get(day.weekday())
    if window is None:
        return None
    tutor = world.user(email)
    starts = [
        world.at(day, window[0], 0) + timedelta(minutes=m)
        for m in range(0, (window[1] - window[0]) * 60 - minutes + 1, 15)
    ]
    world.rng.shuffle(starts)
    for start in starts:
        end = start + timedelta(minutes=minutes)
        if _free(world, tutor, start, end):
            return start, end
    return None


def tutor_for(world: World, template_key: str, day: date) -> str | None:
    preferred = [
        e for e in SPECIALTY.get(template_key, []) if day.weekday() in cast.TUTOR_HOURS[e][1]
    ]
    pool = preferred or [e for e, (_, hours) in cast.TUTOR_HOURS.items() if day.weekday() in hours]
    return world.rng.choice(pool) if pool else None


def _length(world: World, email: str) -> int:
    kind = cast.TUTOR_HOURS[email][0]
    if kind == "consult":
        return 60
    if kind == "online":
        return world.rng.choice([30, 45])
    return world.rng.choice([30, 45, 45, 60])


def _content(
    world: World, key: str, profile: cast.StudentProfile, project: WritingProject
) -> records.Content:
    template = library.TEMPLATES[key]
    rng = world.rng
    topics = [
        world.topics[s]
        for s in (*template.concerns, template.stage, *template.actions)
        if s in world.topics
    ]
    resources = [
        (world.resources[cid], note) for cid, note in template.resources if cid in world.resources
    ]
    by_label: dict[str, Any] = {
        "Did the student bring a draft?": library.BROUGHT_DRAFT.get(key, "Yes"),
        "What did you mostly work on?": template.focus,
    }
    if template.focus == library.STYLE:
        by_label["Which sentence patterns?"] = rng.choice(STYLE_PATTERNS)
    note = rng.choice(library.NEXT_TUTOR_NOTES)
    if note:
        by_label["What should the next tutor know?"] = note
    answers = (
        ReportAnswers(
            version=world.report_version, values=answers_by_label(world.report_version, by_label)
        )
        if world.report_version
        else None
    )
    return records.Content(
        minutes=rng.choice([30, 45, 45, 60]),
        summary=rng.choice(template.summaries),
        next_steps=rng.choice(template.next_steps),
        staff_notes=rng.choice(template.staff_notes),
        topics=topics,
        project=project,
        resources=resources,
        answers=answers,
    )


def _past_sessions(world: World) -> None:
    if (
        Appointment.objects.filter(
            schedule__center=world.center, starts_at__lt=timezone.now()
        ).count()
        > 40
    ):
        return
    rng, today = world.rng, world.today
    desk = world.user("frontdesk@demo.test")
    days = [
        today - timedelta(days=n)
        for n in range(1, HISTORY_DAYS + 1)
        if (today - timedelta(days=n)).weekday() < 5
    ]
    misses_left = {p.email: 2 for p in cast.STUDENTS}
    for profile in cast.STUDENTS:
        if profile.visits == 0:
            continue
        student = world.user(profile.email)
        project = world.projects[profile.email]
        # Spread this student's visits over the term, regulars weekly, others sparsely.
        wanted = round(profile.visits * VISIT_FACTOR)
        picks = sorted(rng.sample(days, min(wanted, len(days))))
        for n, day in enumerate(picks):
            key = profile.wants[n % len(profile.wants)]
            for _attempt in range(6):
                email = tutor_for(world, key, day)
                if email is None:
                    break
                minutes = _length(world, email)
                slot = find_slot(world, email, day, minutes)
                if slot:
                    break
                day = rng.choice(days)
            else:
                continue
            if email is None or not slot:
                continue
            _one_session(world, profile, student, project, email, slot, key, desk, misses_left)


def _one_session(
    world: World,
    profile: cast.StudentProfile,
    student: User,
    project: WritingProject,
    email: str,
    slot: tuple[datetime, datetime],
    key: str,
    desk: User,
    misses_left: dict[str, int],
) -> None:
    rng = world.rng
    assignment = world.assignments[email]
    start, end = slot
    kind = cast.TUTOR_HOURS[email][0]
    online = kind == "online" or (email == "tutor@demo.test" and rng.random() < 0.15)
    appointment = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=end,
        blocked_until=end,
        modality=Modality.ONLINE if online else Modality.IN_PERSON,
        meeting_url=assignment.meeting_url if online else "",
        room=None if online else assignment.room,
    )
    participant = Participant.objects.create(appointment=appointment, student=student)
    Participant.objects.filter(pk=participant.pk).update(
        added_at=start - timedelta(days=rng.randint(1, 6), hours=rng.randint(0, 20))
    )
    roll = rng.randint(1, 100)
    if roll <= 5:
        services.cancel(
            actor=student,
            appointment=appointment,
            as_staff=False,
            now=start - timedelta(hours=rng.choice([3, 24, 30])),
            notify=False,
        )
        return
    if roll <= 5 + profile.misses and misses_left.get(student.email, 0) > 0:
        misses_left[student.email] -= 1
        attendance.mark(
            actor=desk,
            appointment=appointment,
            marks={student.pk: Attendance.NO_SHOW},
            now=start + timedelta(minutes=20),
            notify=False,
        )
        return
    # Yesterday's sessions are left for the tutors to write up.
    if (world.today - start.astimezone(world.tz).date()).days <= 1:
        _intake(world, student, appointment, key, profile)
        return
    _intake(world, student, appointment, key, profile)
    tutor = assignment.tutor
    rec = records.start_for_appointment(actor=tutor, appointment=appointment, student=student)
    records.finalize(
        actor=tutor,
        rec=rec,
        content=_content(world, key, profile, project),
        now=end + timedelta(minutes=rng.randint(5, 240)),
        notify=False,
    )


def _intake(
    world: World,
    student: User,
    appointment: Appointment,
    key: str,
    profile: cast.StudentProfile,
    *,
    always: bool = False,
) -> None:
    version = world.intake_version
    if version is None or (not always and world.rng.random() > 0.65):
        return
    template = library.TEMPLATES[key]
    kinds = {
        library.IDEAS: "Ideas and argument",
        library.ORG: "Organization",
        library.SOURCES: "Using sources",
        library.STYLE: "Citations",
    }
    by_label: dict[str, Any] = {
        "What course is this for?": profile.course,
        "What would you like to focus on?": [kinds[template.focus]],
        "Anything else your tutor should know?": world.rng.choice(
            (
                "",
                "",
                "I have a hard deadline on Friday.",
                "English is my second language.",
                "I'd like to read it aloud.",
            )
        ),
    }
    by_label["What are you writing?"] = "An essay or paper"
    by_label["What are you working on?"] = "An essay or paper"
    form_services.create_response(
        version=version,
        values=answers_by_label(version, {k: v for k, v in by_label.items() if v}),
        answered_by=student,
        student=student,
        appointment=appointment,
    )


# --- Upcoming sessions -------------------------------------------------------------


def _future_sessions(world: World) -> None:
    if (
        Appointment.objects.filter(
            schedule__center=world.center, starts_at__gt=timezone.now()
        ).count()
        > 12
    ):
        return
    rng, today = world.rng, world.today
    days = [
        today + timedelta(days=n) for n in range(0, 14) if (today + timedelta(days=n)).weekday() < 5
    ]
    desk = world.user("frontdesk@demo.test")
    del desk
    sam = cast.STUDENT_BY_EMAIL["student@demo.test"]
    booked = 0
    chosen: list[cast.StudentProfile] = [sam, sam]
    others = [s for s in cast.STUDENTS if s.visits and s.email != sam.email]
    chosen += rng.sample(others, min(len(others), 20))
    for profile in chosen:
        key = rng.choice(profile.wants)
        for _attempt in range(8):
            day = rng.choice(days)
            email = tutor_for(world, key, day)
            if email is None:
                continue
            minutes = _length(world, email)
            slot = find_slot(world, email, day, minutes)
            if slot and slot[0] > timezone.now() + timedelta(hours=2):
                break
        else:
            continue
        assignment = world.assignments[email]
        kind = cast.TUTOR_HOURS[email][0]
        appointment = Appointment.objects.create(
            schedule=assignment.schedule,
            assignment=assignment,
            tutor=assignment.tutor,
            starts_at=slot[0],
            ends_at=slot[1],
            blocked_until=slot[1],
            modality=Modality.ONLINE if kind == "online" else Modality.IN_PERSON,
            meeting_url=assignment.meeting_url if kind == "online" else "",
            room=None if kind == "online" else assignment.room,
            is_tentative=assignment.needs_confirmation,
        )
        student = world.user(profile.email)
        Participant.objects.create(appointment=appointment, student=student)
        _intake(world, student, appointment, key, profile, always=True)
        booked += 1
        if booked >= 24:
            break
