"""Bring a center's past appointments in from a CSV, so a switch doesn't lose its history.

The file is plain CSV, one past appointment per row, with these columns (headings are
matched ignoring case and spaces; extra columns are ignored):

    date          2025-10-14
    start         14:00
    end           14:45
    tutor_email   tutor@example.edu
    student_email student@example.edu
    status        attended, no show, late cancel, canceled, or blank
    modality      in person or online (blank means in person)

Tutors and students must already be in the center (add them first with the people
import); a row naming anyone else is reported and skipped. Rows land on a hidden
"Imported history" schedule in a term of the same name, so they count toward visit
totals and booking limits without ever appearing for booking. Nothing is emailed, no
reminder is scheduled, and no session record is made: the file has no session notes.

This is the generic format. A real WCONLINE export has not been seen, so turning one
into this layout is a spreadsheet job (docs/install.md, "Importing history").
"""

import csv
import io
import re
import zoneinfo
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Modality,
    Participant,
    Schedule,
    Term,
    TutorAssignment,
    Visibility,
)

TERM_NAME = "Imported history"
REQUIRED = ("date", "start", "end", "tutor_email", "student_email")
STATUSES = {
    "": "",
    "attended": "attended",
    "came": "attended",
    "no show": "no_show",
    "noshow": "no_show",
    "no_show": "no_show",
    "late cancel": "late_cancel",
    "late canceled": "late_cancel",
    "late_cancel": "late_cancel",
    "canceled": "canceled",
    "cancelled": "canceled",
}
MODALITIES = {
    "": Modality.IN_PERSON,
    "in person": Modality.IN_PERSON,
    "in-person": Modality.IN_PERSON,
    "in_person": Modality.IN_PERSON,
    "online": Modality.ONLINE,
}


@dataclass
class Row:
    line: int
    day: date
    start: time
    end: time
    tutor: str
    student: str
    status: str
    modality: str


@dataclass
class Result:
    imported: int = 0
    already: int = 0
    problems: list[str] = field(default_factory=list)
    file_problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.file_problem and not self.problems


def _key(text: str) -> str:
    return re.sub(r"[\s_-]+", "", text).lower()


def _clock(text: str) -> time | None:
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(text.strip().upper(), fmt).time()
        except ValueError:
            continue
    return None


def _day(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def read(data: bytes) -> tuple[list[Row], list[str], str]:
    """Parse the file. Returns rows, problems per bad row, and a problem with the file."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return [], [], "The file is empty."
    names = {_key(h): i for i, h in enumerate(header)}
    missing = [c for c in REQUIRED if _key(c) not in names]
    if missing:
        return [], [], "The file needs these columns: " + ", ".join(missing) + "."

    def cell(values: list[str], name: str) -> str:
        i = names.get(_key(name))
        return values[i].strip() if i is not None and i < len(values) else ""

    rows: list[Row] = []
    problems: list[str] = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        day, start, end = (
            _day(cell(values, "date")),
            _clock(cell(values, "start")),
            _clock(cell(values, "end")),
        )
        status = STATUSES.get(re.sub(r"\s+", " ", cell(values, "status").lower()))
        modality = MODALITIES.get(re.sub(r"\s+", " ", cell(values, "modality").lower()))
        tutor, student = cell(values, "tutor_email").lower(), cell(values, "student_email").lower()
        why = None
        if day is None:
            why = "the date isn't YYYY-MM-DD or MM/DD/YYYY"
        elif start is None or end is None:
            why = "the start or end time isn't like 14:00"
        elif end <= start:
            why = "the end is not after the start"
        elif day > timezone.localdate():
            why = "the date is in the future; this is for past appointments"
        elif status is None:
            why = "the status isn't one of attended, no show, late cancel, canceled"
        elif modality is None:
            why = "the modality isn't in person or online"
        elif "@" not in tutor or "@" not in student:
            why = "the tutor or student email is missing"
        if why or day is None or start is None or end is None or modality is None:
            problems.append(f"Line {line}: {why}.")
            continue
        rows.append(Row(line, day, start, end, tutor, student, status or "", modality))
    return rows, problems, ""


def _schedule(center: Center, first: date, last: date) -> Schedule:
    """The hidden schedule history lands on, widened to cover every date in the file."""
    term = Term.objects.filter(center=center, name=TERM_NAME).first()
    if term is None:
        term = Term.objects.create(center=center, name=TERM_NAME, starts_on=first, ends_on=last)
    else:
        term.starts_on, term.ends_on = min(term.starts_on, first), max(term.ends_on, last)
        term.save(update_fields=["starts_on", "ends_on"])
    schedule = Schedule.objects.filter(center=center, term=term, name=TERM_NAME).first()
    if schedule is None:
        return Schedule.objects.create(
            center=center,
            term=term,
            name=TERM_NAME,
            description="Appointments brought in from an earlier system.",
            starts_on=term.starts_on,
            ends_on=term.ends_on,
            opens_at=time(0),
            closes_at=time(23, 59),
            slot_minutes=5,
            buffer_minutes=0,
            visibility=Visibility.HIDDEN,
        )
    schedule.starts_on, schedule.ends_on = term.starts_on, term.ends_on
    schedule.save(update_fields=["starts_on", "ends_on"])
    return schedule


def _assignment(
    schedule: Schedule, tutor: User, cache: dict[Any, TutorAssignment]
) -> TutorAssignment:
    if tutor.pk not in cache:
        cache[tutor.pk], _ = TutorAssignment.objects.get_or_create(
            schedule=schedule,
            tutor=tutor,
            defaults={
                "min_minutes": 5,
                "max_minutes": 600,
                "offers_in_person": True,
                "offers_online": True,
                "is_active": False,
            },
        )
    return cache[tutor.pk]


def run(center: Center, data: bytes, *, dry_run: bool = False) -> Result:
    """Import the file's rows. A row that can't be placed is reported; the others still go in."""
    rows, problems, file_problem = read(data)
    result = Result(problems=problems, file_problem=file_problem)
    if file_problem or not rows:
        return result
    tz = zoneinfo.ZoneInfo(center.time_zone)

    def people(role: Role) -> dict[str, User]:
        return {
            m.user.email.lower(): m.user
            for m in Membership.objects.filter(center=center, role=role).select_related("user")
        }

    tutors, students = people(Role.TUTOR), people(Role.STUDENT)
    usable: list[tuple[Row, User, User]] = []
    for row in rows:
        if row.tutor not in tutors:
            result.problems.append(f"Line {row.line}: {row.tutor} isn't a tutor in this center.")
        elif row.student not in students:
            result.problems.append(
                f"Line {row.line}: {row.student} isn't a student in this center."
            )
        else:
            usable.append((row, tutors[row.tutor], students[row.student]))
    if dry_run or not usable:
        result.imported = len(usable) if dry_run else 0
        return result

    with transaction.atomic():
        schedule = _schedule(
            center, min(r.day for r, _, _ in usable), max(r.day for r, _, _ in usable)
        )
        cache: dict[Any, TutorAssignment] = {}
        for row, tutor, student in usable:
            starts = datetime.combine(row.day, row.start, tzinfo=tz)
            ends = datetime.combine(row.day, row.end, tzinfo=tz)
            if Participant.objects.filter(
                appointment__schedule=schedule,
                appointment__tutor=tutor,
                appointment__starts_at=starts,
                student=student,
            ).exists():
                result.already += 1
                continue
            canceled = row.status in ("canceled", "late_cancel")
            try:
                with transaction.atomic():
                    appointment = Appointment.objects.create(
                        schedule=schedule,
                        assignment=_assignment(schedule, tutor, cache),
                        tutor=tutor,
                        starts_at=starts,
                        ends_at=ends,
                        blocked_until=ends,
                        modality=row.modality,
                        status=AppointmentStatus.CANCELED
                        if canceled
                        else AppointmentStatus.SCHEDULED,
                        canceled_at=starts
                        - (timedelta(days=1) if row.status == "canceled" else timedelta(hours=1))
                        if canceled
                        else None,
                    )
                    Participant.objects.create(
                        appointment=appointment,
                        student=student,
                        canceled_at=appointment.canceled_at,
                        late_cancel=row.status == "late_cancel",
                        attendance={
                            "attended": Attendance.ATTENDED,
                            "no_show": Attendance.NO_SHOW,
                        }.get(row.status, Attendance.UNMARKED),
                    )
            except IntegrityError:
                result.problems.append(
                    f"Line {row.line}: {row.tutor} already has another appointment at that time."
                )
                continue
            result.imported += 1
        record(
            AuditAction.HISTORY_IMPORTED,
            center=center,
            actor_label="command line",
            imported=result.imported,
            already=result.already,
            skipped=len(result.problems),
        )
    return result
