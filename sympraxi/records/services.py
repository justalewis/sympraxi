"""Writing, finalizing and amending session records. Every rule is checked here.

Views call these functions and show `RecordError.message` when one refuses.
Permission checks stay in the views (through `sympraxi.core.policy`); these
functions check the record's own rules: who it's about, whether it's final,
and what finalizing needs.

A final record changes only through `amend()`, which writes an `Amendment`
holding each changed field before and after, then opens that amendment for the
rest of the transaction so the database trigger lets the change through. The
answers to the center's session-report questions are part of the record's
content (`Content.answers`) and follow the same rules (sympraxi/forms/report.py).
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.forms import report
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import notifications
from sympraxi.records.models import (
    Amendment,
    AttachedResource,
    Axis,
    RecordStatus,
    Resource,
    SessionRecord,
    Topic,
    WritingProject,
)
from sympraxi.records.signals import record_finalized
from sympraxi.scheduling import attendance
from sympraxi.scheduling.models import Appointment, AppointmentStatus


class RecordError(Exception):
    """A rule refused the request. `message` is written for the person who asked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Content:
    """Everything a tutor writes in a record. Saving replaces the record's content."""

    minutes: int
    staff_notes: str = ""
    summary: str = ""
    next_steps: str = ""
    topics: Sequence[Topic] = ()
    project: WritingProject | None = None
    # (resource, note to the student), in the order shown.
    resources: Sequence[tuple[Resource, str]] = field(default_factory=list)
    # Answers to the center's session-report questions. None leaves the record's
    # answers as they are.
    answers: report.ReportAnswers | None = None

    @classmethod
    def of(cls, rec: SessionRecord) -> "Content":
        return cls(
            minutes=rec.minutes,
            staff_notes=rec.staff_notes,
            summary=rec.summary,
            next_steps=rec.next_steps,
            topics=list(rec.topics.all()),
            project=rec.project,
            resources=[(a.resource, a.note) for a in rec.attachments.select_related("resource")],
            answers=report.answers_of(rec),
        )


def active_students(appointment: Appointment) -> list[User]:
    return list(
        User.objects.filter(
            participations__appointment=appointment,
            participations__canceled_at__isnull=True,
        ).order_by("full_name", "email")
    )


# --- Starting a record ----------------------------------------------------------


def start_for_appointment(
    *, actor: User, appointment: Appointment, student: User, request: HttpRequest | None = None
) -> SessionRecord:
    """The record for one student in an appointment, started as a draft if there is none."""
    if appointment.status == AppointmentStatus.CANCELED:
        raise RecordError("canceled", _("This appointment was canceled, so it has no record."))
    if student not in active_students(appointment):
        raise RecordError("not_in_it", _("That student isn't in this appointment."))
    existing = SessionRecord.objects.filter(appointment=appointment, student=student).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            rec = SessionRecord.objects.create(
                center=appointment.schedule.center,
                appointment=appointment,
                student=student,
                tutor=appointment.tutor,
                held_at=appointment.starts_at,
                minutes=appointment.minutes,
                created_by=actor,
            )
            _audit(AuditAction.RECORD_STARTED, actor, rec, request)
    except IntegrityError:
        # Someone else started it a moment ago.
        return SessionRecord.objects.get(appointment=appointment, student=student)
    return rec


def start_standalone(
    *,
    actor: User,
    center: Any,
    student: User,
    held_at: datetime,
    minutes: int,
    request: HttpRequest | None = None,
) -> SessionRecord:
    """A record for a meeting that wasn't booked. The person writing it held the session."""
    with transaction.atomic():
        rec = SessionRecord.objects.create(
            center=center,
            student=student,
            tutor=actor,
            held_at=held_at,
            minutes=minutes,
            created_by=actor,
        )
        _audit(AuditAction.RECORD_STARTED, actor, rec, request, off_schedule=True)
    return rec


# --- Drafts and finalizing ------------------------------------------------------


def save_draft(
    *, actor: User, rec: SessionRecord, content: Content, request: HttpRequest | None = None
) -> SessionRecord:
    with transaction.atomic():
        rec = _lock(rec)
        if rec.is_final:
            raise RecordError("final", _("This record is final. Changes need an amendment."))
        _check_project(rec, content.project)
        _apply(rec, content, actor)
        _audit(AuditAction.RECORD_SAVED, actor, rec, request)
    return rec


def finalize(
    *,
    actor: User,
    rec: SessionRecord,
    content: Content | None = None,
    request: HttpRequest | None = None,
    now: datetime | None = None,
    notify: bool = True,
) -> SessionRecord:
    """Save the content (if given) and make the record final. The student is emailed."""
    now = now or timezone.now()
    with transaction.atomic():
        rec = _lock(rec)
        if rec.is_final:
            raise RecordError("final", _("This record is already final."))
        if content is not None:
            _check_project(rec, content.project)
            _apply(rec, content, actor)
        problems = finalize_problems(rec, now=now)
        if problems:
            raise RecordError("incomplete", " ".join(problems))
        rec.status = RecordStatus.FINAL
        rec.finalized_at = now
        rec.finalized_by = actor
        rec.save(update_fields=["status", "finalized_at", "finalized_by", "updated_at"])
        _audit(AuditAction.RECORD_FINALIZED, actor, rec, request)
        # A final record means the session happened, so the student came.
        attendance.record_came(rec, actor=actor, now=now, request=request)
        record_finalized.send(sender=SessionRecord, record=rec)
    if notify:
        base_url = notifications.base_url(request)
        record_id = rec.pk
        transaction.on_commit(
            lambda: notifications.send_summary_ready(record_id, base_url=base_url)
        )
    return rec


def finalize_problems(rec: SessionRecord, *, now: datetime | None = None) -> list[str]:
    """What still stops the record from being finalized, in words for the tutor."""
    now = now or timezone.now()
    problems = []
    if not rec.summary.strip():
        problems.append(_("Write a summary for the student."))
    if not rec.topics.filter(axis=Axis.CONCERN).exists():
        problems.append(_("Tag at least one concern."))
    problems.extend(report.problems(rec))
    if rec.held_at > now:
        problems.append(_("The session hasn't started yet. Finalize the record after it."))
    return problems


# --- Amendments -----------------------------------------------------------------


def amend(
    *,
    actor: User,
    rec: SessionRecord,
    content: Content,
    reason: str,
    request: HttpRequest | None = None,
) -> Amendment:
    """Change a final record, keeping what it said before, who changed it and why."""
    reason = reason.strip()
    if not reason:
        raise RecordError("no_reason", _("Say why you're changing the record."))
    with transaction.atomic():
        rec = _lock(rec)
        if not rec.is_final:
            raise RecordError("draft", _("This record is still a draft. Edit it directly."))
        _check_project(rec, content.project)
        current = Content.of(rec)
        before = snapshot(current)
        after = snapshot(content)
        changes: dict[str, Any] = {
            name: {"before": before[name], "after": after[name]}
            for name in before
            if before[name] != after[name]
        }
        if content.answers is not None:
            if current.answers is not None and content.answers.version != current.answers.version:
                raise RecordError(
                    "version", _("The answers changed while you were editing. Try again.")
                )
            changes.update(report.changes(current.answers, content.answers))
        if not changes:
            raise RecordError("unchanged", _("Nothing changed, so there's nothing to amend."))
        if not content.summary.strip():
            raise RecordError("incomplete", _("A final record needs a summary for the student."))
        if not any(t.axis == Axis.CONCERN for t in content.topics):
            raise RecordError("incomplete", _("A final record needs at least one concern."))
        if content.answers is not None and current.answers is not None:
            missing = report.missing(content.answers.version, content.answers.values)
            if missing:
                raise RecordError("incomplete", " ".join(missing))
        amendment = Amendment.objects.create(
            record=rec, author=actor, reason=reason, changes=changes
        )
        with _amendment_open(amendment):
            _apply(rec, content, actor)
        _audit(AuditAction.RECORD_AMENDED, actor, rec, request, fields=sorted(changes))
    return amendment


def snapshot(content: Content) -> dict[str, Any]:
    """The content as plain JSON, for comparing and for an amendment's history."""
    project = content.project
    return {
        "minutes": content.minutes,
        "staff_notes": content.staff_notes,
        "summary": content.summary,
        "next_steps": content.next_steps,
        "topics": sorted(t.slug for t in content.topics),
        "project": {"id": str(project.pk), "title": project.title} if project else None,
        "resources": [
            {
                "id": resource.catalog_id or str(resource.pk),
                "title": resource.title,
                "note": note,
            }
            for resource, note in content.resources
        ],
    }


@contextmanager
def _amendment_open(amendment: Amendment) -> Iterator[None]:
    """Let this transaction change the amended record (see migration 0002)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('sympraxi.amendment', %s, true)", [str(amendment.pk)])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('sympraxi.amendment', '', true)")


# --- Group appointments ---------------------------------------------------------


def copy_to_group(
    *, actor: User, rec: SessionRecord, request: HttpRequest | None = None
) -> list[SessionRecord]:
    """Start drafts for the other students in the group from this record.

    Each copy is a separate record about one student, finalized on its own after
    the tutor reads it. Staff notes and the writing project stay behind: notes are
    about one student, and each student has their own projects. Answers to the
    center's session-report questions are copied, like the summary.
    """
    appointment = rec.appointment
    if appointment is None:
        return []
    content = Content.of(rec)
    copied = []
    with transaction.atomic():
        for student in active_students(appointment):
            if student.pk == rec.student_id:
                continue
            if SessionRecord.objects.filter(appointment=appointment, student=student).exists():
                continue
            other = SessionRecord.objects.create(
                center=rec.center,
                appointment=appointment,
                student=student,
                tutor=rec.tutor,
                held_at=rec.held_at,
                minutes=content.minutes,
                copied_from=rec,
                created_by=actor,
            )
            _apply(
                other,
                Content(
                    minutes=content.minutes,
                    summary=content.summary,
                    next_steps=content.next_steps,
                    topics=content.topics,
                    resources=content.resources,
                    answers=content.answers,
                ),
                actor,
            )
            _audit(AuditAction.RECORD_STARTED, actor, other, request, copied_from=str(rec.pk))
            copied.append(other)
    return copied


# --- Writing projects -----------------------------------------------------------


def new_project(
    *,
    actor: User,
    center: Any,
    student: User,
    title: str,
    course: str = "",
    instructor: str = "",
    due_on: Any = None,
    request: HttpRequest | None = None,
) -> WritingProject:
    project = WritingProject.objects.create(
        center=center,
        student=student,
        title=title,
        course=course,
        instructor=instructor,
        due_on=due_on,
        created_by=actor,
    )
    record(
        AuditAction.PROJECT_SAVED,
        request=request,
        actor=actor,
        center=center,
        target=project,
        student=str(student.pk),
        created=True,
    )
    return project


# --- Helpers --------------------------------------------------------------------


def _lock(rec: SessionRecord) -> SessionRecord:
    return (
        SessionRecord.objects.select_for_update(of=("self",))
        .select_related("center", "appointment", "student", "tutor", "project")
        .get(pk=rec.pk)
    )


def _check_project(rec: SessionRecord, project: WritingProject | None) -> None:
    if project is not None and (
        project.student_id != rec.student_id or project.center_id != rec.center_id
    ):
        raise RecordError("project", _("Choose one of this student's projects."))


def _apply(rec: SessionRecord, content: Content, actor: User) -> None:
    """Replace the record's content, topics and attachments with `content`."""
    rec.minutes = content.minutes
    rec.staff_notes = content.staff_notes
    rec.summary = content.summary
    rec.next_steps = content.next_steps
    rec.project = content.project
    rec.save()
    wanted = {t.pk for t in content.topics}
    current = set(rec.topics.values_list("pk", flat=True))
    if wanted != current:
        rec.topics.set(list(content.topics))

    attachments = {a.resource_id: a for a in rec.attachments.all()}
    keep = set()
    for position, (resource, note) in enumerate(content.resources):
        keep.add(resource.pk)
        attached = attachments.get(resource.pk)
        if attached is None:
            AttachedResource.objects.create(
                record=rec, resource=resource, note=note, position=position, added_by=actor
            )
        elif attached.note != note or attached.position != position:
            attached.note = note
            attached.position = position
            attached.save(update_fields=["note", "position"])
    gone = [pk for pk in attachments if pk not in keep]
    if gone:
        AttachedResource.objects.filter(record=rec, resource_id__in=gone).delete()

    if content.answers is not None:
        report.write(rec, content.answers, actor)


def _audit(
    action: AuditAction,
    actor: User,
    rec: SessionRecord,
    request: HttpRequest | None,
    **details: Any,
) -> None:
    record(
        action,
        request=request,
        actor=actor,
        center=rec.center,
        target=rec,
        student=str(rec.student_id),
        appointment=str(rec.appointment_id) if rec.appointment_id else "",
        **details,
    )
