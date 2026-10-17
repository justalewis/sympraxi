"""Builders for form tests: forms with questions, published versions and answers."""

import secrets
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.forms import builder, services
from sympraxi.forms.answering import visible
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import (
    Form,
    FormKind,
    FormVersion,
    Question,
    Response,
    SurveyInvitation,
    token_hash,
)
from sympraxi.forms.report import ReportAnswers
from sympraxi.records import services as records
from sympraxi.records.models import SessionRecord
from sympraxi.scheduling.models import Appointment, Schedule
from tests.records_world import appointment_for, topics
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


# Session-report questions: a required yes or no, a choice the student sees with a
# required follow-up, and a paragraph for staff.
REPORT = [
    {"label": "Did the student bring a draft?", "kind": "yes_no", "required": True},
    {
        "label": "What did you mostly work on?",
        "kind": "one_choice",
        "options": ["Ideas", "Organization", "Sources", "Sentences"],
        "shown_to_student": True,
    },
    {
        "label": "Which sentences?",
        "required": True,
        "condition": ("What did you mostly work on?", "Sentences"),
    },
    {"label": "What should the next tutor know?", "kind": "long_text"},
]

REPORT_ANSWERS = {
    "Did the student bring a draft?": "Yes",
    "What did you mostly work on?": "Organization",
    "What should the next tutor know?": "Ask about the conclusion.",
}

# A survey: a required choice, several choices and a paragraph.
SURVEY = [
    {
        "label": "How helpful was the session?",
        "kind": "one_choice",
        "options": ["Very helpful", "Somewhat helpful", "Not helpful"],
        "required": True,
    },
    {
        "label": "What helped most?",
        "kind": "several_choices",
        "options": ["Talking it through", "The tutor's questions", "A resource"],
    },
    {"label": "What could we do better?", "kind": "long_text"},
]

SURVEY_ANSWERS = {
    "How helpful was the session?": "Very helpful",
    "What helped most?": ["Talking it through"],
    "What could we do better?": "More evening hours.",
}


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


def report_answers(form: Form, by_label: dict[str, Any]) -> ReportAnswers:
    """Answers to a center's report questions, in its published version, by label."""
    version = form.published()
    assert version is not None
    return ReportAnswers(version=version, values=values(version, by_label))


def anonymous(form: Form, by_label: dict[str, Any], *, on: date | None = None) -> Response:
    """A survey response as the survey page stores it: no person, only a date."""
    version = form.published()
    assert version is not None
    return services.create_response(
        version=version,
        values=values(version, by_label),
        answered_by=None,
        student=None,
        answered_on=on or timezone.localdate(),
    )


def invite(
    appointment: Appointment, student: User, form: Form, *, days: int = 14
) -> tuple[SurveyInvitation, str]:
    """An invitation as if its email had gone out, and the token in its link."""
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    invitation = SurveyInvitation.objects.create(
        appointment=appointment,
        student=student,
        form=form,
        token_hash=token_hash(token),
        sent_at=now,
        expires_at=now + timedelta(days=days),
    )
    return invitation, token


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
    - `report_form`: the center's session-report questions, published, with a
      draft of version 2 started; `report_question` is a question in that draft.
    - `answered_record`: a final record of a session four days ago, with
      answers to the report questions (requires `add_records` first).
    - `survey_form`: the center's default survey, published, with five
      anonymous responses today, so its results show.
    - `survey_token`: a working survey link for the world's student, about
      `past_appointment`.
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

    world.report_form = make_form(center, FormKind.REPORT, questions=REPORT)
    if hasattr(world, "past_appointment"):
        world.answered_record = answered_record(world, days=-4)
    report_draft = builder.start_draft(actor=director_of(center), form=world.report_form)
    world.report_question = question(report_draft, "What did you mostly work on?")

    world.survey_form = make_form(center, FormKind.SURVEY, questions=SURVEY)
    for helpful in ["Very helpful"] * 3 + ["Somewhat helpful", "Not helpful"]:
        anonymous(world.survey_form, {**SURVEY_ANSWERS, "How helpful was the session?": helpful})
    if hasattr(world, "past_appointment"):
        _invitation, world.survey_token = invite(
            world.past_appointment, world.student, world.survey_form
        )
    return world


def answered_record(
    world: World, *, days: float = -4, by_label: dict[str, Any] | None = None
) -> SessionRecord:
    """A final record about the world's student, with answers to the report questions."""
    appointment = appointment_for(world, world.student, days=days)
    rec = records.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=world.student
    )
    content = records.Content(
        minutes=appointment.minutes,
        summary="You reorganized your paragraphs.",
        next_steps="Read it aloud once more.",
        topics=topics("argument:thesis", "revising"),
        answers=report_answers(world.report_form, by_label or REPORT_ANSWERS),
    )
    return records.finalize(actor=world.tutor, rec=rec, content=content, notify=False)
