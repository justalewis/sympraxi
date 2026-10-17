"""Independent checks of the database after a load test.

These read the tables with plain SQL and work out every rule again here. They
share no code with `sympraxi.scheduling`, so a mistake in the booking service
can't hide itself.

Student limits are replayed in booking order. A schedule that counts every
schedule in the center can see appointments that were booked on another
schedule after it, so a final count above its limit isn't a violation by
itself; what matters is that no booking went over the limit when it was made.
The replay takes each seat's `added_at` as the moment it was booked, which
holds for this test because nobody cancels and rejoins.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection

PERIODS = ("day", "week", "term")


@dataclass(frozen=True)
class Appt:
    id: str
    schedule: str
    assignment: str
    tutor: str
    starts: datetime
    ends: datetime
    blocked_until: datetime
    canceled_at: datetime | None
    capacity: int
    placeholder: bool

    @property
    def minutes(self) -> int:
        return int((self.ends - self.starts) / timedelta(minutes=1))


@dataclass(frozen=True)
class Seat:
    appointment: str
    student: str
    added_at: datetime
    added_by: str | None
    canceled_at: datetime | None


@dataclass(frozen=True)
class Rules:
    id: str
    center: str
    tz: ZoneInfo
    term: tuple[date, date]
    span: bool
    most: dict[str, int | None]
    most_minutes: dict[str, int | None]
    notice: int
    horizon: int
    opens: time
    closes: time
    slot: int


@dataclass(frozen=True)
class Post:
    id: str
    schedule: str
    max_day: int | None
    max_week: int | None


@dataclass
class World:
    appointments: dict[str, Appt]
    seats: list[Seat]
    schedules: dict[str, Rules]
    assignments: dict[str, Post]
    hours: dict[tuple[str, int], list[tuple[time, time]]] = field(default_factory=dict)
    exceptions: set[str] = field(default_factory=set)  # assignments with hour changes
    exemptions: list[tuple[str, str | None, date]] = field(default_factory=list)
    overrides: set[tuple[str, str]] = field(default_factory=set)  # (appointment, student)


# --- Loading --------------------------------------------------------------------


def load(center_id: str) -> World:
    """Read one center's scheduling tables."""
    schedules = {
        row[0]: Rules(
            id=row[0],
            center=row[1],
            tz=ZoneInfo(row[2]),
            term=(row[3], row[4]),
            span=row[5],
            most={"day": row[6], "week": row[7], "term": row[8]},
            most_minutes={"day": row[9], "week": row[10], "term": row[11]},
            notice=row[12],
            horizon=row[13],
            opens=row[14],
            closes=row[15],
            slot=row[16],
        )
        for row in _rows(
            """
            SELECT s.id::text, s.center_id::text, c.time_zone, t.starts_on, t.ends_on,
                   s.limits_span_center, s.max_per_day, s.max_per_week, s.max_per_term,
                   s.max_minutes_per_day, s.max_minutes_per_week, s.max_minutes_per_term,
                   s.min_notice_minutes, s.horizon_days, s.opens_at, s.closes_at, s.slot_minutes
            FROM scheduling_schedule s
            JOIN centers_center c ON c.id = s.center_id
            JOIN scheduling_term t ON t.id = s.term_id
            WHERE s.center_id = %s
            """,
            [center_id],
        )
    }
    assignments = {
        row[0]: Post(id=row[0], schedule=row[1], max_day=row[2], max_week=row[3])
        for row in _rows(
            """
            SELECT a.id::text, a.schedule_id::text, a.max_per_day, a.max_per_week
            FROM scheduling_tutorassignment a
            JOIN scheduling_schedule s ON s.id = a.schedule_id
            WHERE s.center_id = %s
            """,
            [center_id],
        )
    }
    appointments = {
        row[0]: Appt(
            id=row[0],
            schedule=row[1],
            assignment=row[2],
            tutor=row[3],
            starts=row[4],
            ends=row[5],
            blocked_until=row[6],
            canceled_at=(row[8] or row[4]) if row[7] == "canceled" else None,
            capacity=row[9],
            placeholder=row[10],
        )
        for row in _rows(
            """
            SELECT a.id::text, a.schedule_id::text, a.assignment_id::text, a.tutor_id::text,
                   a.starts_at, a.ends_at, a.blocked_until, a.status, a.canceled_at,
                   a.capacity, a.is_placeholder
            FROM scheduling_appointment a
            JOIN scheduling_schedule s ON s.id = a.schedule_id
            WHERE s.center_id = %s
            """,
            [center_id],
        )
    }
    seats = [
        Seat(
            appointment=row[0],
            student=row[1],
            added_at=row[2],
            added_by=row[3],
            canceled_at=row[4],
        )
        for row in _rows(
            """
            SELECT p.appointment_id::text, p.student_id::text, p.added_at,
                   p.added_by_id::text, p.canceled_at
            FROM scheduling_participant p
            JOIN scheduling_appointment a ON a.id = p.appointment_id
            JOIN scheduling_schedule s ON s.id = a.schedule_id
            WHERE s.center_id = %s
            """,
            [center_id],
        )
    ]
    hours: dict[tuple[str, int], list[tuple[time, time]]] = defaultdict(list)
    for assignment, weekday, start, end in _rows(
        """
        SELECT h.assignment_id::text, h.weekday, h.starts_at, h.ends_at
        FROM scheduling_weeklyhours h
        JOIN scheduling_tutorassignment a ON a.id = h.assignment_id
        JOIN scheduling_schedule s ON s.id = a.schedule_id
        WHERE s.center_id = %s
        """,
        [center_id],
    ):
        hours[(assignment, weekday)].append((start, end))
    exceptions = {
        row[0]
        for row in _rows(
            """
            SELECT DISTINCT e.assignment_id::text
            FROM scheduling_availabilityexception e
            JOIN scheduling_tutorassignment a ON a.id = e.assignment_id
            JOIN scheduling_schedule s ON s.id = a.schedule_id
            WHERE s.center_id = %s
            """,
            [center_id],
        )
    }
    exemptions = [
        (row[0], row[1], row[2])
        for row in _rows(
            """
            SELECT student_id::text, schedule_id::text, expires_on
            FROM scheduling_exemption WHERE center_id = %s AND revoked_at IS NULL
            """,
            [center_id],
        )
    ]
    overrides: set[tuple[str, str]] = set()
    for target, students in _rows(
        """
        SELECT target_id, details -> 'students'
        FROM privacy_auditevent
        WHERE center_id = %s
          AND action IN ('scheduling.appointment_booked', 'scheduling.appointment_joined')
          AND (details ->> 'override_limits') = 'true'
        """,
        [center_id],
    ):
        overrides.update((target, s) for s in students or [])
    return World(
        appointments=appointments,
        seats=seats,
        schedules=schedules,
        assignments=assignments,
        hours=dict(hours),
        exceptions=exceptions,
        exemptions=exemptions,
        overrides=overrides,
    )


def _rows(sql: str, params: list[object]) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


# --- Rules ----------------------------------------------------------------------


def run_all(world: World, told_booked: set[tuple[str, str]] | None = None) -> dict[str, list[str]]:
    """Every check, by name, with the problems each found (empty when it passed)."""
    results = {
        "tutor double-booked": tutor_overlaps(world),
        "student double-booked": student_overlaps(world),
        "group over capacity": over_capacity(world),
        "tutor cap exceeded": tutor_caps(world),
        "student limit exceeded": student_limits(world),
        "booked inside notice or past horizon": notice_and_horizon(world),
        "outside the tutor's hours": outside_hours(world),
    }
    if told_booked is not None:
        results["database and responses disagree"] = agreement(world, told_booked)
    return results


def active(world: World) -> list[Appt]:
    return [a for a in world.appointments.values() if a.canceled_at is None]


def active_seats(world: World) -> list[Seat]:
    return [
        s
        for s in world.seats
        if s.canceled_at is None and world.appointments[s.appointment].canceled_at is None
    ]


def tutor_overlaps(world: World) -> list[str]:
    """No two active appointments for one tutor overlap, breaks included, on any schedule."""
    problems = []
    by_tutor: dict[str, list[Appt]] = defaultdict(list)
    for a in active(world):
        by_tutor[a.tutor].append(a)
    for tutor, appts in by_tutor.items():
        appts.sort(key=lambda a: a.starts)
        latest: Appt | None = None
        for a in appts:
            if latest is not None and a.starts < latest.blocked_until:
                problems.append(f"tutor {tutor}: {latest.id} and {a.id} overlap")
            if latest is None or a.blocked_until > latest.blocked_until:
                latest = a
    return problems


def student_overlaps(world: World) -> list[str]:
    """No student is in two active appointments at once."""
    problems = []
    by_student: dict[str, list[Appt]] = defaultdict(list)
    for s in active_seats(world):
        by_student[s.student].append(world.appointments[s.appointment])
    for student, appts in by_student.items():
        appts.sort(key=lambda a: a.starts)
        latest: Appt | None = None
        for a in appts:
            if latest is not None and a.starts < latest.ends:
                problems.append(f"student {student}: {latest.id} and {a.id} overlap")
            if latest is None or a.ends > latest.ends:
                latest = a
    return problems


def over_capacity(world: World) -> list[str]:
    """No appointment has more active students than its capacity."""
    seats: dict[str, int] = defaultdict(int)
    for s in active_seats(world):
        seats[s.appointment] += 1
    return [
        f"appointment {a}: {n} students, capacity {world.appointments[a].capacity}"
        for a, n in seats.items()
        if n > world.appointments[a].capacity
    ]


def tutor_caps(world: World) -> list[str]:
    """No tutor assignment has more appointments a day or a week than its caps."""
    problems = []
    counts: dict[tuple[str, str, date], int] = defaultdict(int)
    overridden = {a for a, _ in world.overrides}
    for a in active(world):
        if a.placeholder or a.id in overridden:
            continue
        day = local_date(a.starts, world.schedules[a.schedule].tz)
        counts[(a.assignment, "day", day)] += 1
        counts[(a.assignment, "week", week_of(day))] += 1
    for (assignment, period, key), n in counts.items():
        post = world.assignments[assignment]
        cap = post.max_day if period == "day" else post.max_week
        if cap is not None and n > cap:
            problems.append(f"assignment {assignment}: {n} on the {period} of {key}, cap {cap}")
    return problems


def student_limits(world: World) -> list[str]:
    """Replay each booking: it, plus what already counted, stayed within the limits."""
    problems = []
    seats_by_student: dict[str, list[Seat]] = defaultdict(list)
    for s in world.seats:
        if not world.appointments[s.appointment].placeholder:
            seats_by_student[s.student].append(s)
    for student, seats in seats_by_student.items():
        for seat in seats:
            appt = world.appointments[seat.appointment]
            rules = world.schedules[appt.schedule]
            day = local_date(appt.starts, rules.tz)
            if (seat.appointment, student) in world.overrides or _exempt(
                world, student, rules, day
            ):
                continue
            earlier = [
                world.appointments[other.appointment]
                for other in seats
                if other is not seat
                and other.added_at < seat.added_at
                and _live_at(world, other, seat.added_at)
                and _in_scope(world, rules, world.appointments[other.appointment])
            ]
            for period in PERIODS:
                most, most_minutes = rules.most[period], rules.most_minutes[period]
                if most is None and most_minutes is None:
                    continue
                window = _window(rules, day, period)
                counted = [o for o in earlier if _in_window(rules, o, window)]
                if most is not None and len(counted) + 1 > most:
                    problems.append(
                        f"student {student}: booking {appt.id} made {len(counted) + 1} "
                        f"in the {period}, limit {most}"
                    )
                used = sum(o.minutes for o in counted) + appt.minutes
                if most_minutes is not None and used > most_minutes:
                    problems.append(
                        f"student {student}: booking {appt.id} made {used} minutes "
                        f"in the {period}, limit {most_minutes}"
                    )
    return problems


def notice_and_horizon(world: World) -> list[str]:
    """Students booking for themselves kept to the notice and the horizon."""
    problems = []
    for seat in world.seats:
        if seat.added_by != seat.student:
            continue  # booked by staff, who aren't held to these
        appt = world.appointments[seat.appointment]
        rules = world.schedules[appt.schedule]
        if appt.starts < seat.added_at + timedelta(minutes=rules.notice):
            problems.append(f"student {seat.student}: {appt.id} booked inside the notice")
        last = local_date(seat.added_at, rules.tz) + timedelta(days=rules.horizon)
        if local_date(appt.starts, rules.tz) > last:
            problems.append(f"student {seat.student}: {appt.id} booked past the horizon")
    return problems


def outside_hours(world: World) -> list[str]:
    """Every appointment is on the schedule's grid and inside its tutor's weekly hours.

    Assignments with hour changes (extra hours or time off) are skipped; the
    load test makes none.
    """
    problems = []
    for a in active(world):
        if a.assignment in world.exceptions:
            continue
        rules = world.schedules[a.schedule]
        start, end = a.starts.astimezone(rules.tz), a.ends.astimezone(rules.tz)
        opens = datetime.combine(start.date(), rules.opens, start.tzinfo)
        on_grid = (start - opens) % timedelta(minutes=rules.slot) == timedelta(0)
        same_day = start.date() == end.date()
        inside_day = same_day and rules.opens <= start.time() and end.time() <= rules.closes
        blocks = _merge(world.hours.get((a.assignment, start.weekday()), []))
        covered = any(lo <= start.time() and end.time() <= hi for lo, hi in blocks)
        if not (on_grid and inside_day and covered):
            problems.append(f"appointment {a.id} at {start:%a %H:%M}-{end:%H:%M} is outside hours")
    return problems


def agreement(world: World, told_booked: set[tuple[str, str]]) -> list[str]:
    """The database holds exactly the bookings people were told they had."""
    held = {(s.student, s.appointment) for s in active_seats(world)}
    problems = [f"student {s} has {a} but was never told" for s, a in sorted(held - told_booked)]
    problems += [
        f"student {s} was told {a} but it isn't there" for s, a in sorted(told_booked - held)
    ]
    return problems


# --- Helpers --------------------------------------------------------------------


def local_date(instant: datetime, tz: ZoneInfo) -> date:
    return instant.astimezone(tz).date()


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _window(rules: Rules, day: date, period: str) -> tuple[date, date]:
    if period == "day":
        return day, day
    if period == "week":
        return week_of(day), week_of(day) + timedelta(days=6)
    return rules.term


def _in_window(rules: Rules, appt: Appt, window: tuple[date, date]) -> bool:
    return window[0] <= local_date(appt.starts, rules.tz) <= window[1]


def _in_scope(world: World, rules: Rules, appt: Appt) -> bool:
    if appt.placeholder:
        return False
    if rules.span:
        return world.schedules[appt.schedule].center == rules.center
    return appt.schedule == rules.id


def _live_at(world: World, seat: Seat, moment: datetime) -> bool:
    appt = world.appointments[seat.appointment]
    return (seat.canceled_at is None or seat.canceled_at > moment) and (
        appt.canceled_at is None or appt.canceled_at > moment
    )


def _exempt(world: World, student: str, rules: Rules, day: date) -> bool:
    return any(
        who == student and (schedule is None or schedule == rules.id) and day <= until
        for who, schedule, until in world.exemptions
    )


def _merge(blocks: Iterable[tuple[time, time]]) -> list[tuple[time, time]]:
    merged: list[tuple[time, time]] = []
    for lo, hi in sorted(blocks):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged
