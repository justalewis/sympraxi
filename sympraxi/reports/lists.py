"""Master lists for the front desk: the appointments on a day or in a week, and the
students who booked in a period with how many times they came.

Both name students, so every view and every download is audited with the
students it named (`reports.list_viewed`, `reports.list_exported`), the way the
records pages are. The appointment list stops at `APPOINTMENT_LIMIT` rows and
says so; the student list is counted in PostgreSQL, one row per student.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.db.models import Count, Max, Min, Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.formats import time_format
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center, Membership, Role
from sympraxi.reports.forms import ListChoice, Period
from sympraxi.reports.statistics import CAME, LATE_CANCEL, NO_SHOW
from sympraxi.reports.tables import Column, Report, Table
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Modality, Participant
from sympraxi.scheduling.timeutils import date_range_bounds, local_date
from sympraxi.scheduling.views import attendance_label

# A week at a busy center is a few thousand appointments. Past this the page says
# the list was cut short and asks for one day or one schedule.
APPOINTMENT_LIMIT = 3000


@dataclass
class AppointmentList:
    report: Report
    # The appointments drawn, by day, for the page.
    days: list[tuple[date, list[Appointment]]]
    truncated: bool
    student_ids: list[str]


def appointments(center: Center, choice: ListChoice) -> tuple[list[Appointment], bool]:
    tz = ZoneInfo(center.time_zone)
    start, end = date_range_bounds(choice.first, choice.last, tz)
    found = Appointment.objects.filter(
        schedule__center=center, starts_at__gte=start, starts_at__lt=end
    )
    if choice.schedule is not None:
        found = found.filter(schedule=choice.schedule)
    if not choice.include_canceled:
        found = found.filter(status=AppointmentStatus.SCHEDULED)
    rows: list[Appointment] = list(
        found.select_related("tutor", "schedule", "room")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.select_related("student").order_by(
                    "student__full_name", "pk"
                ),
                to_attr="people",
            )
        )
        .order_by("starts_at", "tutor__full_name", "pk")[: APPOINTMENT_LIMIT + 1]
    )
    return rows[:APPOINTMENT_LIMIT], len(rows) > APPOINTMENT_LIMIT


def shown_people(appointment: Appointment) -> list[Participant]:
    """The students to name: those still in it, or everyone who was in a canceled one."""
    people: list[Participant] = getattr(appointment, "people", [])
    if not appointment.is_active:
        return people
    return [p for p in people if p.canceled_at is None]


def status_label(appointment: Appointment) -> str:
    if not appointment.is_active:
        words = [_("Canceled")]
    elif appointment.is_tentative:
        words = [_("Tentative")]
    else:
        words = [_("Booked")]
    if appointment.is_walk_in:
        words.append(_("walk-in"))
    if appointment.is_placeholder:
        words.append(_("placeholder"))
    if appointment.capacity > 1:
        words.append(_("group"))
    return ", ".join(words)


def place(appointment: Appointment) -> str:
    if appointment.modality == Modality.ONLINE:
        return _("Online")
    return appointment.room.name if appointment.room else ""


def appointment_list(center: Center, choice: ListChoice) -> AppointmentList:
    tz = ZoneInfo(center.time_zone)
    rows, truncated = appointments(center, choice)
    now = timezone.now()
    labels = dict(Modality.choices)
    days: dict[date, list[Appointment]] = {}
    table_rows: list[list[Any]] = []
    named: dict[str, None] = {}
    for appointment in rows:
        people = shown_people(appointment)
        for person in people:
            person.attendance_label = attendance_label(person, appointment, now)  # type: ignore[attr-defined]
            named[str(person.student_id)] = None
        appointment.shown = people  # type: ignore[attr-defined]
        appointment.status_label = status_label(appointment)  # type: ignore[attr-defined]
        appointment.place = place(appointment)  # type: ignore[attr-defined]
        day = local_date(appointment.starts_at, tz)
        days.setdefault(day, []).append(appointment)
        local_start = appointment.starts_at.astimezone(tz)
        local_end = appointment.ends_at.astimezone(tz)
        table_rows.append(
            [
                day,
                time_format(local_start, "H:i"),
                time_format(local_end, "H:i"),
                appointment.schedule.name,
                appointment.tutor.display_name,
                "; ".join(p.student.display_name for p in people),
                "; ".join(p.student.student_id or "" for p in people),
                str(labels.get(appointment.modality, appointment.modality)),
                appointment.place,  # type: ignore[attr-defined]
                appointment.status_label,  # type: ignore[attr-defined]
                "; ".join(
                    f"{p.student.display_name}: {p.attendance_label}"  # type: ignore[attr-defined]
                    for p in people
                    if p.attendance_label  # type: ignore[attr-defined]
                ),
            ]
        )
    table = Table(
        "appointments",
        _("Appointments"),
        [
            Column("date", _("Date")),
            Column("starts", _("Starts")),
            Column("ends", _("Ends")),
            Column("schedule", _("Schedule")),
            Column("tutor", _("Tutor")),
            Column("students", _("Students")),
            Column("student_ids", _("Student IDs")),
            Column("modality", _("How")),
            Column("place", _("Where")),
            Column("status", _("Status")),
            Column("attendance", _("Attendance")),
        ],
        table_rows,
        note=_("The list was cut short at %(n)s appointments.") % {"n": APPOINTMENT_LIMIT}
        if truncated
        else "",
    )
    report = Report(
        kind="appointment-list",
        title=_("Appointment list"),
        described=choice.described(),
        tables=[table],
        meta={
            "from": choice.first,
            "to": choice.last,
            "schedule": choice.schedule.name if choice.schedule else None,
            "time_zone": center.time_zone,
        },
    )
    return AppointmentList(report, sorted(days.items()), truncated, list(named))


# --- Students and their visits -----------------------------------------------------


def _visits(prefix: str, condition: Q) -> Q:
    """`condition` on Participant, rewritten for a query that reaches it through `prefix`."""
    children: list[Any] = []
    for child in condition.children:
        if isinstance(child, Q):
            children.append(_visits(prefix, child))
        else:
            key, value = cast(tuple[str, Any], child)
            children.append((f"{prefix}{key}", value))
    return Q(*children, _connector=condition.connector, _negated=condition.negated)


def students(center: Center, period: Period) -> QuerySet[Membership]:
    """Each student with an appointment in the period, with what happened to their visits."""
    start, end = period.bounds(center)
    p = "user__participations__"
    here = Q(
        **{
            f"{p}appointment__schedule__center": center,
            f"{p}appointment__is_placeholder": False,
        }
    )
    in_period = here & Q(
        **{f"{p}appointment__starts_at__gte": start, f"{p}appointment__starts_at__lt": end}
    )
    if period.schedule is not None:
        in_period &= Q(**{f"{p}appointment__schedule": period.schedule})
    return (
        Membership.objects.filter(center=center, role=Role.STUDENT)
        .annotate(
            booked=Count(f"{p}pk", filter=in_period),
            came=Count(f"{p}pk", filter=in_period & _visits(p, CAME)),
            no_shows=Count(f"{p}pk", filter=in_period & _visits(p, NO_SHOW)),
            late_cancels=Count(f"{p}pk", filter=in_period & _visits(p, LATE_CANCEL)),
            first_visit=Min(f"{p}appointment__starts_at", filter=here & _visits(p, CAME)),
            last_visit=Max(f"{p}appointment__starts_at", filter=in_period & _visits(p, CAME)),
        )
        .filter(booked__gt=0)
        .select_related("user")
        .order_by("user__full_name", "user__email", "pk")
    )


def student_columns() -> list[Column]:
    return [
        Column("name", _("Name")),
        Column("goes_by", _("Goes by")),
        Column("email", _("Email")),
        Column("student_id", _("Student ID")),
        Column("booked", _("Visits booked"), numeric=True),
        Column("came", _("Came"), numeric=True),
        Column("no_shows", _("No-shows"), numeric=True),
        Column("late_cancels", _("Late cancellations"), numeric=True),
        Column("first_visit", _("First visit")),
        Column("last_visit", _("Last visit in this period")),
    ]


def student_row(m: Membership, tz: ZoneInfo) -> list[Any]:
    user = m.user
    first = getattr(m, "first_visit", None)
    last = getattr(m, "last_visit", None)
    return [
        user.full_name,
        user.preferred_name,
        user.email,
        user.student_id or "",
        getattr(m, "booked", 0),
        getattr(m, "came", 0),
        getattr(m, "no_shows", 0),
        getattr(m, "late_cancels", 0),
        local_date(first, tz) if first else None,
        local_date(last, tz) if last else None,
    ]


def student_report(center: Center, period: Period, members: list[Membership]) -> Report:
    tz = ZoneInfo(center.time_zone)
    return Report(
        kind="student-list",
        title=_("Students and their visits"),
        described=period.described(),
        tables=[
            Table(
                "students",
                _("Students"),
                student_columns(),
                [student_row(m, tz) for m in members],
                note=_(
                    "Students with at least one appointment in the period. A first visit is the "
                    "student's first visit to the center, on any schedule."
                ),
            )
        ],
        meta=period.meta(),
    )
