"""Everything a center keeps, in one ZIP file, for its directors.

A director asks for it on the export page; the task worker builds it
(`sympraxi/reports/tasks.py`) and the director downloads it from a link that
works for `LINK_HOURS`. Then the scheduler deletes the file (`expire`).

What's inside, as JSON (and CSV for the flat tables people open in a
spreadsheet), with `manifest.json` saying what each file holds:

- the center's settings, its people and their roles, terms, closures, rooms,
  schedules with each tutor's assignment, weekly hours and changes to them;
- every appointment with each student in it and their attendance, exemptions
  and booking blocks;
- writing projects, and every session record, drafts too, with staff notes,
  topics, resources, the tutor's report answers and every amendment;
- the forms with each version's questions and option keys, and every
  registration and intake response;
- survey results only as the results page shows them: counted per term, only
  for terms with at least five responses, written answers sorted. No survey
  response, its ID or its date is ever listed, because the order and dates
  could tie an anonymous answer to the student who gave it;
- the center's own resources and its audit log.

This is the center's own copy of its records, for its directors only: the
policy grants `reports.export_center` to directors and never to site
administrators, who don't read records. Asking for it and each download are
audited. Nothing about other centers is included.

The file is built inside one read-only REPEATABLE READ transaction, so it is a
consistent picture of one moment, and written to a temporary file as it goes:
rows are read in chunks, never a whole table at once.
"""

import io
import json
import logging
import tempfile
import zipfile
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import IO, Any
from uuid import UUID

from django.core.files import File
from django.core.files.storage import Storage, storages
from django.db import connection, models, transaction
from django.db.models import Prefetch
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership
from sympraxi.core.logs import scrub
from sympraxi.forms import export as form_export
from sympraxi.forms import surveys
from sympraxi.forms.models import Form, FormKind, FormVersion, Response
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export as record_export
from sympraxi.records.models import AttachedResource, Resource, SessionRecord, WritingProject
from sympraxi.reports.models import UNFINISHED, CenterExport, ExportStatus
from sympraxi.reports.tables import write_csv_rows
from sympraxi.scheduling.models import (
    Appointment,
    AvailabilityException,
    Block,
    Exemption,
    Participant,
    Room,
    Schedule,
    Term,
    TutorAssignment,
    WeeklyHours,
)

logger = logging.getLogger("sympraxi.reports")

FORMAT = "sympraxi.center-export"
VERSION = 1
# How long the download link works once the file is ready.
LINK_HOURS = 24
# An export still queued or building after this long is marked failed (its
# worker stopped), so the center can ask for another.
STUCK_AFTER = timedelta(hours=6)
CHUNK = 500


def storage() -> Storage:
    return storages["exports"]


# --- Values -----------------------------------------------------------------------


def plain(value: Any) -> Any:
    """A database value as JSON."""
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


def columns_of(obj: models.Model, *, leave_out: tuple[str, ...] = ()) -> dict[str, Any]:
    """Every column of a row, by its column name (`schedule_id`, not `schedule`)."""
    return {
        f.attname: plain(getattr(obj, f.attname))
        for f in obj._meta.concrete_fields
        if f.attname not in leave_out
    }


def person(user: User | None) -> dict[str, str] | None:
    if user is None:
        return None
    return {"id": str(user.pk), "name": user.display_name, "email": user.email}


# --- Writing ----------------------------------------------------------------------


class JsonList:
    """A JSON array written one item at a time into the ZIP."""

    def __init__(self, archive: zipfile.ZipFile, name: str) -> None:
        self.name = name
        self.stream = io.TextIOWrapper(
            archive.open(name, "w", force_zip64=True), encoding="utf-8", newline="\n"
        )
        self.count = 0
        self.stream.write("[")

    def add(self, item: Any, *, scrubbed: bool = False) -> None:
        text = json.dumps(item, ensure_ascii=False, default=str)
        self.stream.write(",\n" if self.count else "\n")
        self.stream.write(scrub(text) if scrubbed else text)
        self.count += 1

    def close(self) -> None:
        self.stream.write("\n]\n" if self.count else "]\n")
        self.stream.close()


class Writer:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.counts: dict[str, int] = {}

    def items(self, name: str, rows: Any, *, scrubbed: bool = False) -> None:
        out = JsonList(self.archive, name)
        for row in rows:
            out.add(row, scrubbed=scrubbed)
        out.close()
        self.counts[name] = out.count

    def document(self, name: str, data: Any) -> None:
        with self.archive.open(name, "w") as raw:
            raw.write(json.dumps(data, indent=2, ensure_ascii=False, default=str).encode())

    def csv(self, name: str, header: list[str], rows: Any, *, scrubbed: bool = False) -> None:
        count = 0
        with (
            self.archive.open(name, "w", force_zip64=True) as raw,
            io.TextIOWrapper(raw, encoding="utf-8", newline="") as out,
        ):
            out.write("\ufeff")
            write_csv_rows(out, [header])
            for row in rows:
                if scrubbed:
                    row = [scrub(v) if isinstance(v, str) else v for v in row]
                write_csv_rows(out, [row])
                count += 1
        self.counts[name] = count


# --- What goes in -------------------------------------------------------------------


def _people(center: Center) -> Any:
    for m in (
        Membership.objects.filter(center=center)
        .select_related("user")
        .order_by("user__full_name", "role", "pk")
    ):
        user = m.user
        yield {
            "membership_id": str(m.pk),
            "user_id": str(user.pk),
            "full_name": user.full_name,
            "preferred_name": user.preferred_name,
            "pronouns": user.pronouns,
            "email": user.email,
            "student_id": user.student_id or "",
            "role": m.role,
            "active": m.is_active,
            "account_active": user.is_active,
            "since": plain(m.created_at),
        }


def _terms(center: Center) -> Any:
    for term in (
        Term.objects.filter(center=center).prefetch_related("closures").order_by("starts_on", "pk")
    ):
        yield {
            **columns_of(term),
            "closures": [columns_of(c) for c in term.closures.all()],
        }


def _schedules(center: Center) -> Any:
    assignments = TutorAssignment.objects.select_related("tutor").prefetch_related(
        Prefetch("weekly_hours", queryset=WeeklyHours.objects.order_by("weekday", "starts_at")),
        Prefetch("exceptions", queryset=AvailabilityException.objects.order_by("starts_at")),
    )
    for schedule in (
        Schedule.objects.filter(center=center)
        .select_related("term")
        .prefetch_related(Prefetch("assignments", queryset=assignments))
        .order_by("starts_on", "name", "pk")
    ):
        yield {
            **columns_of(schedule),
            "term": schedule.term.name,
            "tutors": [
                {
                    **columns_of(a),
                    "tutor": person(a.tutor),
                    "weekly_hours": [
                        columns_of(h, leave_out=("assignment_id",)) for h in a.weekly_hours.all()
                    ],
                    "changes": [
                        columns_of(e, leave_out=("assignment_id",)) for e in a.exceptions.all()
                    ],
                }
                for a in schedule.assignments.all()
            ],
        }


def _appointments(center: Center) -> Any:
    return (
        Appointment.objects.filter(schedule__center=center)
        .select_related("schedule", "tutor", "room")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.select_related("student").order_by("added_at", "pk"),
            )
        )
        .order_by("starts_at", "pk")
        .iterator(chunk_size=CHUNK)
    )


def _appointment(a: Appointment) -> dict[str, Any]:
    return {
        **columns_of(a),
        "schedule": a.schedule.name,
        "tutor": person(a.tutor),
        "room": a.room.name if a.room else "",
        "students": [
            {**columns_of(p, leave_out=("appointment_id",)), "student": person(p.student)}
            for p in a.participants.all()
        ],
    }


APPOINTMENT_CSV = [
    "appointment_id",
    "starts_at",
    "ends_at",
    "schedule",
    "tutor",
    "tutor_email",
    "modality",
    "room",
    "status",
    "walk_in",
    "placeholder",
    "tentative",
    "capacity",
    "series_id",
    "student",
    "student_email",
    "student_id",
    "left_at",
    "late_cancellation",
    "attendance",
    "waived_at",
]


def _appointment_rows(center: Center) -> Any:
    for a in _appointments(center):
        base = [
            str(a.pk),
            a.starts_at,
            a.ends_at,
            a.schedule.name,
            a.tutor.display_name,
            a.tutor.email,
            a.modality,
            a.room.name if a.room else "",
            a.status,
            a.is_walk_in,
            a.is_placeholder,
            a.is_tentative,
            a.capacity,
            str(a.series_id) if a.series_id else "",
        ]
        people = list(a.participants.all())
        if not people:
            yield [*base, "", "", "", None, False, "", None]
        for p in people:
            yield [
                *base,
                p.student.display_name,
                p.student.email,
                p.student.student_id or "",
                p.canceled_at,
                p.late_cancel,
                p.attendance,
                p.waived_at,
            ]


def _records(center: Center) -> Any:
    found = (
        SessionRecord.objects.filter(center=center)
        .select_related("tutor", "student", "created_by", "finalized_by", "project")
        .prefetch_related(
            "topics",
            Prefetch(
                "attachments",
                queryset=AttachedResource.objects.select_related("resource", "added_by"),
            ),
            "amendments__author",
            "report_responses__version",
            "report_responses__form",
            "report_responses__answered_by",
            "report_responses__answers__question__options",
        )
        .order_by("held_at", "created_at", "pk")
    )
    for rec in found.iterator(chunk_size=CHUNK):
        yield {**record_export._record(rec), "student": person(rec.student)}


def _forms(center: Center) -> Any:
    versions = FormVersion.objects.order_by("number").prefetch_related("questions__options")
    for form in (
        Form.objects.filter(center=center)
        .select_related("schedule")
        .prefetch_related(Prefetch("versions", queryset=versions))
        .order_by("kind", "created_at", "pk")
    ):
        yield {
            "id": str(form.pk),
            "kind": form.kind,
            "title": form.title,
            "schedule_id": str(form.schedule_id) if form.schedule_id else None,
            "in_use": form.is_active,
            "versions": [
                {
                    "number": v.number,
                    "intro": v.intro,
                    "published_at": plain(v.published_at),
                    "questions": [
                        {
                            "key": str(q.key),
                            "position": q.position,
                            "kind": q.kind,
                            "label": q.label,
                            "hint": q.help_text,
                            "required": q.required,
                            "demographic": q.is_demographic,
                            "shown_to_staff": q.shown_to_staff,
                            "shown_to_student": q.shown_to_student,
                            "show_if_question": plain(q.show_if_question),
                            "show_if_option": plain(q.show_if_option),
                            "options": [
                                {"key": str(o.key), "label": o.label}
                                for o in sorted(q.options.all(), key=lambda o: o.position)
                            ],
                        }
                        for q in sorted(v.questions.all(), key=lambda q: q.position)
                    ],
                }
                for v in form.versions.all()
            ],
        }


def _responses(center: Center) -> Any:
    """Registration and intake answers. Session-report answers are with their records;
    survey responses are never listed one by one."""
    found = (
        Response.objects.filter(
            center=center, form__kind__in=[FormKind.REGISTRATION, FormKind.INTAKE]
        )
        .select_related("version", "answered_by", "form__schedule", "student")
        .prefetch_related("answers__question__options")
        .order_by("created_at", "pk")
    )
    for response in found.iterator(chunk_size=CHUNK):
        yield {
            **form_export.response_data(response),
            "kind": response.form.kind,
            "form_id": str(response.form_id),
            "student": person(response.student),
            "appointment_id": str(response.appointment_id) if response.appointment_id else None,
        }


def _survey_results(center: Center) -> Any:
    periods = surveys.periods(center)
    for form in Form.objects.filter(center=center, kind=FormKind.SURVEY).order_by(
        "created_at", "pk"
    ):
        for period in periods:
            found = surveys.results(form, period)
            item: dict[str, Any] = {
                "survey": form.title,
                "term": period.term.name,
                "counted_from": period.starts_on.isoformat(),
                "counted_before": period.ends_before.isoformat() if period.ends_before else None,
                "shown": found.shown,
            }
            if found.shown:
                item["responses"] = found.responses
                item["questions"] = [asdict(q) for q in found.questions]
            yield item


def _audit(center: Center) -> Any:
    return (
        AuditEvent.objects.filter(center_id=center.pk)
        .order_by("occurred_at", "id")
        .iterator(chunk_size=CHUNK)
    )


def _audit_item(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.pk,
        "occurred_at": plain(e.occurred_at),
        "action": e.action,
        "what": e.action_label,
        "actor_id": str(e.actor_id) if e.actor_id else None,
        "actor": e.actor_label,
        "target_type": e.target_type,
        "target_id": e.target_id,
        "ip_address": e.ip_address,
        "user_agent": e.user_agent,
        "details": e.details,
    }


AUDIT_CSV = [
    "id",
    "occurred_at",
    "action",
    "what",
    "actor",
    "target_type",
    "target_id",
    "ip_address",
]


def _audit_rows(center: Center) -> Any:
    for e in _audit(center):
        yield [
            e.pk,
            e.occurred_at,
            e.action,
            e.action_label,
            e.actor_label,
            e.target_type,
            e.target_id,
            e.ip_address or "",
        ]


def _center(center: Center) -> dict[str, Any]:
    return columns_of(center)


def write(export: CenterExport, out: IO[bytes], *, now: datetime) -> dict[str, int]:
    """Write the whole export as a ZIP into `out`. Returns how many items each file holds."""
    center = export.center
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        w = Writer(archive)
        w.document("center.json", _center(center))
        w.items("people.json", _people(center))
        w.items("terms.json", _terms(center))
        w.items(
            "rooms.json",
            (columns_of(r) for r in Room.objects.filter(center=center).order_by("name")),
        )
        w.items("schedules.json", _schedules(center))
        w.items("appointments.json", (_appointment(a) for a in _appointments(center)))
        w.csv("appointments.csv", APPOINTMENT_CSV, _appointment_rows(center))
        w.items(
            "exemptions.json",
            (
                columns_of(x)
                for x in Exemption.objects.filter(center=center).order_by("created_at", "pk")
            ),
        )
        w.items(
            "booking_blocks.json",
            (
                columns_of(b)
                for b in Block.objects.filter(center=center).order_by("created_at", "pk")
            ),
        )
        w.items(
            "writing_projects.json",
            (
                columns_of(p)
                for p in WritingProject.objects.filter(center=center).order_by("created_at", "pk")
            ),
        )
        w.items("session_records.json", _records(center))
        w.items("forms.json", _forms(center))
        w.items("form_responses.json", _responses(center))
        w.items("survey_results.json", _survey_results(center))
        w.items(
            "resources.json",
            (
                {**columns_of(r), "topics": sorted(t.slug for t in r.topics.all())}
                for r in Resource.objects.filter(center=center)
                .prefetch_related("topics")
                .order_by("title", "pk")
            ),
        )
        w.items("audit_events.json", (_audit_item(e) for e in _audit(center)), scrubbed=True)
        w.csv("audit_events.csv", AUDIT_CSV, _audit_rows(center), scrubbed=True)
        w.document(
            "manifest.json",
            {
                "format": FORMAT,
                "version": VERSION,
                "exported_at": now.isoformat(),
                "requested_at": export.requested_at.isoformat(),
                "requested_by": person(export.requested_by),
                "center": {"name": center.name, "slug": center.slug, "time_zone": center.time_zone},
                "files": w.counts,
                "notes": [
                    "Times are in UTC; the center's time zone is given above.",
                    "Survey results are counted per term and only for terms with at least "
                    f"{surveys.MIN_RESPONSES} responses. Single survey responses are never listed.",
                    "Session-report answers are inside each session record.",
                    "Nothing about other centers is included.",
                ],
            },
        )
    return w.counts


def file_name(export: CenterExport) -> str:
    return f"{export.center_id}/{export.pk}.zip"


def build(export: CenterExport) -> None:
    """Build the file and mark the export ready. Run by the task worker."""
    now = timezone.now()
    # Started outside a transaction (the worker), the export reads one snapshot. Run
    # inside one (the tests' immediate task backend), it reads that transaction's.
    snapshot = not connection.in_atomic_block
    with tempfile.TemporaryFile() as out:
        with transaction.atomic():
            if snapshot:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            counts = write(export, out, now=now)
        size = out.tell()
        out.seek(0)
        saved = storage().save(file_name(export), File(out))
    ready = timezone.now()
    CenterExport.objects.filter(pk=export.pk).update(
        status=ExportStatus.READY,
        file_name=saved,
        size=size,
        counts=counts,
        ready_at=ready,
        expires_at=ready + timedelta(hours=LINK_HOURS),
    )


def expire(now: datetime) -> int:
    """Delete the files of exports whose links have expired. Returns how many."""
    expired = 0
    for export in CenterExport.objects.filter(status=ExportStatus.READY, expires_at__lte=now):
        storage().delete(export.file_name)
        expired += CenterExport.objects.filter(pk=export.pk, status=ExportStatus.READY).update(
            status=ExportStatus.EXPIRED, file_name=""
        )
    CenterExport.objects.filter(status__in=UNFINISHED, requested_at__lte=now - STUCK_AFTER).update(
        status=ExportStatus.FAILED
    )
    return expired
