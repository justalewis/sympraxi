"""A de-identified file of session data for a research project.

A center that has an IRB protocol can give its directors a file for it. A director records
the protocol number, the investigator and the approval dates (`ResearchSettings`), and the
export works only on days the approval covers. It is a table with one row per final
session record held in the period, and it is built to say nothing about who anyone is:

- No names, emails, student IDs, appointment or record IDs, tutors, or any text a person
  wrote: not a summary, a note, a goal, a project title or a free-text answer.
- Each student is a code, an HMAC of their ID under a random key made for this export and
  never kept. The same student has the same code throughout one file, so a project can follow
  a student across sessions, and a different code in every other export, so two files
  can't be joined and nobody, the center included, can turn a code back into a person.
- No dates. A session is placed by term and week of the term, its length by a band, and its
  order among that student's sessions in the file.
- Class standing and course are kept only where at least `MIN_STUDENTS` different students
  share them. Otherwise the value is "Other". A course is kept only if it looks like a
  course code (letters then digits), so a typed sentence can never get through. A topic used
  by fewer than that many students is reported as its parent topic, or dropped.

What this does not do. It is not a guarantee. A center with a handful of students, a
tutor who remembers one student's unusual combination of topics, or a project that
joins this file to another source can still identify someone, and a student can be
recognized from facts they chose to share elsewhere. FERPA lets a school release
de-identified data if it has made a reasonable determination that no one could be
identified (34 CFR 99.31(b)); this file is built to support that determination, and the
IRB and the registrar make it. The about table says exactly what was generalized.
"""

import hashlib
import hmac
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.records.models import AttachedResource, RecordTopic, SessionRecord
from sympraxi.reports import demographics, statistics
from sympraxi.reports.forms import Period
from sympraxi.reports.models import ResearchSettings
from sympraxi.reports.tables import Column, Report, Table
from sympraxi.scheduling.models import Term

MIN_STUDENTS = demographics.MIN_STUDENTS
OTHER = "Other"
# Letters then digits, optionally a final letter: ENG 101, BIO220, MATH 1010A.
COURSE_CODE = re.compile(r"^[A-Z]{2,6} ?\d{2,4}[A-Z]?$")

BANDS = [(15, "1-15"), (30, "16-30"), (45, "31-45"), (60, "46-60"), (90, "61-90")]
OVER = "91 or more"


def settings_for(center: Center) -> ResearchSettings:
    found, _created = ResearchSettings.objects.get_or_create(center=center)
    return found


def allowed(settings: ResearchSettings, today: date) -> str:
    """Why the export can't be made today, or an empty string if it can."""
    if not settings.protocol_number.strip():
        return _("Record the IRB protocol number first.")
    if settings.approved_on is None or settings.expires_on is None:
        return _("Record the dates the protocol is approved for.")
    if today < settings.approved_on:
        return _("The protocol's approval starts on a later day.")
    if today > settings.expires_on:
        return _("The protocol's approval has ended. Record its renewal to export again.")
    return ""


def pseudonym(key: bytes, student_id: object) -> str:
    return hmac.new(key, str(student_id).encode(), hashlib.sha256).hexdigest()[:12]


def band(minutes: int) -> str:
    for limit, label in BANDS:
        if minutes <= limit:
            return label
    return OVER


@dataclass
class Session:
    student: Any
    held: date
    minutes: int
    modality: str
    standing: str
    course: str
    topics: dict[str, list[tuple[str, str]]]  # axis -> [(slug, label)]
    resources: int


def _modality(rec: SessionRecord) -> str:
    return rec.appointment.modality if rec.appointment is not None else "unbooked"


def sessions(center: Center, period: Period) -> list[Session]:
    records = statistics.final_records(center, period)
    found = list(records.select_related("appointment", "project").order_by("held_at", "pk"))
    base = demographics.rows(center, records)
    by_record = {row.record: row for row in base}
    topics: dict[Any, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for item in RecordTopic.objects.filter(record__in=records).values(
        "record_id", "topic__axis", "topic__slug", "topic__label"
    ):
        topics[item["record_id"]][item["topic__axis"]].append(
            (item["topic__slug"], item["topic__label"])
        )
    resources = {
        row["record_id"]: row["n"]
        for row in AttachedResource.objects.filter(record__in=records)
        .values("record_id")
        .annotate(n=Count("pk"))
    }
    zone = ZoneInfo(center.time_zone)
    return [
        Session(
            student=rec.student_id,
            held=rec.held_at.astimezone(zone).date(),
            minutes=rec.minutes,
            modality=_modality(rec),
            standing=by_record[rec.pk].standing,
            course=by_record[rec.pk].course,
            topics=topics.get(rec.pk, {}),
            resources=resources.get(rec.pk, 0),
        )
        for rec in found
    ]


def _generalize(found: list[Session]) -> dict[str, int]:
    """Replace values shared by too few students, in place. Returns what was changed."""
    changed = {"standing": 0, "course": 0, "topics": 0}

    def students(key: Any) -> dict[Any, set[Any]]:
        groups: dict[Any, set[Any]] = defaultdict(set)
        for s in found:
            groups[key(s)].add(s.student)
        return groups

    # A course is kept only if it looks like a course code.
    for s in found:
        if s.course and not COURSE_CODE.match(s.course):
            s.course = OTHER
            changed["course"] += 1
    groups = students(lambda s: s.standing)
    for s in found:
        if s.standing and len(groups[s.standing]) < MIN_STUDENTS:
            s.standing = OTHER
            changed["standing"] += 1
    groups = students(lambda s: s.course)
    for s in found:
        if s.course and s.course != OTHER and len(groups[s.course]) < MIN_STUDENTS:
            s.course = OTHER
            changed["course"] += 1
    groups = students(lambda s: (s.standing, s.course))
    for s in found:
        if s.course and s.course != OTHER and len(groups[(s.standing, s.course)]) < MIN_STUDENTS:
            s.course = OTHER
            changed["course"] += 1
    groups = students(lambda s: s.standing)
    for s in found:
        if s.standing and s.standing != OTHER and len(groups[s.standing]) < MIN_STUDENTS:
            s.standing = OTHER
            changed["standing"] += 1
    # A topic used by too few students becomes its parent topic, or is dropped.
    used: dict[str, set[Any]] = defaultdict(set)
    for s in found:
        for pairs in s.topics.values():
            for slug, _label in pairs:
                used[slug].add(s.student)
    for s in found:
        for axis, pairs in list(s.topics.items()):
            kept: list[tuple[str, str]] = []
            for slug, label in pairs:
                if len(used[slug]) >= MIN_STUDENTS:
                    kept.append((slug, label))
                    continue
                changed["topics"] += 1
                parent = slug.split(":", 1)[0]
                if parent != slug and len(used.get(parent, ())) >= MIN_STUDENTS:
                    kept.append((parent, parent))
            s.topics[axis] = sorted(set(kept))
    return changed


def build(center: Center, period: Period, *, today: date, key: bytes | None = None) -> Report:
    settings = settings_for(center)
    key = key or os.urandom(32)
    found = sessions(center, period)
    changed = _generalize(found)
    terms = list(Term.objects.filter(center=center))

    def placed(day: date) -> tuple[str, int | None]:
        term = next((t for t in terms if t.starts_on <= day <= t.ends_on), None)
        if term is None:
            return "", None
        return term.name, (day - term.starts_on).days // 7 + 1

    order: dict[Any, int] = defaultdict(int)
    rows: list[list[Any]] = []
    for s in found:
        order[s.student] += 1
        term, week = placed(s.held)
        rows.append(
            [
                pseudonym(key, s.student),
                term,
                week,
                order[s.student],
                band(s.minutes),
                s.modality,
                s.standing,
                s.course,
                "; ".join(slug for slug, _l in s.topics.get("concern", [])),
                "; ".join(slug for slug, _l in s.topics.get("stage", [])),
                "; ".join(slug for slug, _l in s.topics.get("action", [])),
                s.resources,
            ]
        )
    data = Table(
        "sessions",
        _("Sessions"),
        [
            Column("student", _("Student code")),
            Column("term", _("Term")),
            Column("week", _("Week of the term"), numeric=True),
            Column("session", _("Session number for this student"), numeric=True),
            Column("length", _("Length, in minutes")),
            Column("how", _("How it was held")),
            Column("standing", _("Class standing")),
            Column("course", _("Course")),
            Column("concerns", _("Concerns")),
            Column("stages", _("Stages")),
            Column("actions", _("Tutor actions")),
            Column("resources", _("Resources attached"), numeric=True),
        ],
        rows,
    )
    about = Table(
        "about",
        _("About this file"),
        [Column("item", _("Item")), Column("value", _("Value"))],
        [
            [_("IRB protocol"), settings.protocol_number],
            [_("Investigator"), settings.investigator],
            [_("Approved through"), settings.expires_on.isoformat() if settings.expires_on else ""],
            [_("Period"), period.label],
            [_("Made on"), today.isoformat()],
            [_("Sessions"), len(rows)],
            [_("Students"), len({r[0] for r in rows})],
            [_("Smallest group shown"), MIN_STUDENTS],
            [_("Class standings generalized to Other"), changed["standing"]],
            [_("Courses generalized to Other"), changed["course"]],
            [_("Topics generalized or dropped"), changed["topics"]],
            [
                _("Student codes"),
                _(
                    "New in every export. Not kept, so they can't be matched to another file "
                    "or to a person."
                ),
            ],
            [
                _("Not included"),
                _("Names, emails, IDs, dates, tutors and anything a person wrote."),
            ],
        ],
    )
    return Report(
        kind="research",
        title=_("De-identified research export"),
        described=period.described(),
        tables=[data, about],
        meta={**period.meta(), "protocol": settings.protocol_number},
    )
