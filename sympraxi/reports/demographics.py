"""Session topics by class standing and by course, and across terms.

A center says which of its questions give a student's class standing (first-year,
sophomore and so on) and which a course (`ReportQuestions`, set by a director). They are
chosen by the question's permanent key, so they follow the question through every new
version of its form, and no form has to be edited.

Where each value comes from:

- Class standing: the student's latest answer to that question, from their registration
  form or any intake form. Standing changes, and the latest answer is taken at the time
  the report is run, not as it was on the day of the session.
- Course: the student's answer to that question on the intake form for the appointment
  the record is about; failing that, the course of the record's writing project; failing
  that, none given.

What is counted is final session records held in the period, as in the statistics. Text
answers are tidied (spaces collapsed, courses upper-cased) so "eng  101" and "ENG 101" are
one course; choice answers use the label of the option chosen.

Small groups are left out. A row for fewer than `MIN_STUDENTS` different students isn't
shown, so the report can't point at one person ("the one sophomore who came about
citation"). The tables say how many rows were left out. Totals are not adjusted, so
someone with the totals can still work out that a group was small; the report is for
directors, who can read the records themselves, and this keeps the page and its downloads
safe to pass on.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Answer,
    Form,
    FormKind,
    Option,
    QuestionKind,
)
from sympraxi.records.models import Axis, RecordStatus, RecordTopic, SessionRecord
from sympraxi.reports import statistics
from sympraxi.reports.forms import Period
from sympraxi.reports.models import ReportQuestions
from sympraxi.reports.tables import Column, Report, Table
from sympraxi.scheduling.models import Term
from sympraxi.scheduling.timeutils import date_range_bounds

MIN_STUDENTS = 5
NOT_GIVEN = ""
# The most concern rows one table lists.
MAX_ROWS = 300
# How many terms "across terms" looks back.
TERMS = 8

ANSWERABLE = (QuestionKind.ONE_CHOICE, QuestionKind.SHORT_TEXT)


def settings_for(center: Center) -> ReportQuestions:
    found, _created = ReportQuestions.objects.get_or_create(center=center)
    return found


def candidates(center: Center) -> list[tuple[UUID, str]]:
    """Questions a director can choose from: those on published registration and intake forms."""
    found: dict[UUID, str] = {}
    forms = Form.objects.filter(
        center=center, kind__in=[FormKind.REGISTRATION, FormKind.INTAKE], is_active=True
    )
    for form in forms:
        version = form.published()
        if version is None:
            continue
        for question in version.questions.filter(kind__in=ANSWERABLE).order_by("position"):
            found.setdefault(question.key, f"{form.title}: {question.label}")
    return sorted(found.items(), key=lambda item: item[1].lower())


def _tidy(text: str, *, upper: bool = False) -> str:
    text = " ".join(text.split())
    return text.upper() if upper else text


def _values(answers: QuerySet[Answer], *, upper: bool = False) -> dict[Any, str]:
    """Answer id -> the value it means, with choices turned into their labels."""
    answers = answers.select_related("question")
    option_keys = {key for a in answers if a.question.kind in CHOICE_KINDS for key in a.options}
    labels = {(o.question_id, o.key): o.label for o in Option.objects.filter(key__in=option_keys)}
    result = {}
    for answer in answers:
        if answer.question.kind in CHOICE_KINDS:
            value = "; ".join(
                labels.get((answer.question_id, key), "") for key in answer.options
            ).strip("; ")
        else:
            value = answer.text
        result[answer.pk] = _tidy(value, upper=upper)
    return result


def standing_of(center: Center, key: UUID | None, student_ids: set[Any]) -> dict[Any, str]:
    """Each student's latest answer to the class-standing question."""
    if key is None or not student_ids:
        return {}
    answers = list(
        Answer.objects.filter(key=key, response__center=center, response__student__in=student_ids)
        .select_related("response")
        .order_by("response__student_id", "-response__created_at")
    )
    values = _values(Answer.objects.filter(pk__in=[a.pk for a in answers]))
    latest: dict[Any, str] = {}
    for answer in answers:
        latest.setdefault(answer.response.student_id, values[answer.pk])
    return latest


def course_of(center: Center, key: UUID | None, records: list[SessionRecord]) -> dict[Any, str]:
    """Each record's course: the intake answer for its appointment, else its project's."""
    by_appointment: dict[tuple[Any, Any], str] = {}
    appointments = {r.appointment_id for r in records if r.appointment_id}
    if key is not None and appointments:
        answers = list(
            Answer.objects.filter(
                key=key, response__center=center, response__appointment__in=appointments
            )
            .select_related("response")
            .order_by("response__created_at")
        )
        values = _values(Answer.objects.filter(pk__in=[a.pk for a in answers]), upper=True)
        for answer in answers:  # later answers replace earlier ones
            by_appointment[(answer.response.appointment_id, answer.response.student_id)] = values[
                answer.pk
            ]
    result = {}
    for record in records:
        value = by_appointment.get((record.appointment_id, record.student_id), "")
        if not value and record.project is not None:
            value = _tidy(record.project.course, upper=True)
        result[record.pk] = value
    return result


@dataclass
class Row:
    record: Any
    student: Any
    standing: str
    course: str
    concerns: list[tuple[str, str]]  # (label, slug)


def rows(center: Center, records: QuerySet[SessionRecord]) -> list[Row]:
    settings = settings_for(center)
    found = list(records.select_related("project"))
    standing = standing_of(center, settings.standing_key, {r.student_id for r in found})
    course = course_of(center, settings.course_key, found)
    concerns: dict[Any, list[tuple[str, str]]] = defaultdict(list)
    for item in RecordTopic.objects.filter(record__in=records, topic__axis=Axis.CONCERN).values(
        "record_id", "topic__label", "topic__slug"
    ):
        concerns[item["record_id"]].append((item["topic__label"], item["topic__slug"]))
    return [
        Row(r.pk, r.student_id, standing.get(r.student_id, NOT_GIVEN), course[r.pk], concerns[r.pk])
        for r in found
    ]


def _label(value: str) -> str:
    return value or _("Not given")


def _group(found: list[Row], key: Any) -> dict[Any, tuple[set[Any], int]]:
    """For each value of `key`: the students and how many records."""
    groups: dict[Any, tuple[set[Any], int]] = {}
    for row in found:
        for value in key(row):
            students, count = groups.get(value, (set(), 0))
            students.add(row.student)
            groups[value] = (students, count + 1)
    return groups


def _shown(groups: dict[Any, tuple[set[Any], int]]) -> tuple[list[Any], int]:
    keep = [k for k, (students, _n) in groups.items() if len(students) >= MIN_STUDENTS]
    return keep, len(groups) - len(keep)


def _note(left_out: int) -> str:
    if not left_out:
        return ""
    return _("%(n)s groups with fewer than %(k)s students are left out.") % {
        "n": left_out,
        "k": MIN_STUDENTS,
    }


def _by(found: list[Row], name: str, title: str, label: str, key: Any) -> Table:
    groups = _group(found, lambda r: [key(r)])
    keep, left_out = _shown(groups)
    keep.sort(key=lambda k: (-groups[k][1], _label(k).lower()))
    return Table(
        name,
        title,
        [
            Column("group", label),
            Column("students", _("Students"), numeric=True),
            Column("sessions", _("Sessions"), numeric=True),
        ],
        [[_label(k), len(groups[k][0]), groups[k][1]] for k in keep],
        note=_note(left_out),
    )


def _concerns(found: list[Row]) -> Table:
    groups: dict[tuple[str, str, str, str], tuple[set[Any], int]] = {}
    for row in found:
        for label, slug in row.concerns:
            k = (row.standing, row.course, slug, label)
            students, count = groups.get(k, (set(), 0))
            students.add(row.student)
            groups[k] = (students, count + 1)
    keep, left_out = _shown(groups)
    keep.sort(key=lambda k: (-groups[k][1], k[0], k[1], k[3]))
    shown = keep[:MAX_ROWS]
    note = _note(left_out)
    if len(keep) > MAX_ROWS:
        note = (note + " " if note else "") + _("Only the %(n)s largest rows are listed.") % {
            "n": MAX_ROWS
        }
    return Table(
        "concerns",
        _("Concerns by class standing and course"),
        [
            Column("standing", _("Class standing")),
            Column("course", _("Course")),
            Column("concern", _("Concern")),
            Column("key", _("Topic key")),
            Column("students", _("Students"), numeric=True),
            Column("sessions", _("Sessions"), numeric=True),
        ],
        [[_label(k[0]), _label(k[1]), k[3], k[2], len(groups[k][0]), groups[k][1]] for k in shown],
        note=note,
    )


def _across_terms(center: Center, standing: str, configured: bool) -> Table:
    """Concerns in each of the center's recent terms, for one class standing or for all."""
    terms = list(Term.objects.filter(center=center).order_by("-starts_on")[:TERMS])
    out: list[list[Any]] = []
    left_out = 0
    for term in terms:
        start, end = date_range_bounds(term.starts_on, term.ends_on, ZoneInfo(center.time_zone))
        found = rows(
            center,
            SessionRecord.objects.filter(
                center=center, status=RecordStatus.FINAL, held_at__gte=start, held_at__lt=end
            ),
        )
        if standing:
            found = [r for r in found if r.standing == standing]
        groups: dict[tuple[str, str], tuple[set[Any], int]] = {}
        for row in found:
            for label, slug in row.concerns:
                students, count = groups.get((slug, label), (set(), 0))
                students.add(row.student)
                groups[(slug, label)] = (students, count + 1)
        keep, hidden = _shown(groups)
        left_out += hidden
        keep.sort(key=lambda k: (-groups[k][1], k[1]))
        out.extend([[term.name, k[1], k[0], len(groups[k][0]), groups[k][1]] for k in keep])
    note = _note(left_out)
    if standing and not configured:
        note = _("No question is chosen for class standing yet.")
    return Table(
        "across_terms",
        _("Concerns across terms"),
        [
            Column("term", _("Term")),
            Column("concern", _("Concern")),
            Column("key", _("Topic key")),
            Column("students", _("Students"), numeric=True),
            Column("sessions", _("Sessions"), numeric=True),
        ],
        out,
        note=note,
    )


def standings_seen(center: Center, period: Period) -> list[str]:
    """The class standings found among the period's records, for the filter."""
    found = rows(center, statistics.final_records(center, period))
    return sorted({r.standing for r in found if r.standing}, key=str.lower)


def build(center: Center, period: Period, *, standing: str = "") -> Report:
    configured = settings_for(center)
    found = rows(center, statistics.final_records(center, period))
    if standing:
        found = [r for r in found if r.standing == standing]
    note = ""
    if configured.standing_key is None and configured.course_key is None:
        note = _(
            "No questions are chosen yet for class standing or course, so every row says "
            "Not given, except courses taken from writing projects."
        )
    tables = [
        _by(
            found,
            "standing",
            _("Sessions by class standing"),
            _("Class standing"),
            lambda r: r.standing,
        ),
        _by(found, "course", _("Sessions by course"), _("Course"), lambda r: r.course),
        _concerns(found),
        _across_terms(center, standing, configured.standing_key is not None),
    ]
    tables[0].note = (tables[0].note + " " + note).strip()
    described = period.described()
    if standing:
        described[_("Class standing")] = standing
    meta = period.meta()
    meta["standing"] = standing or None
    return Report(
        kind="by-course",
        title=_("Topics by course and class standing"),
        described=described,
        tables=tables,
        meta=meta,
    )
