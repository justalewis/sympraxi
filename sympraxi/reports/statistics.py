"""A center's statistics for a term or a range of dates, on one schedule or all.

Everything is counted in PostgreSQL. A term at a busy center has thousands of
appointments, and none of them is loaded into Python here; each table is one
grouped query.

What is counted:

- A visit is one student booked into one appointment (a `Participant`), so a
  group of four is four visits. Placeholders hold time without a student and
  have none. Each visit has one outcome: came, no-show, late cancellation,
  canceled (the student, staff or a canceled appointment), or not marked yet
  (upcoming, or past and no one has marked it). Waived no-shows and late
  cancellations still count here; a waiver only takes them out of the count
  toward a booking block.
- An appointment's kind, one each, checked in this order: walk-in, group (room
  for more than one student), part of a weekly series, or a single booking.
- A first visit is a student's first visit marked "came" at the center, on any
  schedule and at any time, which falls in the period. Their later visits are
  return visits, so a student who first came last term returns this term.
- Topics come from final session records held in the period. A record for a
  meeting that wasn't booked has no schedule, so it counts only when the report
  covers every schedule.

Survey answers are left out on purpose. They are anonymous, counted per term
only, and shown only once five people have answered (`sympraxi/forms/surveys.py`);
a report by tutor or by week could single someone out.
"""

from collections.abc import Mapping
from typing import Any

from django.db.models import Case, Count, F, OuterRef, Q, QuerySet, Subquery, Value, When
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.records.models import Axis, RecordStatus, RecordTopic, SessionRecord
from sympraxi.reports.forms import Period
from sympraxi.reports.tables import Column, Report, Table
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Modality,
    Participant,
)

# Each outcome of a visit, as a condition on `Participant`.
HELD = Q(canceled_at__isnull=True, appointment__status=AppointmentStatus.SCHEDULED)
CAME = HELD & Q(attendance=Attendance.ATTENDED)
NO_SHOW = HELD & Q(attendance=Attendance.NO_SHOW)
NOT_MARKED = HELD & Q(attendance=Attendance.UNMARKED)
LATE_CANCEL = Q(late_cancel=True)
CANCELED = ~HELD & Q(late_cancel=False)

OUTCOMES = [
    ("booked", None),
    ("came", CAME),
    ("late_cancels", LATE_CANCEL),
    ("no_shows", NO_SHOW),
    ("canceled", CANCELED),
    ("not_marked", NOT_MARKED),
]


def kind_labels() -> dict[str, str]:
    return {
        "walk_in": _("Walk-in"),
        "group": _("Group"),
        "series": _("Weekly series"),
        "single": _("Single booking"),
    }


def outcome_columns(first: Column) -> list[Column]:
    return [
        first,
        Column("booked", _("Visits booked"), numeric=True),
        Column("came", _("Came"), numeric=True),
        Column("late_cancels", _("Late cancellations"), numeric=True),
        Column("no_shows", _("No-shows"), numeric=True),
        Column("canceled", _("Canceled"), numeric=True),
        Column("not_marked", _("Not marked"), numeric=True),
    ]


def _counts() -> dict[str, Count]:
    return {
        name: Count("pk", filter=condition) if condition is not None else Count("pk")
        for name, condition in OUTCOMES
    }


def visits(center: Center, period: Period) -> QuerySet[Participant]:
    """Every student booked into a (non-placeholder) appointment starting in the period."""
    start, end = period.bounds(center)
    found = Participant.objects.filter(
        appointment__schedule__center=center,
        appointment__starts_at__gte=start,
        appointment__starts_at__lt=end,
        appointment__is_placeholder=False,
    )
    if period.schedule is not None:
        found = found.filter(appointment__schedule=period.schedule)
    return found


def appointments(center: Center, period: Period) -> QuerySet[Appointment]:
    start, end = period.bounds(center)
    found = Appointment.objects.filter(
        schedule__center=center, starts_at__gte=start, starts_at__lt=end
    )
    if period.schedule is not None:
        found = found.filter(schedule=period.schedule)
    return found


KIND = Case(
    When(appointment__is_walk_in=True, then=Value("walk_in")),
    When(appointment__capacity__gt=1, then=Value("group")),
    When(appointment__series_id__isnull=False, then=Value("series")),
    default=Value("single"),
)


def _row(label: str, counts: Mapping[str, Any]) -> list[Any]:
    return [label, *(counts[name] for name, _c in OUTCOMES)]


def build(center: Center, period: Period) -> Report:
    found = visits(center, period)
    tables = [
        _summary(center, period, found),
        Table(
            "visits",
            _("Every visit"),
            outcome_columns(Column("group", _("Visits"))),
            [_row(_("All visits"), found.aggregate(**_counts()))],
        ),
        _by_tutor(found),
        _by_modality(found),
        _by_kind(found),
    ]
    if period.schedule is None:
        tables.append(_by_schedule(found))
    tables.append(_topics(center, period))
    return Report(
        kind="statistics",
        title=_("Statistics"),
        described=period.described(),
        tables=tables,
        meta=period.meta(),
    )


def _summary(center: Center, period: Period, found: QuerySet[Participant]) -> Table:
    held = appointments(center, period).aggregate(
        booked=Count("pk", filter=Q(is_placeholder=False)),
        canceled=Count("pk", filter=Q(is_placeholder=False, status=AppointmentStatus.CANCELED)),
        placeholders=Count("pk", filter=Q(is_placeholder=True, status=AppointmentStatus.SCHEDULED)),
    )
    students = found.aggregate(
        booked=Count("student", distinct=True),
        came=Count("student", distinct=True, filter=CAME),
    )
    first = (
        Participant.objects.filter(
            CAME,
            student=OuterRef("student"),
            appointment__schedule__center=center,
            appointment__is_placeholder=False,
        )
        .order_by("appointment__starts_at", "pk")
        .values("pk")[:1]
    )
    came = (
        found.filter(CAME)
        .annotate(first_visit=Subquery(first))
        .aggregate(total=Count("pk"), first=Count("pk", filter=Q(pk=F("first_visit"))))
    )
    rows = [
        [_("Appointments booked"), held["booked"]],
        [_("Appointments canceled"), held["canceled"]],
        [_("Placeholders (time held without a student)"), held["placeholders"]],
        [_("Students who booked"), students["booked"]],
        [_("Students who came"), students["came"]],
        [_("First visits (a student's first visit to the center)"), came["first"]],
        [_("Return visits"), came["total"] - came["first"]],
    ]
    return Table(
        "summary",
        _("Appointments and students"),
        [Column("measure", _("Measure")), Column("count", _("Count"), numeric=True)],
        rows,
    )


def _by_tutor(found: QuerySet[Participant]) -> Table:
    rows = (
        found.values(
            "appointment__tutor_id",
            "appointment__tutor__full_name",
            "appointment__tutor__preferred_name",
        )
        .annotate(**_counts())
        .order_by("appointment__tutor__full_name", "appointment__tutor_id")
    )
    return Table(
        "by_tutor",
        _("By tutor"),
        outcome_columns(Column("tutor", _("Tutor"))),
        [
            _row(r["appointment__tutor__preferred_name"] or r["appointment__tutor__full_name"], r)
            for r in rows
        ],
    )


def _by_modality(found: QuerySet[Participant]) -> Table:
    labels = dict(Modality.choices)
    rows = (
        found.values("appointment__modality")
        .annotate(**_counts())
        .order_by("appointment__modality")
    )
    return Table(
        "by_modality",
        _("By how they met"),
        outcome_columns(Column("modality", _("How"))),
        [
            _row(str(labels.get(r["appointment__modality"], r["appointment__modality"])), r)
            for r in rows
        ],
    )


def _by_kind(found: QuerySet[Participant]) -> Table:
    labels = kind_labels()
    order = list(labels)
    rows = found.annotate(kind=KIND).values("kind").annotate(**_counts())
    return Table(
        "by_kind",
        _("By kind of appointment"),
        outcome_columns(Column("kind", _("Kind"))),
        [_row(labels[r["kind"]], r) for r in sorted(rows, key=lambda r: order.index(r["kind"]))],
        note=_(
            "Each appointment has one kind, checked in this order: walk-in, group, weekly "
            "series, single booking."
        ),
    )


def _by_schedule(found: QuerySet[Participant]) -> Table:
    rows = (
        found.values("appointment__schedule_id", "appointment__schedule__name")
        .annotate(**_counts())
        .order_by("appointment__schedule__name", "appointment__schedule_id")
    )
    return Table(
        "by_schedule",
        _("By schedule"),
        outcome_columns(Column("schedule", _("Schedule"))),
        [_row(r["appointment__schedule__name"], r) for r in rows],
    )


def final_records(center: Center, period: Period) -> QuerySet[SessionRecord]:
    start, end = period.bounds(center)
    found = SessionRecord.objects.filter(
        center=center, status=RecordStatus.FINAL, held_at__gte=start, held_at__lt=end
    )
    if period.schedule is not None:
        found = found.filter(appointment__schedule=period.schedule)
    return found


def _topics(center: Center, period: Period) -> Table:
    records = final_records(center, period)
    total = records.count()
    axes = dict(Axis.choices)
    axis_order = {axis: i for i, axis in enumerate(axes)}
    rows = (
        RecordTopic.objects.filter(record__in=records)
        .values("topic__axis", "topic__slug", "topic__label", "topic__position")
        .annotate(records=Count("record_id"))
    )
    ordered = sorted(
        rows,
        key=lambda r: (axis_order.get(r["topic__axis"], 99), -r["records"], r["topic__position"]),
    )
    return Table(
        "topics",
        _("Topics in final session records"),
        [
            Column("topic", _("Topic")),
            Column("axis", _("Kind of topic")),
            Column("slug", _("Topic key")),
            Column("records", _("Records tagged"), numeric=True),
        ],
        [
            [r["topic__label"], str(axes.get(r["topic__axis"], "")), r["topic__slug"], r["records"]]
            for r in ordered
        ],
        note=_("%(count)s final session records were held in this period.") % {"count": total},
    )
