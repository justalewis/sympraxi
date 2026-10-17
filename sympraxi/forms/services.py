"""Which form applies where, and saving answers.

- Registration: one form per center, asked the first time a student books there
  (`registration_to_ask`). Students can change their answers later; each change
  is a new response, and the latest one counts.
- Intake: the schedule's own form if it has one in use, otherwise the center's
  default (`intake_version`). Answers belong to the appointment, so they stay
  with it when it moves.

Only published versions are answered, always the latest one at the time.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Answer,
    Form,
    FormKind,
    FormVersion,
    QuestionKind,
    Response,
)
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Appointment, Schedule

# A typed answer: text, a number, a date, or the keys of the options chosen.
AnswerValue = str | Decimal | date | list[UUID]


def published_version(form: Form | None) -> FormVersion | None:
    """The version to answer, if the form is in use and has been published."""
    if form is None or not form.is_active:
        return None
    return form.published()


def registration_form(center: Center) -> Form | None:
    return Form.objects.filter(center=center, kind=FormKind.REGISTRATION).first()


def registration_version(center: Center) -> FormVersion | None:
    return published_version(registration_form(center))


def registration_to_ask(center: Center, student: User) -> FormVersion | None:
    """The registration version to ask on the booking page: only if never answered."""
    version = registration_version(center)
    if version is None:
        return None
    if Response.objects.filter(form=version.form, student=student).exists():
        return None
    return version


def intake_version(schedule: Schedule) -> FormVersion | None:
    """The schedule's own intake form if it is in use, otherwise the center's default."""
    forms = {
        f.schedule_id: f
        for f in Form.objects.filter(center_id=schedule.center_id, kind=FormKind.INTAKE).filter(
            Q(schedule=schedule) | Q(schedule__isnull=True)
        )
    }
    return published_version(forms.get(schedule.pk)) or published_version(forms.get(None))


def latest_registration(center: Center, student: User) -> Response | None:
    return (
        Response.objects.filter(center=center, form__kind=FormKind.REGISTRATION, student=student)
        .select_related("version", "answered_by")
        .order_by("-created_at")
        .first()
    )


def latest_intake(appointment: Appointment, student: User) -> Response | None:
    return (
        Response.objects.filter(
            appointment=appointment, form__kind=FormKind.INTAKE, student=student
        )
        .select_related("version", "answered_by")
        .order_by("-created_at")
        .first()
    )


def values_of(response: Response | None) -> dict[UUID, AnswerValue]:
    """A response's answers by question key, to start a new response from."""
    if response is None:
        return {}
    return {a.key: a.value for a in response.answers.select_related("question")}


def save_response(
    *,
    version: FormVersion,
    values: dict[UUID, AnswerValue],
    answered_by: User,
    student: User | None,
    appointment: Appointment | None = None,
    request: HttpRequest | None = None,
) -> Response:
    """Store answers to a published version as a new response.

    `values` holds only questions that were shown and answered, by key. Earlier
    responses stay as they are.
    """
    form = version.form
    questions = {q.key: q for q in version.ordered_questions()}
    with transaction.atomic():
        response = Response.objects.create(
            form=form,
            version=version,
            center=form.center,
            student=student,
            appointment=appointment,
            answered_by=answered_by,
        )
        answers = []
        for key, value in values.items():
            question = questions[key]
            answer = Answer(response=response, question=question, key=key)
            if question.kind in CHOICE_KINDS:
                answer.options = list(value) if isinstance(value, list) else []
            elif question.kind == QuestionKind.NUMBER:
                answer.number = value if isinstance(value, Decimal) else None
            elif question.kind == QuestionKind.DATE:
                answer.date = value if isinstance(value, date) else None
            else:
                answer.text = str(value)
            answers.append(answer)
        Answer.objects.bulk_create(answers)
        record(
            AuditAction.FORM_ANSWERED,
            request=request,
            actor=answered_by,
            center=form.center,
            target=response,
            kind=form.kind,
            form=str(form.pk),
            version=version.number,
            student=str(student.pk) if student else None,
            appointment=str(appointment.pk) if appointment else None,
            by_staff=student is not None and student.pk != answered_by.pk,
            answers=len(answers),
        )
    return response
