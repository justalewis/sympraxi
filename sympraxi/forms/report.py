"""Session-report questions: what a tutor answers about a session in its record.

A center has one set of report questions, built like its other forms. They show
on the session record form below the fixed fields, and their answers belong to
the record: one response per record (`Response.record`), about the record's
student, given by the tutor who saved it.

- While the record is a draft, each save replaces its answers with a new
  response on the version the center asks now. Earlier draft answers aren't
  kept, the same as the record's own draft text.
- Finalizing needs an answer to every required question that shows (a
  follow-up counts only when its condition is met).
- Once the record is final, its answers stay on the version they were given in
  and change only through `records.services.amend()`, which keeps each changed
  answer before and after in the amendment. The database refuses any other
  change (migration 0004).
- Group copies get the same answers, like the summary; the tutor reads and
  finalizes each copy.

Who sees the answers: staff who read the record (`records.view`) see all of
them. The student sees an answer only if its question said so when the tutor
answered it and the center's latest published version still says so, so
marking a question for students later never shows them answers a tutor wrote
for staff only.
"""

from dataclasses import dataclass
from uuid import UUID

from django.http import QueryDict
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.forms import services
from sympraxi.forms.answering import Questions, visible
from sympraxi.forms.display import ShownAnswer, ShownResponse, answer_text, value_text
from sympraxi.forms.models import Answer, FormVersion, Question, Response
from sympraxi.forms.services import AnswerValue
from sympraxi.records.models import SessionRecord

PREFIX = "report"


@dataclass
class ReportAnswers:
    """A record's answers: the version answered and the values by question key."""

    version: FormVersion
    values: dict[UUID, AnswerValue]


def response_of(rec: SessionRecord) -> Response | None:
    return (
        Response.objects.filter(record=rec).select_related("version__form", "answered_by").first()
    )


def answers_of(rec: SessionRecord) -> ReportAnswers | None:
    response = response_of(rec)
    if response is None:
        return None
    return ReportAnswers(version=response.version, values=services.values_of(response))


def questions_for(
    *,
    center: Center,
    rec: SessionRecord | None,
    amend: bool,
    data: QueryDict | None,
) -> Questions | None:
    """The report questions on the record form, or None if there are none to ask.

    A draft (or a record not started yet) asks the version the center asks now,
    filled in from the answers saved so far, and requires nothing until it is
    finalized. An amendment asks the version the record was answered in, with
    its required questions required; a record finalized without answers gets no
    questions, since an amendment corrects what was recorded.
    """
    response = response_of(rec) if rec is not None else None
    version = (response.version if response else None) if amend else services.report_version(center)
    if version is None:
        return None
    return Questions(
        version,
        prefix=PREFIX,
        data=data,
        initial=services.values_of(response),
        enforce_required=amend,
    )


def missing(version: FormVersion, values: dict[UUID, AnswerValue]) -> list[str]:
    """Required questions that show, given these answers, and have no answer."""
    chosen = {k: set(v) for k, v in values.items() if isinstance(v, list)}
    shown = visible(version.ordered_questions(), chosen)
    return [
        _("Answer “%(label)s”.") % {"label": q.label}
        for q in shown
        if q.required and q.key not in values
    ]


def problems(rec: SessionRecord) -> list[str]:
    """What the record's answers still need before it can be finalized."""
    response = response_of(rec)
    if response is not None:
        return missing(response.version, services.values_of(response))
    version = services.report_version(rec.center)
    return missing(version, {}) if version is not None else []


def write(rec: SessionRecord, answers: ReportAnswers, actor: User) -> None:
    """Store the record's answers. Called from `records.services`, which locks the record.

    A draft's response is replaced. A final record's answers are changed in
    place, on the version they were given in; the database allows that only
    inside an amendment.
    """
    existing = response_of(rec)
    if not rec.is_final or existing is None:
        if existing is not None:
            existing.delete()
        if answers.values:
            services.create_response(
                version=answers.version,
                values=answers.values,
                answered_by=actor,
                student=rec.student,
                appointment=rec.appointment,
                session_record=rec,
            )
        return
    if existing.version_id != answers.version.pk:
        raise ValueError("A final record's answers stay on the version they were given in.")
    questions = {q.key: q for q in existing.version.ordered_questions()}
    current = {a.key: a for a in existing.answers.select_related("question")}
    for key, value in answers.values.items():
        question = questions[key]
        answer = current.pop(key, None)
        if answer is None:
            answer = Answer(response=existing, question=question, key=key)
            services.set_value(answer, question, value)
            answer.save()
        elif answer.value != value:
            services.set_value(answer, question, value)
            answer.save(update_fields=["text", "number", "date", "options"])
    for answer in current.values():
        answer.delete()


def changes(before: ReportAnswers | None, after: ReportAnswers) -> dict[str, dict[str, str]]:
    """Each changed answer, in words, for an amendment: "answer:<key>" -> before and after."""
    old = before.values if before is not None else {}
    asked = after.version.ordered_questions()
    earlier = before.version.ordered_questions() if before is not None else []
    by_key: dict[UUID, Question] = {q.key: q for q in [*earlier, *asked]}
    keys = [q.key for q in asked] + [q.key for q in earlier if q.key not in after.values]
    result: dict[str, dict[str, str]] = {}
    for key in dict.fromkeys(keys):
        was, now = old.get(key), after.values.get(key)
        if was == now:
            continue
        question = by_key[key]
        result[f"answer:{key}"] = {
            "question": question.label,
            "question_key": str(key),
            "before": value_text(question, was),
            "after": value_text(question, now),
        }
    return result


def _student_marks(center: Center) -> dict[UUID, bool]:
    """Which questions the latest published version shows to students, by key."""
    form = services.report_form(center)
    latest = form.published() if form is not None else None
    if latest is None:
        return {}
    return dict(latest.questions.values_list("key", "shown_to_student"))


def _sees(answer: Answer, marks: dict[UUID, bool]) -> bool:
    return answer.question.shown_to_student and marks.get(answer.key, True)


def _answers(response: Response) -> list[Answer]:
    return list(
        response.answers.select_related("question")
        .prefetch_related("question__options")
        .order_by("question__position")
    )


def for_staff(rec: SessionRecord) -> ShownResponse | None:
    """Every answer in the record, marking the ones the student sees. Doesn't audit."""
    response = response_of(rec)
    if response is None:
        return None
    marks = _student_marks(rec.center)
    return ShownResponse(
        response=response,
        answers=[
            ShownAnswer(
                key=a.key,
                question=a.question.label,
                answer=answer_text(a),
                student_sees=_sees(a, marks),
            )
            for a in _answers(response)
        ],
    )


def for_student(rec: SessionRecord) -> list[ShownAnswer]:
    """The answers this record's student sees, on a final record only."""
    if not rec.is_final:
        return []
    response = response_of(rec)
    if response is None:
        return []
    marks = _student_marks(rec.center)
    return [
        ShownAnswer(key=a.key, question=a.question.label, answer=answer_text(a), student_sees=True)
        for a in _answers(response)
        if _sees(a, marks)
    ]
