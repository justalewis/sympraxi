"""Showing answers, always in the wording of the version that was answered.

Who sees what (the actions are in `sympraxi/core/policy.py`):

- Intake answers: the tutor of the appointment, front desk staff and directors
  (`forms.view_intake`), and the student who gave them.
- Registration answers: directors see every answer (`forms.view_all_registration`).
  Tutors (for students they tutor) and front desk staff see only the answers to
  questions the current published version marks for staff
  (`forms.view_registration`). The current version decides, so a director who
  stops showing a question to staff hides the earlier answers to it too. A
  question dropped from the form is seen by directors only.
- Students see all of their own answers.

Every staff read is audited (`forms.answers_viewed`), naming the student and the
responses shown, never the answers themselves.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.policy import Action, can
from sympraxi.forms import services
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Answer,
    FormKind,
    QuestionKind,
    Response,
    plain_number,
)
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Appointment


@dataclass
class ShownAnswer:
    key: UUID
    question: str
    answer: str


@dataclass
class ShownResponse:
    response: Response
    answers: list[ShownAnswer] = field(default_factory=list)

    @property
    def by_staff(self) -> bool:
        return self.response.by_staff


def answer_text(answer: Answer) -> str:
    """One answer as words, in the version's own wording."""
    question = answer.question
    if question.kind in CHOICE_KINDS:
        labels = question.option_labels()
        return ", ".join(labels.get(k, "") for k in answer.options if k in labels)
    if question.kind == QuestionKind.NUMBER:
        return plain_number(answer.number) if answer.number is not None else ""
    if question.kind == QuestionKind.DATE:
        return date_format(answer.date, "F j, Y") if answer.date else ""
    return answer.text


def answers_of(response: Response, *, keys: set[UUID] | None = None) -> list[ShownAnswer]:
    """The response's answers in question order. With `keys`, only those questions."""
    answers = (
        response.answers.select_related("question")
        .prefetch_related("question__options")
        .order_by("question__position")
    )
    return [
        ShownAnswer(key=a.key, question=a.question.label, answer=answer_text(a))
        for a in answers
        if keys is None or a.key in keys
    ]


def show(response: Response, *, keys: set[UUID] | None = None) -> ShownResponse:
    return ShownResponse(response=response, answers=answers_of(response, keys=keys))


def staff_keys(center: Center) -> set[UUID]:
    """Registration questions the current published version shows to staff.

    A form taken out of use still decides what staff see of the answers it got.
    """
    form = services.registration_form(center)
    version = form.published() if form is not None else None
    if version is None:
        return set()
    return set(version.questions.filter(shown_to_staff=True).values_list("key", flat=True))


def registration_for(user: Any, membership: Membership) -> ShownResponse | None:
    """The student's latest registration answers, as much as this viewer may see.

    None when the viewer may see none of them. Doesn't audit; callers do.
    """
    if not can(user, Action.VIEW_REGISTRATION, membership):
        return None
    response = services.latest_registration(membership.center, membership.user)
    if response is None:
        return None
    if can(user, Action.VIEW_ALL_REGISTRATION, membership):
        return show(response)
    shown = show(response, keys=staff_keys(membership.center))
    return shown if shown.answers else None


@dataclass
class StudentAnswers:
    """What a staff member sees of one student's answers on an appointment page."""

    student: User
    intake: ShownResponse | None = None
    registration: ShownResponse | None = None
    registration_url: str = ""

    @property
    def any(self) -> bool:
        return self.intake is not None or self.registration is not None


def for_appointment(
    request: HttpRequest, appointment: Appointment, students: list[User]
) -> dict[Any, StudentAnswers]:
    """Each student's intake and registration answers that this staff member may see.

    Audited as one read listing the responses shown.
    """
    user = request.user
    center = appointment.schedule.center
    result: dict[Any, StudentAnswers] = {}
    memberships = {
        m.user_id: m
        for m in Membership.objects.filter(
            center=center, role=Role.STUDENT, user__in=students
        ).select_related("user", "center")
    }
    see_intake = can(user, Action.VIEW_INTAKE, appointment)
    for student in students:
        row = StudentAnswers(student=student)
        if see_intake:
            intake = services.latest_intake(appointment, student)
            row.intake = show(intake) if intake is not None else None
        membership = memberships.get(student.pk)
        if membership is not None:
            row.registration = registration_for(user, membership)
            if row.registration is not None:
                row.registration_url = reverse(
                    "forms:student_registration",
                    kwargs={"slug": center.slug, "membership_id": membership.pk},
                )
        result[student.pk] = row
    shown = [
        str(r.response.pk)
        for row in result.values()
        for r in (row.intake, row.registration)
        if r is not None
    ]
    if shown:
        record(
            AuditAction.ANSWERS_VIEWED,
            request=request,
            center=center,
            target=appointment,
            students=[str(s.pk) for s in students if result[s.pk].any],
            responses=shown,
            where="appointment page",
        )
    return result


def own_answers(user: Any, appointment: Appointment) -> dict[str, Any]:
    """A student's own intake answers on the appointment page, and where to change them.

    Not audited: the answers are the student's own.
    """
    context: dict[str, Any] = {"my_answers": None, "intake_url": ""}
    if not user.is_authenticated or not can(user, Action.ANSWER_OWN_FORMS, appointment):
        return context
    response = services.latest_intake(appointment, user)
    if response is not None:
        context["my_answers"] = show(response)
    still_open = appointment.is_active and appointment.starts_at > timezone.now()
    if still_open and services.intake_version(appointment.schedule) is not None:
        context["intake_url"] = reverse(
            "forms:intake",
            kwargs={"slug": appointment.schedule.center.slug, "appointment_id": appointment.pk},
        )
    return context


def registration_history(membership: Membership) -> list[Response]:
    return list(
        Response.objects.filter(
            center=membership.center,
            form__kind=FormKind.REGISTRATION,
            student=membership.user,
        )
        .select_related("version", "answered_by")
        .order_by("-created_at")
    )
