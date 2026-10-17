"""The center the load test books against.

`build()` makes one center with three schedules: in person, online, and group
sessions. 24 tutors work overlapping shifts, so the popular hours have the most
of them. Six tutors work both in person and online and two work in person and
in groups, so bookings for one tutor on two schedules race each other, which is
what the per-tutor locks are for. Every student and front desk worker is
already signed in: each gets a session row, so the test spends its time
booking rather than hashing passwords. Pass `password_hash` to also let the
students sign in with a password.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.backends.base import VALID_KEY_CHARS
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.scheduling.models import Room, Schedule, Term, TutorAssignment, WeeklyHours

SLUG = "loadtest"
DOMAIN = "load.test"
WEEKDAYS = range(5)  # Monday to Friday
BACKEND = "django.contrib.auth.backends.ModelBackend"

Hours = tuple[tuple[int, int], ...]

# In-person shifts, handed out in turn. They overlap most from 11 to 15.
MAIN_SHIFTS: list[Hours] = [
    ((9, 13),),
    ((10, 14),),
    ((11, 15),),
    ((12, 16),),
    ((13, 17),),
    ((14, 18),),
    ((9, 12), (13, 16)),
    ((10, 12), (14, 18)),
]
ONLINE_SHIFTS: list[Hours] = [
    ((10, 14),),
    ((12, 16),),
    ((14, 18),),
    ((16, 20),),
    ((11, 15),),
    ((13, 17),),
]
GROUP_HOURS: Hours = ((12, 16),)

MAIN_TUTORS = 16
SHARED_ONLINE = range(10, 16)  # in-person tutors who also work online
ONLINE_ONLY = 6
GROUP_FROM_MAIN = range(2)  # in-person tutors who also lead groups
GROUP_ONLY = 2
CAPPED = range(2, 6)  # at most 5 appointments a day
NEEDS_CONFIRMATION = (6,)
GROUP_SIZE = 4


def build(
    *,
    students: int = 500,
    front_desk: int = 3,
    password_hash: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Create the center and return the manifest the driver reads."""
    with transaction.atomic():
        return _build(students, front_desk, password_hash, today)


def _build(
    n_students: int, n_front_desk: int, password_hash: str | None, today: date | None
) -> dict[str, Any]:
    center = Center.objects.create(
        name="Load Test Writing Center",
        slug=SLUG,
        time_zone="America/New_York",
        contact_email=f"writing@{DOMAIN}",
    )
    tz_today = today or timezone.now().astimezone(ZoneInfo(center.time_zone)).date()
    term = Term.objects.create(
        center=center,
        name="Load test term",
        starts_on=tz_today - timedelta(days=7),
        ends_on=tz_today + timedelta(days=120),
    )
    room = Room.objects.create(center=center, name="Library 210", details="Second floor")
    common = {"center": center, "term": term, "starts_on": term.starts_on, "ends_on": term.ends_on}
    main = Schedule.objects.create(
        **common,
        name="Writing center",
        opens_at=time(9),
        closes_at=time(18),
        slot_minutes=30,
        max_per_day=1,
        max_per_week=2,
        max_minutes_per_week=120,
    )
    online = Schedule.objects.create(
        **common,
        name="Online",
        opens_at=time(10),
        closes_at=time(20),
        slot_minutes=30,
        buffer_minutes=10,
        limits_span_center=True,
        max_per_day=2,
        max_per_week=3,
    )
    groups = Schedule.objects.create(
        **common,
        name="Group sessions",
        opens_at=time(12),
        closes_at=time(16),
        slot_minutes=60,
        max_per_week=1,
    )

    tutors = _people(center, Role.TUTOR, "tutor", MAIN_TUTORS + ONLINE_ONLY + GROUP_ONLY)
    _people(center, Role.DIRECTOR, "director", 1)
    desk = _people(center, Role.FRONT_DESK, "frontdesk", n_front_desk)
    Authenticator.objects.bulk_create(
        Authenticator(user=u, type=Authenticator.Type.TOTP, data={"secret": "load-test"})
        for u in [*tutors, *desk]
    )
    learners = _people(center, Role.STUDENT, "student", n_students, password_hash=password_hash)

    main_tutors = tutors[:MAIN_TUTORS]
    online_tutors = [main_tutors[i] for i in SHARED_ONLINE] + tutors[
        MAIN_TUTORS : MAIN_TUTORS + ONLINE_ONLY
    ]
    group_tutors = [main_tutors[i] for i in GROUP_FROM_MAIN] + tutors[MAIN_TUTORS + ONLINE_ONLY :]
    for i, tutor in enumerate(main_tutors):
        _assign(
            main,
            tutor,
            MAIN_SHIFTS[i % len(MAIN_SHIFTS)],
            room=room,
            max_per_day=5 if i in CAPPED else None,
            needs_confirmation=i in NEEDS_CONFIRMATION,
        )
    for i, tutor in enumerate(online_tutors):
        _assign(
            online,
            tutor,
            ONLINE_SHIFTS[i % len(ONLINE_SHIFTS)],
            offers_in_person=False,
            offers_online=True,
            meeting_url=f"https://meet.{DOMAIN}/{tutor.pk}",
        )
    for tutor in group_tutors:
        _assign(
            groups,
            tutor,
            GROUP_HOURS,
            room=room,
            min_minutes=60,
            max_minutes=60,
            capacity=GROUP_SIZE,
        )

    return {
        "center": {"id": str(center.pk), "slug": center.slug, "time_zone": center.time_zone},
        "schedules": [
            {"key": "main", "id": str(main.pk), "name": main.name, "lengths": [30, 60]},
            {"key": "online", "id": str(online.pk), "name": online.name, "lengths": [30, 60]},
            {"key": "groups", "id": str(groups.pk), "name": groups.name, "lengths": [60]},
        ],
        "tutors": len(tutors),
        "students": _signed_in(learners),
        "front_desk": _signed_in(desk),
        "built_at": timezone.now().isoformat(),
    }


def _people(
    center: Center, role: Role, label: str, count: int, *, password_hash: str | None = None
) -> list[User]:
    users = User.objects.bulk_create(
        User(
            email=f"{label}{i:04d}@{DOMAIN}",
            full_name=f"{label.title()} {i:04d}",
            student_id=f"L{i:06d}" if role == Role.STUDENT else None,
            password=password_hash or make_password(None),
        )
        for i in range(1, count + 1)
    )
    Membership.objects.bulk_create(Membership(user=u, center=center, role=role) for u in users)
    EmailAddress.objects.bulk_create(
        EmailAddress(user=u, email=u.email, verified=True, primary=True) for u in users
    )
    return users


def _assign(schedule: Schedule, tutor: User, hours: Hours, **fields: Any) -> TutorAssignment:
    assignment = TutorAssignment.objects.create(schedule=schedule, tutor=tutor, **fields)
    WeeklyHours.objects.bulk_create(
        WeeklyHours(assignment=assignment, weekday=day, starts_at=time(start), ends_at=time(end))
        for day in WEEKDAYS
        for start, end in hours
    )
    return assignment


def _signed_in(users: list[User]) -> list[dict[str, str]]:
    """A session row for each person, as if they had just signed in."""
    store = SessionStore()
    expires = timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE)
    people = []
    rows = []
    for user in users:
        key = get_random_string(32, VALID_KEY_CHARS)
        data = {
            SESSION_KEY: str(user.pk),
            BACKEND_SESSION_KEY: BACKEND,
            HASH_SESSION_KEY: user.get_session_auth_hash(),
        }
        rows.append(Session(session_key=key, session_data=store.encode(data), expire_date=expires))
        people.append({"id": str(user.pk), "email": user.email, "session": key})
    Session.objects.bulk_create(rows)
    return people


def first_weekdays(after: date, count: int) -> list[date]:
    """The next `count` weekdays after `after`: the days students try to book."""
    days: list[date] = []
    day = after
    while len(days) < count:
        day += timedelta(days=1)
        if day.weekday() in WEEKDAYS:
            days.append(day)
    return days


def local_today(manifest: dict[str, Any]) -> date:
    return datetime.now(ZoneInfo(manifest["center"]["time_zone"])).date()
