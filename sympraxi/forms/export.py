"""A student's form answers for the FERPA export (sympraxi/records/export.py).

Every response is included, earlier ones too, each in the wording of the version
answered: the question as asked, the answer as words, and the stored value
(option keys for choices) so the export can be matched to later versions.
"""

from collections import defaultdict
from typing import Any

from sympraxi.centers.models import Membership
from sympraxi.forms.display import answer_text
from sympraxi.forms.models import CHOICE_KINDS, Answer, FormKind, Response


def responses(membership: Membership, kind: str) -> list[Response]:
    return list(
        Response.objects.filter(center=membership.center, student=membership.user, form__kind=kind)
        .select_related("version", "answered_by", "form__schedule")
        .prefetch_related("answers__question__options")
        .order_by("created_at")
    )


def intake_by_appointment(membership: Membership) -> dict[Any, list[Response]]:
    """Every intake response about the student, by appointment ID, oldest first."""
    found: dict[Any, list[Response]] = defaultdict(list)
    for response in responses(membership, FormKind.INTAKE):
        found[response.appointment_id].append(response)
    return found


def registration(membership: Membership) -> list[Response]:
    return responses(membership, FormKind.REGISTRATION)


def response_data(response: Response) -> dict[str, Any]:
    answers = sorted(response.answers.all(), key=lambda a: a.question.position)
    return {
        "id": str(response.id),
        "form": response.form.title,
        "version": response.version.number,
        "answered_at": response.created_at.isoformat() if response.created_at else None,
        "answered_by": {
            "name": response.answered_by.display_name,
            "email": response.answered_by.email,
        }
        if response.answered_by
        else None,
        "answers": [_answer(a) for a in answers],
    }


def _answer(answer: Answer) -> dict[str, Any]:
    value: Any = answer.value
    if answer.question.kind in CHOICE_KINDS:
        value = [str(k) for k in value]
    elif value is not None and not isinstance(value, str):
        value = str(value)
    return {
        "question": answer.question.label,
        "question_key": str(answer.key),
        "answer": answer_text(answer),
        "value": value,
    }
