"""Open times: when a tutor is available and not already booked.

A tutor's availability on a date is their weekly hours for that weekday, plus
extra hours, minus time off, inside the schedule's opening hours. Appointments
start on the schedule's grid: every `slot_minutes` from the opening time.
Nothing here writes to the database; `services.book()` re-checks every rule
inside a transaction before it creates anything.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from django.db.models import Prefetch

from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityException,
    ExceptionKind,
    Participant,
    Schedule,
    TutorAssignment,
)
from sympraxi.scheduling.timeutils import (
    Interval,
    clip,
    covers,
    first_instant,
    local_date,
    merge,
    overlaps,
    subtract,
)


@dataclass(frozen=True)
class OpenTime:
    assignment: TutorAssignment
    starts_at: datetime
    ends_at: datetime
    # Set when this is a seat in a group appointment that someone already booked.
    appointment: Appointment | None = None
    seats_left: int = 1

    @property
    def modalities(self) -> list[str]:
        if self.appointment is not None:
            return [self.appointment.modality]
        return self.assignment.modalities

    @property
    def minutes(self) -> int:
        return int((self.ends_at - self.starts_at) / timedelta(minutes=1))


def is_closed(schedule: Schedule, day: date) -> bool:
    if not (schedule.starts_on <= day <= schedule.ends_on):
        return True
    return any(c.starts_on <= day <= c.ends_on for c in schedule.term.closures.all())


def day_window(schedule: Schedule, day: date) -> Interval | None:
    """The schedule's opening hours on a date, or None if it is closed that day."""
    if is_closed(schedule, day):
        return None
    tz = schedule.tz
    return first_instant(day, schedule.opens_at, tz), first_instant(day, schedule.closes_at, tz)


def available_intervals(assignment: TutorAssignment, day: date, window: Interval) -> list[Interval]:
    """When the tutor is available on `day`, clipped to the schedule's opening hours.

    Reads `assignment.weekly_hours` and `assignment.exceptions`, so prefetch them
    when looking at many tutors.
    """
    tz = assignment.schedule.tz
    weekly = [
        (first_instant(day, h.starts_at, tz), first_instant(day, h.ends_at, tz))
        for h in assignment.weekly_hours.all()
        if h.weekday == day.weekday()
    ]
    extras: list[Interval] = []
    blackouts: list[Interval] = []
    for exc in assignment.exceptions.all():
        if not overlaps((exc.starts_at, exc.ends_at), window):
            continue
        (extras if exc.kind == ExceptionKind.EXTRA else blackouts).append(
            (exc.starts_at, exc.ends_at)
        )
    return clip(subtract(merge(weekly + extras), blackouts), window)


def on_grid(schedule: Schedule, starts_at: datetime) -> bool:
    """True if `starts_at` is a whole number of slots after the opening time that day."""
    day = local_date(starts_at, schedule.tz)
    opening = first_instant(day, schedule.opens_at, schedule.tz)
    offset = starts_at - opening
    return offset >= timedelta(0) and offset % timedelta(
        minutes=schedule.slot_minutes
    ) == timedelta(0)


def fits_availability(assignment: TutorAssignment, starts_at: datetime, ends_at: datetime) -> bool:
    """True if [starts_at, ends_at) is on the grid and inside the tutor's hours that day."""
    schedule = assignment.schedule
    day = local_date(starts_at, schedule.tz)
    window = day_window(schedule, day)
    if window is None or not on_grid(schedule, starts_at):
        return False
    return covers(available_intervals(assignment, day, window), starts_at, ends_at)


def busy_intervals(tutor_ids: Iterable[UUID], window: Interval) -> dict[UUID, list[Interval]]:
    """Each tutor's active appointments (with breaks) touching the window, on any schedule."""
    busy: dict[UUID, list[Interval]] = defaultdict(list)
    rows = (
        Appointment.objects.filter(
            tutor_id__in=list(tutor_ids), starts_at__lt=window[1], blocked_until__gt=window[0]
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .values_list("tutor_id", "starts_at", "blocked_until")
    )
    for tutor_id, start, end in rows:
        busy[tutor_id].append((start, end))
    return busy


def lengths_offered(schedule: Schedule) -> list[int]:
    lengths: set[int] = set()
    for assignment in schedule.assignments.filter(is_active=True).select_related("schedule"):
        lengths.update(assignment.lengths())
    return sorted(lengths)


def open_times(
    schedule: Schedule,
    day: date,
    minutes: int,
    *,
    earliest: datetime | None = None,
    assignment_id: UUID | None = None,
    modality: str | None = None,
) -> list[OpenTime]:
    """Times on `day` when an appointment of `minutes` could be booked, earliest first.

    `earliest` hides times that start before it (the minimum notice, for students).
    Group appointments with seats left are listed as open times of their own.
    """
    window = day_window(schedule, day)
    if window is None:
        return []
    assignments = (
        schedule.assignments.filter(is_active=True)
        .select_related("tutor", "schedule__center", "room")
        .prefetch_related(
            "weekly_hours",
            Prefetch(
                "exceptions",
                queryset=AvailabilityException.objects.filter(
                    starts_at__lt=window[1], ends_at__gt=window[0]
                ),
            ),
        )
    )
    if assignment_id is not None:
        assignments = assignments.filter(pk=assignment_id)
    if modality == "in_person":
        assignments = assignments.filter(offers_in_person=True)
    elif modality == "online":
        assignments = assignments.filter(offers_online=True)
    candidates = [a for a in assignments if minutes in a.lengths()]
    if not candidates:
        return []

    buffer = timedelta(minutes=schedule.buffer_minutes)
    length = timedelta(minutes=minutes)
    step = timedelta(minutes=schedule.slot_minutes)
    busy = busy_intervals(
        {a.tutor_id for a in candidates}, (window[0], window[1] + length + buffer)
    )
    groups = _groups_with_seats(candidates, window)

    result: list[OpenTime] = []
    for assignment in candidates:
        available = available_intervals(assignment, day, window)
        tutor_busy = busy.get(assignment.tutor_id, [])
        start = window[0]
        while start + length <= window[1]:
            end = start + length
            if (
                (earliest is None or start >= earliest)
                and covers(available, start, end)
                and not any(overlaps((start, end + buffer), b) for b in tutor_busy)
            ):
                result.append(OpenTime(assignment, start, end))
            start += step
        for appointment, taken in groups.get(assignment.pk, []):
            if appointment.minutes != minutes:
                continue
            if earliest is not None and appointment.starts_at < earliest:
                continue
            if modality and appointment.modality != modality:
                continue
            result.append(
                OpenTime(
                    assignment,
                    appointment.starts_at,
                    appointment.ends_at,
                    appointment=appointment,
                    seats_left=appointment.capacity - taken,
                )
            )
    result.sort(key=lambda o: (o.starts_at, o.assignment.tutor.display_name))
    return result


def _groups_with_seats(
    assignments: list[TutorAssignment], window: Interval
) -> dict[UUID, list[tuple[Appointment, int]]]:
    appointments = list(
        Appointment.objects.filter(
            assignment__in=[a for a in assignments if a.capacity > 1],
            starts_at__gte=window[0],
            starts_at__lt=window[1],
            is_placeholder=False,
            capacity__gt=1,
        ).exclude(status=AppointmentStatus.CANCELED)
    )
    taken = Counter(
        Participant.objects.filter(
            appointment__in=appointments, canceled_at__isnull=True
        ).values_list("appointment_id", flat=True)
    )
    groups: dict[UUID, list[tuple[Appointment, int]]] = defaultdict(list)
    for appointment in appointments:
        if taken[appointment.pk] < appointment.capacity:
            groups[appointment.assignment_id].append((appointment, taken[appointment.pk]))
    return groups
