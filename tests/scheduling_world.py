"""Builders for scheduling tests: terms, schedules, tutors with hours, and a full "world"."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.scheduling.forms import format_start
from sympraxi.scheduling.models import (
    Appointment,
    AvailabilityException,
    Closure,
    ExceptionKind,
    Exemption,
    Modality,
    Participant,
    Room,
    Schedule,
    Term,
    TutorAssignment,
    WeeklyHours,
)
from sympraxi.scheduling.timeutils import first_instant
from tests.factories import MembershipFactory, UserFactory, give_mfa

EVERY_DAY = range(7)


def make_term(center: Center, first: date, last: date) -> Term:
    return Term.objects.create(
        center=center, name=f"Term {first:%Y-%m-%d}", starts_on=first, ends_on=last
    )


def make_schedule(center: Center, first: date, last: date, **kwargs: object) -> Schedule:
    term = Term.objects.filter(
        center=center, starts_on__lte=first, ends_on__gte=last
    ).first() or make_term(center, first - timedelta(days=30), last + timedelta(days=30))
    fields: dict[str, object] = {
        "name": "Main schedule",
        "opens_at": time(0),
        "closes_at": time(23, 30),
        "slot_minutes": 30,
        "buffer_minutes": 0,
        "min_notice_minutes": 0,
        "horizon_days": 366,
        "change_cutoff_minutes": 0,
    }
    fields.update(kwargs)
    return Schedule.objects.create(
        center=center, term=term, starts_on=first, ends_on=last, **fields
    )


def make_member(center: Center, role: Role, **user_fields: object) -> User:
    user = UserFactory(**user_fields)
    MembershipFactory(user=user, center=center, role=role)
    if role != Role.STUDENT:
        give_mfa(user)
    return user


def assign(
    schedule: Schedule,
    tutor: User | None = None,
    *,
    weekdays: range | list[int] = EVERY_DAY,
    hours: tuple[tuple[time, time], ...] = ((time(9), time(17)),),
    **fields: object,
) -> TutorAssignment:
    tutor = tutor or make_member(schedule.center, Role.TUTOR)
    values: dict[str, object] = {"min_minutes": 30, "max_minutes": 60}
    values.update(fields)
    assignment = TutorAssignment.objects.create(schedule=schedule, tutor=tutor, **values)
    for weekday in weekdays:
        for start, end in hours:
            WeeklyHours.objects.create(
                assignment=assignment, weekday=weekday, starts_at=start, ends_at=end
            )
    return assignment


def at(schedule: Schedule, day: date, hour: int, minute: int = 0) -> datetime:
    """The instant a wall-clock time on `day` stands for in the schedule's zone."""
    return first_instant(day, time(hour, minute), schedule.tz)


@dataclass
class World:
    """One of everything in a center, for page and permission tests."""

    center: Center
    student: User
    tutor: User
    term: Term
    closure: Closure
    room: Room
    schedule: Schedule
    assignment: TutorAssignment
    exception: AvailabilityException
    exemption: Exemption
    appointment: Appointment
    free_start: datetime

    def slot_query(self) -> str:
        return urlencode(
            {
                "assignment": str(self.assignment.pk),
                "start": format_start(self.free_start),
                "minutes": 30,
            }
        )


def build_world(center: Center, *, student: User | None = None, tutor: User | None = None) -> World:
    """A schedule open every day from a week ago to a month ahead, with one booking tomorrow."""
    student = student or make_member(center, Role.STUDENT)
    tutor = tutor or make_member(center, Role.TUTOR)
    schedule_tz_today = timezone.now().astimezone(ZoneInfo(center.time_zone)).date()
    first = schedule_tz_today - timedelta(days=7)
    last = schedule_tz_today + timedelta(days=30)
    room = Room.objects.create(center=center, name="Room 101", details="Library, second floor")
    schedule = make_schedule(
        center, first, last, opens_at=time(8), closes_at=time(22), min_notice_minutes=0
    )
    closure = Closure.objects.create(
        term=schedule.term,
        label="Reading day",
        starts_on=schedule.term.ends_on,
        ends_on=schedule.term.ends_on,
    )
    assignment = assign(
        schedule,
        tutor,
        hours=((time(8), time(22)),),
        room=room,
        offers_online=True,
        meeting_url="https://meet.example.edu/tutor",
    )
    tomorrow = schedule_tz_today + timedelta(days=1)
    start = at(schedule, tomorrow, 10)
    appointment = Appointment.objects.create(
        schedule=schedule,
        assignment=assignment,
        tutor=tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality=Modality.IN_PERSON,
        room=room,
        is_tentative=True,
    )
    Participant.objects.create(appointment=appointment, student=student)
    exception = AvailabilityException.objects.create(
        assignment=assignment,
        kind=ExceptionKind.BLACKOUT,
        starts_at=at(schedule, last, 8),
        ends_at=at(schedule, last, 12),
        note="Conference",
    )
    exemption = Exemption.objects.create(
        center=center, student=student, expires_on=last, note="Accommodation on file"
    )
    return World(
        center=center,
        student=student,
        tutor=tutor,
        term=schedule.term,
        closure=closure,
        room=room,
        schedule=schedule,
        assignment=assignment,
        exception=exception,
        exemption=exemption,
        appointment=appointment,
        free_start=at(schedule, tomorrow, 14),
    )
