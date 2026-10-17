"""Builders for form tests: forms with questions, published versions and answers."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.forms import builder, services
from sympraxi.forms.answering import visible
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import Form, FormKind, FormVersion, Question, Response
from sympraxi.scheduling.models import Appointment, Schedule
from tests.scheduling_world import World, make_member

# Intake questions: a required text, a choice with a follow-up, several choices,
# a date, a number and a paragraph.
INTAKE = [
    {"label": "What course is this for?", "required": True},
    {
        "label": "What kind of writing is it?",
        "kind": "one_choice",
        "options": ["Essay", "Lab report", "Something else"],
        "required": True,
    },
    {
        "label": "What kind of writing, then?",
        "required": True,
        "condition": ("What kind of writing is it?", "Something else"),
    },
    {
        "label": "What would you like to work on?",
        "kind": "several_choices",
        "options": ["Ideas", "Organization", "Citations"],
    },
    {"label": "When is it due?", "kind": "date"},
    {"label": "How many pages is it?", "kind": "number"},
    {"label": "Anything else your tutor should know?", "kind": "long_text"},
]

REGISTRATION = [
    {"label": "What is your major?", "shown_to_staff": True},
    {"label": "Is English your first language?", "kind": "yes_no", "is_demographic": True},
    {"label": "Are you the first in your family to go to college?", "kind": "yes_no"},
]


def director_of(center: Center) -> User:
    found = User.objects.filter(
        memberships__center=center, memberships__role=Role.DIRECTOR, memberships__is_active=True
    ).first()
    return found or make_member(center, Role.DIRECTOR)


def add_question(draft: FormVersion, spec: dict[str, Any]) -> Question:
    """A question from a spec like those in INTAKE: label, kind, options, condition and flags."""
    spec = dict(spec)
    condition = None
    if "condition" in spec:
        source_label, option_label = spec.pop("condition")
        source = draft.questions.get(label=source_label)
        condition = (source.key, source.options.get(label=option_label).key)
    options = [(None, label) for label in spec.pop("options", [])]
    return builder.save_question(
        draft=draft,
        question=None,
        data=QuestionData(
            kind=spec.pop("kind", "short_text"),
            label=spec.pop("label"),
            condition=condition,
            options=options,
            **spec,
        ),
    )


def make_form(
    center: Center,
    kind: str = FormKind.INTAKE,
    *,
    questions: Iterable[dict[str, Any]] = INTAKE,
    schedule: Schedule | None = None,
    publish: bool = True,
) -> Form:
    actor = director_of(center)
    form = builder.create_form(actor=actor, center=center, kind=kind, schedule=schedule)
    draft = form.draft()
    assert draft is not None
    for spec in questions:
        add_question(draft, spec)
    if publish:
        builder.publish(actor=actor, draft=draft)
    return form


def question(version: FormVersion, label: str) -> Question:
    return version.questions.get(label=label)


def values(version: FormVersion, by_label: dict[str, Any]) -> dict[Any, Any]:
    """Answers by question label (and option labels for choices) as the service takes them."""
    result: dict[Any, Any] = {}
    for label, value in by_label.items():
        q = question(version, label)
        if q.kind in ("one_choice", "several_choices", "yes_no"):
            chosen = [value] if isinstance(value, str) else value
            labels = {o.label: o.key for o in q.options.all()}
            result[q.key] = [labels[c] for c in chosen]
        elif q.kind == "number":
            result[q.key] = Decimal(str(value))
        elif q.kind == "date":
            result[q.key] = value if isinstance(value, date) else date.fromisoformat(value)
        else:
            result[q.key] = value
    return result


def answer(
    form: Form,
    student: User,
    by_label: dict[str, Any],
    *,
    appointment: Appointment | None = None,
    by: User | None = None,
) -> Response:
    version = form.published()
    assert version is not None
    return services.save_response(
        version=version,
        values=values(version, by_label),
        answered_by=by or student,
        student=student,
        appointment=appointment,
    )


def form_data(
    version: FormVersion,
    by_label: dict[str, Any],
    *,
    prefix: str = "intake",
    shown: list[str] | None = None,
) -> dict[str, Any]:
    """Answers as a browser posts them. The page showed `shown` (labels), or by
    default every question the answers call for."""
    data: dict[str, Any] = {}
    answers = values(version, by_label)
    for key, value in answers.items():
        name = f"{prefix}-q-{key.hex}"
        data[name] = [k.hex for k in value] if isinstance(value, list) else str(value)
    if shown is None:
        chosen = {k: set(v) for k, v in answers.items() if isinstance(v, list)}
        keys = [q.key for q in visible(version.ordered_questions(), chosen)]
    else:
        keys = [question(version, label).key for label in shown]
    data[f"{prefix}-shown"] = ",".join(k.hex for k in keys)
    return data


INTAKE_ANSWERS = {
    "What course is this for?": "ENG 101",
    "What kind of writing is it?": "Essay",
    "What would you like to work on?": ["Ideas", "Citations"],
    "Anything else your tutor should know?": "My thesis keeps changing.",
}

REGISTRATION_ANSWERS = {
    "What is your major?": "Biology",
    "Is English your first language?": "No",
    "Are you the first in your family to go to college?": "Yes",
}


def add_forms(world: World) -> World:
    """Give a world the form objects pages need.

    - `registration_form`: published, and answered by the world's student
      (`registration_response`).
    - `form`: the center's default intake form, published, with a draft of
      version 2 started; `question` is a question in that draft.
    - `intake_response`: the world's student's answers for the world's appointment.
    """
    center = world.center
    world.registration_form = make_form(center, FormKind.REGISTRATION, questions=REGISTRATION)
    world.registration_response = answer(
        world.registration_form, world.student, REGISTRATION_ANSWERS
    )
    world.form = make_form(center)
    world.intake_response = answer(
        world.form, world.student, INTAKE_ANSWERS, appointment=world.appointment
    )
    draft = builder.start_draft(actor=director_of(center), form=world.form)
    world.question = question(draft, "What course is this for?")
    return world
