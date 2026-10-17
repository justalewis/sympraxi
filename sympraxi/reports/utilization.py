"""Hours offered, booked and attended, by schedule, by tutor and by week.

- Offered: each tutor's weekly hours on the schedule, plus extra hours, minus
  time off, inside the schedule's opening hours and dates, on days the term
  isn't closed. This is how open times are worked out
  (`sympraxi/scheduling/availability.py`), added up day by day. Weekly hours
  aren't kept as a history, so the hours offered are worked out from each tutor's
  hours as they are now, over the whole period. Tutors marked as not taking
  appointments offer none.
- Booked: the length of every appointment that wasn't canceled and isn't a
  placeholder. A group appointment counts once: it is one hour of the tutor's
  time however many students joined.
- Attended: the booked hours of appointments at least one student was marked
  as coming to.

Weeks run Monday to Sunday in the center's time zone. Booked and attended hours
are summed in PostgreSQL; offered hours are worked out in Python from the
tutors' hours, which grow with the number of tutors and days, never with the
number of appointments.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import DurationField, Exists, ExpressionWrapper, F, OuterRef, Prefetch, Q, Sum
from django.db.models.functions import TruncWeek
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.reports.forms import Period
from sympraxi.reports.tables import Column, Report, Table
from sympraxi.scheduling.availability import available_intervals, day_window
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    AvailabilityException,
    Participant,
    Schedule,
    TutorAssignment,
)
from sympraxi.scheduling.timeutils import week_start

# (schedule, tutor, Monday of the week)
Key = tuple[UUID, UUID, date]


@dataclass
class Hours:
    offered: timedelta = timedelta(0)
    booked: timedelta = timedelta(0)
    attended: timedelta = timedelta(0)

    def add(self, other: "Hours") -> None:
        self.offered += other.offered
        self.booked += other.booked
        self.attended += other.attended

    def cells(self) -> list[Any]:
        share = None
        if self.offered > timedelta(0):
            exact = Decimal(100 * self.booked.total_seconds()) / Decimal(
                self.offered.total_seconds()
            )
            share = int(exact.quantize(Decimal(1), ROUND_HALF_UP))
        return [hours(self.offered), hours(self.booked), hours(self.attended), share]


def hours(value: timedelta) -> Decimal:
    """Hours to one decimal place: 90 minutes is 1.5."""
    return (Decimal(value.total_seconds()) / 3600).quantize(Decimal("0.1"), ROUND_HALF_UP)


def schedules_in(center: Center, period: Period) -> list[Schedule]:
    if period.schedule is not None:
        return [period.schedule]
    return list(
        Schedule.objects.filter(
            center=center, starts_on__lte=period.last, ends_on__gte=period.first
        ).order_by("name", "pk")
    )


def offered(center: Center, period: Period, schedules: list[Schedule]) -> dict[Key, timedelta]:
    start, end = period.bounds(center)
    assignments = (
        TutorAssignment.objects.filter(schedule__in=schedules, is_active=True)
        .select_related("schedule__center", "schedule__term")
        .prefetch_related(
            "weekly_hours",
            "schedule__term__closures",
            Prefetch(
                "exceptions",
                queryset=AvailabilityException.objects.filter(
                    starts_at__lt=end + timedelta(days=1), ends_at__gt=start - timedelta(days=1)
                ),
            ),
        )
    )
    found: dict[Key, timedelta] = defaultdict(timedelta)
    for assignment in assignments:
        schedule = assignment.schedule
        day = max(period.first, schedule.starts_on)
        last = min(period.last, schedule.ends_on)
        while day <= last:
            window = day_window(schedule, day)
            if window is not None:
                for begins, ends in available_intervals(assignment, day, window):
                    found[(schedule.pk, assignment.tutor_id, week_start(day))] += ends - begins
            day += timedelta(days=1)
    return found


def booked(
    center: Center, period: Period, schedules: list[Schedule]
) -> dict[Key, tuple[timedelta, timedelta]]:
    start, end = period.bounds(center)
    tz = ZoneInfo(center.time_zone)
    length = ExpressionWrapper(F("ends_at") - F("starts_at"), output_field=DurationField())
    someone_came = Participant.objects.filter(
        appointment=OuterRef("pk"), canceled_at__isnull=True, attendance=Attendance.ATTENDED
    )
    rows = (
        Appointment.objects.filter(
            schedule__in=schedules,
            starts_at__gte=start,
            starts_at__lt=end,
            is_placeholder=False,
            status=AppointmentStatus.SCHEDULED,
        )
        .annotate(week=TruncWeek("starts_at", tzinfo=tz), came=Exists(someone_came))
        .values("schedule_id", "tutor_id", "week")
        .annotate(booked=Sum(length), attended=Sum(length, filter=Q(came=True)))
        .order_by()
    )
    return {
        (r["schedule_id"], r["tutor_id"], r["week"].astimezone(tz).date()): (
            r["booked"] or timedelta(0),
            r["attended"] or timedelta(0),
        )
        for r in rows
    }


def columns(*first: Column) -> list[Column]:
    return [
        *first,
        Column("offered", _("Hours offered"), numeric=True),
        Column("booked", _("Hours booked"), numeric=True),
        Column("attended", _("Hours attended"), numeric=True),
        Column("booked_share", _("Booked, % of offered"), numeric=True),
    ]


def build(center: Center, period: Period) -> Report:
    schedules = schedules_in(center, period)
    detail: dict[Key, Hours] = defaultdict(Hours)
    for key, length in offered(center, period, schedules).items():
        detail[key].offered += length
    for key, (b, a) in booked(center, period, schedules).items():
        detail[key].booked += b
        detail[key].attended += a

    names = {s.pk: s.name for s in schedules}
    tutors = {
        u.pk: u.display_name
        for u in User.objects.filter(pk__in={tutor for _s, tutor, _w in detail}).only(
            "full_name", "preferred_name", "email"
        )
    }

    by_schedule: dict[UUID, Hours] = defaultdict(Hours)
    by_tutor: dict[tuple[UUID, UUID], Hours] = defaultdict(Hours)
    # Every week in the period, so a week the center was closed shows as nothing.
    by_week: dict[date, Hours] = defaultdict(Hours)
    monday = week_start(period.first)
    while monday <= period.last:
        by_week[monday] = Hours()
        monday += timedelta(days=7)
    total = Hours()
    for (schedule_id, tutor_id, week), value in detail.items():
        by_schedule[schedule_id].add(value)
        by_tutor[(schedule_id, tutor_id)].add(value)
        by_week[week].add(value)
        total.add(value)

    def tutor_order(key: tuple[UUID, UUID]) -> tuple[str, str]:
        return (names[key[0]], tutors.get(key[1], "").casefold())

    tables = [
        Table(
            "by_schedule",
            _("By schedule"),
            columns(Column("schedule", _("Schedule"))),
            [
                [names[s], *by_schedule[s].cells()]
                for s in sorted(by_schedule, key=lambda s: names[s])
            ]
            + [[_("Every schedule shown"), *total.cells()]],
        ),
        Table(
            "by_tutor",
            _("By tutor"),
            columns(Column("tutor", _("Tutor")), Column("schedule", _("Schedule"))),
            [
                [tutors.get(t, ""), names[s], *by_tutor[(s, t)].cells()]
                for s, t in sorted(by_tutor, key=tutor_order)
            ],
        ),
        Table(
            "by_week",
            _("By week"),
            columns(Column("week", _("Week of"))),
            [[w, *by_week[w].cells()] for w in sorted(by_week)],
        ),
        Table(
            "by_tutor_and_week",
            _("Each tutor, week by week"),
            columns(
                Column("tutor", _("Tutor")),
                Column("schedule", _("Schedule")),
                Column("week", _("Week of")),
            ),
            [
                [tutors.get(t, ""), names[s], w, *detail[(s, t, w)].cells()]
                for s, t, w in sorted(detail, key=lambda k: (*tutor_order((k[0], k[1])), k[2]))
            ],
        ),
    ]
    tables[0].note = _(
        "Hours offered come from each tutor's weekly hours and changes to them as they are now. "
        "Placeholders and canceled appointments aren't booked hours. A group appointment counts "
        "once."
    )
    return Report(
        kind="utilization",
        title=_("Hours offered and booked"),
        described=period.described(),
        tables=tables,
        meta=period.meta(),
    )
