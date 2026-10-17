"""Everything a center keeps about one student, for a FERPA inspection request.

34 CFR 99.10 gives students the right to inspect their education records. The
export covers the student's account details, their appointments, every
session record about them (drafts too, with staff notes and every amendment),
attached resources with the tutors' notes, and their writing projects.
Appointments include attendance: no-shows, late cancellations and waivers with
their reasons, and every set of intake answers given for them. The student's
registration answers are included, earlier ones too (sympraxi/forms/export.py).
Booking blocks are listed with any director's note.

Other students are left out. A group appointment shows how many students it
had, never who they were (99.12: when a record holds information about more
than one student, a student sees only the part about them).
"""

from datetime import date, datetime
from typing import Any

from django.db.models import Count, QuerySet
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Membership
from sympraxi.forms import export as form_export
from sympraxi.records.models import SessionRecord, WritingProject
from sympraxi.scheduling.models import Block, Participant

FORMAT = "sympraxi.student-record"
# Version 2 added registration answers and each appointment's intake answers.
VERSION = 2


def build(membership: Membership, *, exported_by: User) -> dict[str, Any]:
    center = membership.center
    student = membership.user
    intake = form_export.intake_by_appointment(membership)
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": _when(timezone.now()),
        "exported_by": _person(exported_by),
        "center": {"name": center.name, "slug": center.slug, "time_zone": center.time_zone},
        "student": {
            "id": str(student.pk),
            "full_name": student.full_name,
            "preferred_name": student.preferred_name,
            "pronouns": student.pronouns,
            "email": student.email,
            "student_id": student.student_id or "",
            "member_since": _when(membership.created_at),
            "membership_active": membership.is_active,
        },
        "writing_projects": [
            _project(p) for p in WritingProject.objects.filter(center=center, student=student)
        ],
        "registration_answers": [
            form_export.response_data(r) for r in form_export.registration(membership)
        ],
        "appointments": [
            _appointment(p, intake.get(p.appointment_id, [])) for p in participations(membership)
        ],
        "session_records": [_record(r) for r in records(membership)],
        "booking_blocks": [
            _block(b)
            for b in Block.objects.filter(center=center, student=student)
            .select_related("schedule", "lifted_by")
            .order_by("created_at")
        ],
    }


def records(membership: Membership) -> QuerySet[SessionRecord]:
    """Every record about the student in the center, drafts included, oldest first."""
    return (
        SessionRecord.objects.filter(center=membership.center, student=membership.user)
        .select_related("tutor", "created_by", "finalized_by", "project")
        .prefetch_related("topics", "attachments__resource", "attachments__added_by")
        .prefetch_related("amendments__author")
        .order_by("held_at", "created_at")
    )


def participations(membership: Membership) -> QuerySet[Participant]:
    """Every appointment the student was in at the center, canceled ones too.

    `group_size` counts the students in each appointment. The others are never named.
    """
    return (
        Participant.objects.filter(
            student=membership.user, appointment__schedule__center=membership.center
        )
        .select_related(
            "appointment__tutor",
            "appointment__schedule",
            "appointment__room",
            "attendance_marked_by",
            "waived_by",
        )
        .annotate(group_size=Count("appointment__participants"))
        .order_by("appointment__starts_at")
    )


def _appointment(p: Participant, intake: list[Any]) -> dict[str, Any]:
    a = p.appointment
    return {
        "id": str(a.pk),
        "starts_at": _when(a.starts_at),
        "ends_at": _when(a.ends_at),
        "schedule": a.schedule.name,
        "tutor": _person(a.tutor),
        "modality": a.modality,
        "room": a.room.name if a.room else "",
        "status": a.status,
        "walk_in": a.is_walk_in,
        "tentative": a.is_tentative,
        "canceled_at": _when(a.canceled_at),
        "group_size": getattr(p, "group_size", 1),
        "joined_at": _when(p.added_at),
        "left_at": _when(p.canceled_at),
        "late_cancellation": p.late_cancel,
        "attendance": p.attendance or None,
        "attendance_marked_at": _when(p.attendance_marked_at),
        "attendance_marked_by": _person(p.attendance_marked_by),
        "flagged_as_possible_no_show_at": _when(p.flagged_at),
        "flag_cleared_at": _when(p.flag_cleared_at),
        "waived_at": _when(p.waived_at),
        "waived_by": _person(p.waived_by),
        "waiver_reason": p.waiver_reason,
        "intake_answers": [form_export.response_data(r) for r in intake],
    }


def _block(b: Block) -> dict[str, Any]:
    return {
        "id": str(b.pk),
        "reason": b.reason,
        "schedule": b.schedule.name if b.schedule else None,
        "count": b.count,
        "limit": b.limit,
        "counted_from": b.counted_from.isoformat() if b.counted_from else None,
        "set_at": _when(b.created_at),
        "last_day": b.ends_on.isoformat(),
        "lifted_at": _when(b.lifted_at),
        "lifted_by": _person(b.lifted_by),
        "lift_note": b.lift_note,
    }


def _record(r: SessionRecord) -> dict[str, Any]:
    return {
        "id": str(r.pk),
        "appointment_id": str(r.appointment_id) if r.appointment_id else None,
        "held_at": _when(r.held_at),
        "minutes": r.minutes,
        "tutor": _person(r.tutor),
        "status": r.status,
        "created_at": _when(r.created_at),
        "created_by": _person(r.created_by),
        "finalized_at": _when(r.finalized_at),
        "finalized_by": _person(r.finalized_by) if r.finalized_by else None,
        "started_from_group_record": r.copied_from_id is not None,
        "summary": r.summary,
        "next_steps": r.next_steps,
        "staff_notes": r.staff_notes,
        "topics": [
            {"slug": t.slug, "axis": t.axis, "label": t.label}
            for t in sorted(r.topics.all(), key=lambda t: t.position)
        ],
        "writing_project": {"id": str(r.project.pk), "title": r.project.title}
        if r.project
        else None,
        "resources": [
            {
                "id": a.resource.catalog_id or str(a.resource.pk),
                "title": a.resource.title,
                "url": a.resource.url,
                "publisher": a.resource.publisher,
                "license": a.resource.license,
                "note": a.note,
                "added_by": _person(a.added_by),
                "added_at": _when(a.added_at),
            }
            for a in r.attachments.all()
        ],
        "amendments": [
            {
                "at": _when(m.created_at),
                "by": _person(m.author),
                "reason": m.reason,
                "changes": m.changes,
            }
            for m in r.amendments.all()
        ],
    }


def _project(p: WritingProject) -> dict[str, Any]:
    return {
        "id": str(p.pk),
        "title": p.title,
        "course": p.course,
        "instructor": p.instructor,
        "due_on": p.due_on.isoformat() if isinstance(p.due_on, date) else None,
        "finished": p.is_finished,
        "created_at": _when(p.created_at),
    }


def _person(user: User | None) -> dict[str, str] | None:
    if user is None:
        return None
    return {"name": user.display_name, "email": user.email}


def _when(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
