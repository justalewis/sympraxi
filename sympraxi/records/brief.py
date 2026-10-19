"""What the appointment page shows about session records.

- Staff who may see record status (tutor, front desk, director) get a line per
  student: final, draft or not written yet. Front desk staff see no more than that.
- Staff who may read the student's records (the tutor, directors) also get the
  pre-session brief: the student's last three summaries and next steps, and
  their open writing projects. Reading the brief is a read of the student's
  history, so each one shown is audited.
- A student in the appointment gets a link to their own summary once it's final.

The brief also carries what each student answered when booking: their intake
answers for this appointment and the registration answers this viewer may see
(sympraxi/forms/display.py). Staff who get no brief, such as the front desk,
see those answers in a section of their own (`answer_rows`).
"""

from dataclasses import dataclass, field
from typing import Any, cast

from django.http import HttpRequest
from django.urls import reverse

from sympraxi.accounts.models import User
from sympraxi.centers.models import Membership, Role
from sympraxi.core.policy import Action, can
from sympraxi.forms import display as form_answers
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import goals
from sympraxi.records.models import Goal, RecordStatus, SessionRecord, WritingProject
from sympraxi.records.services import active_students
from sympraxi.scheduling.models import Appointment, AppointmentStatus

BRIEF_RECORDS = 3


@dataclass
class StudentRecordRow:
    student: User
    rec: SessionRecord | None
    write_url: str = ""
    view_url: str = ""


@dataclass
class Brief:
    student: User
    membership: Membership
    recent: list[SessionRecord] = field(default_factory=list)
    projects: list[WritingProject] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    goals_url: str = ""
    history_url: str = ""
    answers: form_answers.StudentAnswers | None = None


def appointment_context(request: HttpRequest, appointment: Appointment) -> dict[str, Any]:
    user = cast(User, request.user)
    center = appointment.schedule.center
    context: dict[str, Any] = {
        "record_rows": None,
        "briefs": [],
        "my_record_url": "",
        "answer_rows": [],
    }
    students = active_students(appointment)
    records = {
        r.student_id: r
        for r in SessionRecord.objects.filter(appointment=appointment).select_related("tutor")
    }

    mine = records.get(user.pk)
    if mine is not None and can(user, Action.VIEW_OWN_WRITING, mine):
        context["my_record_url"] = reverse(
            "records:my_record", kwargs={"slug": center.slug, "record_id": mine.pk}
        )

    if not students or not can(user, Action.VIEW_RECORD_STATUS, appointment):
        return context

    writable = appointment.status != AppointmentStatus.CANCELED and can(
        user, Action.WRITE_RECORDS, appointment
    )
    rows = []
    for student in students:
        row = StudentRecordRow(student=student, rec=records.get(student.pk))
        if row.rec is not None and can(user, Action.VIEW_RECORDS, row.rec):
            row.view_url = reverse(
                "records:detail", kwargs={"slug": center.slug, "record_id": row.rec.pk}
            )
        if writable and (row.rec is None or can(user, Action.WRITE_RECORDS, row.rec)):
            if row.rec is None:
                url = reverse(
                    "records:start", kwargs={"slug": center.slug, "appointment_id": appointment.pk}
                )
                row.write_url = f"{url}?student={student.pk}"
            else:
                name = "records:amend" if row.rec.is_final else "records:edit"
                row.write_url = reverse(name, kwargs={"slug": center.slug, "record_id": row.rec.pk})
        rows.append(row)
    context["record_rows"] = rows

    answers: dict[Any, form_answers.StudentAnswers] = {}
    if can(user, Action.VIEW_INTAKE, appointment) or can(user, Action.VIEW_REGISTRATION, center):
        answers = form_answers.for_appointment(request, appointment, students)

    memberships = {
        m.user_id: m
        for m in Membership.objects.filter(
            center=center, role=Role.STUDENT, user__in=students
        ).select_related("user", "center")
    }
    for student in students:
        membership = memberships.get(student.pk)
        if membership is None or not can(user, Action.VIEW_RECORDS, membership):
            continue
        brief = Brief(
            student=student,
            membership=membership,
            recent=list(
                SessionRecord.objects.filter(
                    center=center,
                    student=student,
                    status=RecordStatus.FINAL,
                    held_at__lt=appointment.starts_at,
                )
                .exclude(appointment=appointment)
                .select_related("tutor", "project")
                .order_by("-held_at")[:BRIEF_RECORDS]
            ),
            projects=list(
                WritingProject.objects.filter(center=center, student=student, is_finished=False)
            ),
            goals=goals.open_goals(center, student),
            goals_url=reverse(
                "records:student_goals",
                kwargs={"slug": center.slug, "membership_id": membership.pk},
            ),
            history_url=reverse(
                "records:student_history",
                kwargs={"slug": center.slug, "membership_id": membership.pk},
            ),
            answers=answers.get(student.pk),
        )
        record(
            AuditAction.HISTORY_VIEWED,
            request=request,
            center=center,
            target=membership,
            student=str(student.pk),
            where="appointment brief",
            appointment=str(appointment.pk),
            records=[str(r.pk) for r in brief.recent],
        )
        context["briefs"].append(brief)
    with_brief = {b.student.pk for b in context["briefs"]}
    context["answer_rows"] = [
        row for pk, row in answers.items() if row.any and pk not in with_brief
    ]
    return context
