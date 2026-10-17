"""The form engine: versions, permanent keys, conditions, and what the database enforces."""

import uuid
from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction
from django.http import QueryDict

from sympraxi.centers.models import Role
from sympraxi.forms import builder, services
from sympraxi.forms.answering import Questions
from sympraxi.forms.builder import BuilderError, QuestionData
from sympraxi.forms.display import answers_of
from sympraxi.forms.models import Answer, Form, FormKind, FormVersion, Option, Question, Response
from tests.forms_world import (
    INTAKE,
    INTAKE_ANSWERS,
    add_question,
    answer,
    director_of,
    form_data,
    make_form,
    question,
)
from tests.scheduling_world import build_world, make_member, make_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    return build_world(center)


def new_draft(form: Form) -> FormVersion:
    return builder.start_draft(actor=director_of(form.center), form=form)


def publish(draft: FormVersion) -> FormVersion:
    return builder.publish(actor=director_of(draft.form.center), draft=draft)


def shown(response: Response) -> list[tuple[str, str]]:
    return [(a.question, a.answer) for a in answers_of(response)]


def reword(draft: FormVersion, label: str, new_label: str, **changes: object) -> Question:
    q = question(draft, label)
    options = [(o.key, str(changes.pop(o.label, o.label))) for o in q.options.all()]
    data = QuestionData(
        kind=q.kind,
        label=new_label,
        help_text=q.help_text,
        required=q.required,
        condition=(q.show_if_question, q.show_if_option) if q.show_if_question else None,
        options=options,
    )
    return builder.save_question(draft=draft, question=q, data=data)


# --- Versions and keys -----------------------------------------------------------------


def test_a_new_draft_copies_the_published_version_keys_and_all(center) -> None:
    form = make_form(center)
    v1 = form.published()
    draft = new_draft(form)
    assert draft.number == 2 and not draft.is_published
    assert [(q.key, q.label, q.position) for q in draft.ordered_questions()] == [
        (q.key, q.label, q.position) for q in v1.ordered_questions()
    ]
    kind = question(draft, "What kind of writing is it?")
    assert [o.key for o in kind.options.all()] == [
        o.key for o in question(v1, "What kind of writing is it?").options.all()
    ]
    # Only one draft at a time: asking again returns the same one.
    assert new_draft(form) == draft


def test_answers_survive_reordering(world) -> None:
    form = make_form(world.center)
    first = answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    before = shown(first)
    draft = new_draft(form)
    last = question(draft, "Anything else your tutor should know?")
    for _ in range(6):
        builder.move_question(draft=draft, question=last, up=True)
        last.refresh_from_db()
    assert draft.ordered_questions()[0].label == "Anything else your tutor should know?"
    publish(draft)
    first.refresh_from_db()
    # The earlier answers keep their version's order and wording.
    assert shown(first) == before
    second = answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    assert shown(second)[0] == (
        "Anything else your tutor should know?",
        "My thesis keeps changing.",
    )
    # Both responses count under the same keys.
    key = question(draft, "What course is this for?").key
    assert Answer.objects.filter(key=key).count() == 2


def test_answers_keep_the_wording_they_were_given(world) -> None:
    form = make_form(world.center)
    first = answer(form, world.student, INTAKE_ANSWERS)
    draft = new_draft(form)
    reword(
        draft,
        "What kind of writing is it?",
        "What are you writing?",
        Essay="An essay or paper",
    )
    publish(draft)
    second = answer(
        form,
        world.student,
        {"What course is this for?": "ENG 101", "What are you writing?": "An essay or paper"},
    )
    assert ("What kind of writing is it?", "Essay") in shown(first)
    assert ("What are you writing?", "An essay or paper") in shown(second)
    # The option kept its key, so both answers chose the same thing.
    labels = ["What kind of writing is it?", "What are you writing?"]
    old, new = (Answer.objects.get(response=r, question__label__in=labels) for r in (first, second))
    assert old.key == new.key and old.options == new.options


def test_answers_to_a_removed_question_stay(world) -> None:
    form = make_form(world.center)
    first = answer(form, world.student, INTAKE_ANSWERS)
    draft = new_draft(form)
    builder.remove_question(
        draft=draft, question=question(draft, "Anything else your tutor should know?")
    )
    publish(draft)
    first.refresh_from_db()
    assert ("Anything else your tutor should know?", "My thesis keeps changing.") in shown(first)
    later = form.published()
    assert later.number == 2
    assert [q.position for q in later.ordered_questions()] == list(range(1, len(INTAKE)))
    with pytest.raises(Question.DoesNotExist):
        question(later, "Anything else your tutor should know?")


def test_new_versions_are_answered_from_then_on(world) -> None:
    form = make_form(world.center)
    first = answer(form, world.student, INTAKE_ANSWERS)
    publish(new_draft(form))
    second = answer(form, world.student, INTAKE_ANSWERS)
    assert (first.version.number, second.version.number) == (1, 2)
    assert {a.key for a in first.answers.all()} == {a.key for a in second.answers.all()}


def test_kind_of_answer_locks_once_published(center) -> None:
    form = make_form(center, publish=False)
    draft = form.draft()
    course = question(draft, "What course is this for?")
    # Never published: it can still change.
    builder.save_question(
        draft=draft, question=course, data=QuestionData(kind="long_text", label=course.label)
    )
    publish(draft)
    draft = new_draft(form)
    course = question(draft, "What course is this for?")
    assert builder.kind_locked(course)
    with pytest.raises(BuilderError, match="can't change"):
        builder.save_question(
            draft=draft, question=course, data=QuestionData(kind="number", label=course.label)
        )


def test_demographic_questions_are_always_optional(center) -> None:
    form = make_form(center, FormKind.REGISTRATION, questions=[], publish=False)
    q = add_question(
        form.draft(),
        {"label": "What is your age?", "kind": "number", "is_demographic": True, "required": True},
    )
    assert q.is_demographic and not q.required


def test_staff_visibility_is_only_for_registration(center) -> None:
    form = make_form(center, questions=[], publish=False)
    q = add_question(form.draft(), {"label": "Course?", "shown_to_staff": True})
    assert not q.shown_to_staff


# --- What the database enforces --------------------------------------------------------


def refused(statement) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        statement()


def test_a_published_version_cannot_change(world) -> None:
    form = make_form(world.center)
    v1 = form.published()
    q = question(v1, "What course is this for?")
    option = question(v1, "What kind of writing is it?").options.first()
    refused(lambda: Question.objects.filter(pk=q.pk).update(label="Changed"))
    refused(lambda: Question.objects.filter(pk=q.pk).delete())
    refused(
        lambda: Question.objects.create(version=v1, position=99, kind="short_text", label="New")
    )
    refused(lambda: Option.objects.filter(pk=option.pk).update(label="Changed"))
    refused(lambda: Option.objects.create(question=q, label="New", position=9))
    refused(lambda: FormVersion.objects.filter(pk=v1.pk).update(intro="Changed"))
    refused(lambda: FormVersion.objects.filter(pk=v1.pk).delete())
    # Through the builder, too.
    with pytest.raises(BuilderError):
        builder.save_question(draft=v1, question=q, data=QuestionData(kind=q.kind, label="Changed"))


def test_answers_are_never_changed_or_deleted(world) -> None:
    form = make_form(world.center)
    response = answer(form, world.student, INTAKE_ANSWERS)
    refused(lambda: Answer.objects.filter(response=response).update(text="Changed"))
    refused(lambda: Answer.objects.filter(response=response).delete())
    refused(lambda: Response.objects.filter(pk=response.pk).update(student=world.tutor))
    refused(lambda: Response.objects.filter(pk=response.pk).delete())


def test_an_answer_must_belong_to_its_version(world) -> None:
    form = make_form(world.center)
    v1 = form.published()
    v2 = publish(new_draft(form))
    response = services.save_response(
        version=v2, values={}, answered_by=world.student, student=world.student
    )
    old_question = question(v1, "What course is this for?")
    # A question from another version, even under its own key, is refused.
    refused(
        lambda: Answer.objects.create(
            response=response, question=old_question, key=old_question.key, text="x"
        )
    )
    current = question(v2, "What course is this for?")
    # The key must be the question's own.
    refused(lambda: Answer.objects.create(response=response, question=current, key=uuid.uuid4()))
    # Options must be the question's own.
    kind = question(v2, "What kind of writing is it?")
    refused(
        lambda: Answer.objects.create(
            response=response, question=kind, key=kind.key, options=[uuid.uuid4()]
        )
    )


def test_drafts_cannot_be_answered(world) -> None:
    form = make_form(world.center, publish=False)
    refused(
        lambda: Response.objects.create(
            form=form,
            version=form.draft(),
            center=world.center,
            student=world.student,
            answered_by=world.student,
        )
    )


def test_a_center_has_one_form_of_each_kind_per_place(world) -> None:
    make_form(world.center, FormKind.REGISTRATION, questions=[], publish=False)
    with pytest.raises(BuilderError):
        make_form(world.center, FormKind.REGISTRATION, questions=[], publish=False)
    make_form(world.center, questions=[], publish=False)
    make_form(world.center, questions=[], schedule=world.schedule, publish=False)
    with pytest.raises(BuilderError):
        make_form(world.center, questions=[], schedule=world.schedule, publish=False)


# --- Builder rules -----------------------------------------------------------------------


def test_follow_ups_depend_on_a_choice_question_above(center) -> None:
    form = make_form(center, publish=False)
    draft = form.draft()
    course = question(draft, "What course is this for?")
    with pytest.raises(BuilderError, match="choice question above"):
        builder.save_question(
            draft=draft,
            question=None,
            data=QuestionData(
                kind="short_text", label="Why?", condition=(course.key, uuid.uuid4())
            ),
        )
    follow = question(draft, "What kind of writing, then?")
    source = question(draft, "What kind of writing is it?")
    # The follow-up can't move above its question, nor its question below it.
    with pytest.raises(BuilderError, match="stay below"):
        builder.move_question(draft=draft, question=follow, up=True)
    with pytest.raises(BuilderError, match="stay below"):
        builder.move_question(draft=draft, question=source, up=False)
    # The question can't be removed, or stop being a choice, while the follow-up remains.
    with pytest.raises(BuilderError, match="follows up"):
        builder.remove_question(draft=draft, question=source)
    with pytest.raises(BuilderError, match="stay a choice"):
        builder.save_question(
            draft=draft, question=source, data=QuestionData(kind="short_text", label=source.label)
        )
    # Nor can the option it depends on.
    keep = [(o.key, o.label) for o in source.options.all() if o.label != "Something else"]
    with pytest.raises(BuilderError, match="option you removed"):
        builder.save_question(
            draft=draft,
            question=source,
            data=QuestionData(kind="one_choice", label=source.label, options=keep),
        )


def test_choice_questions_need_two_different_options(center) -> None:
    form = make_form(center, questions=[], publish=False)
    with pytest.raises(BuilderError, match="two options"):
        add_question(form.draft(), {"label": "Pick", "kind": "one_choice", "options": ["Only"]})
    with pytest.raises(BuilderError, match="different wording"):
        add_question(form.draft(), {"label": "Pick", "kind": "one_choice", "options": ["A", "a"]})
    yes_no = add_question(form.draft(), {"label": "Agree?", "kind": "yes_no"})
    assert [o.label for o in yes_no.options.all()] == ["Yes", "No"]


def test_publishing_needs_a_question(center) -> None:
    form = make_form(center, questions=[], publish=False)
    assert builder.publish_problems(form.draft()) == ["Add at least one question."]
    with pytest.raises(BuilderError):
        publish(form.draft())


def test_rewording_and_reordering_options_keeps_their_keys(center) -> None:
    form = make_form(center, publish=False)
    draft = form.draft()
    source = question(draft, "What would you like to work on?")
    before = {o.label: o.key for o in source.options.all()}
    options = [
        (before["Citations"], "Citations and sources"),
        (before["Ideas"], "Ideas"),
        (before["Organization"], "Organization"),
        (None, "Grammar"),
    ]
    builder.save_question(
        draft=draft,
        question=source,
        data=QuestionData(kind=source.kind, label=source.label, options=options),
    )
    after = [(o.key, o.label) for o in source.options.order_by("position")]
    assert after[:3] == options[:3]
    assert after[3][1] == "Grammar" and after[3][0] not in before.values()


def test_throwing_away_a_draft_keeps_the_published_version(center) -> None:
    form = make_form(center)
    draft = new_draft(form)
    builder.remove_question(draft=draft, question=question(draft, "When is it due?"))
    builder.discard_draft(form=form)
    assert form.draft() is None
    assert form.published().number == 1
    assert new_draft(form).number == 2


# --- Which form applies ----------------------------------------------------------------------


def test_a_schedule_uses_its_own_intake_form_or_the_default(world) -> None:
    assert services.intake_version(world.schedule) is None
    default = make_form(world.center)
    assert services.intake_version(world.schedule) == default.published()
    own = make_form(world.center, questions=INTAKE[:1], schedule=world.schedule)
    assert services.intake_version(world.schedule) == own.published()
    other = make_schedule(world.center, world.schedule.starts_on, world.schedule.ends_on)
    assert services.intake_version(other) == default.published()
    # A form taken out of use isn't asked; the default takes over.
    own.is_active = False
    own.save()
    assert services.intake_version(world.schedule) == default.published()
    default.is_active = False
    default.save()
    assert services.intake_version(world.schedule) is None


def test_registration_is_asked_once(world) -> None:
    form = make_form(world.center, FormKind.REGISTRATION, questions=[{"label": "Major?"}])
    assert services.registration_to_ask(world.center, world.student) == form.published()
    answer(form, world.student, {})
    assert services.registration_to_ask(world.center, world.student) is None
    newcomer = make_member(world.center, Role.STUDENT)
    assert services.registration_to_ask(world.center, newcomer) is not None


# --- Conditions, required questions and skipping -----------------------------------------------


def post(version: FormVersion, by_label: dict, *, shown: list[str] | None = None) -> QueryDict:
    data = QueryDict(mutable=True)
    for name, value in form_data(version, by_label, shown=shown).items():
        data.setlist(name, value if isinstance(value, list) else [value])
    return data


FIRST_PAGE = [q["label"] for q in INTAKE if "condition" not in q]


def test_follow_ups_show_only_after_their_option(world) -> None:
    version = make_form(world.center).published()
    labels = lambda qs: [q.label for q in qs]  # noqa: E731
    assert labels(Questions(version, prefix="intake").shown) == FIRST_PAGE
    page = Questions(
        version,
        prefix="intake",
        data=post(version, {"What kind of writing is it?": "Something else"}, shown=FIRST_PAGE),
    )
    assert labels(page.revealed) == ["What kind of writing, then?"]
    assert page.needs_another_look
    assert page.status == "1 follow-up question appeared."
    # Redrawn for the Continue step: unbound, so no error messages, with the follow-up.
    again = page.again()
    assert not again.form.is_bound
    assert "What kind of writing, then?" in labels(again.shown)


def test_a_follow_up_hidden_again_loses_its_answer(world) -> None:
    version = make_form(world.center).published()
    everything = [q["label"] for q in INTAKE]
    data = post(
        version,
        {
            **INTAKE_ANSWERS,
            "What kind of writing is it?": "Essay",
            "What kind of writing, then?": "A zine",
        },
        shown=everything,
    )
    page = Questions(version, prefix="intake", data=data)
    assert not page.needs_another_look
    assert page.removed == 1 and "removed" in page.status
    assert page.is_valid()
    follow = question(version, "What kind of writing, then?")
    assert follow.key not in page.values()


def test_required_questions_and_follow_ups(world) -> None:
    version = make_form(world.center).published()
    page = Questions(version, prefix="intake", data=post(version, {}, shown=FIRST_PAGE))
    assert not page.is_valid()
    errors = page.form.errors
    assert set(errors) == {
        f"q-{question(version, 'What course is this for?').key.hex}",
        f"q-{question(version, 'What kind of writing is it?').key.hex}",
    }
    # A required follow-up is required only once it shows.
    answers = {
        "What course is this for?": "ENG 101",
        "What kind of writing is it?": "Something else",
    }
    page = Questions(version, prefix="intake", data=post(version, answers))
    assert not page.is_valid()
    page = Questions(
        version,
        prefix="intake",
        data=post(version, {**answers, "What kind of writing, then?": "A zine"}),
    )
    assert page.is_valid()


def test_values_are_typed_and_blank_answers_left_out(world) -> None:
    version = make_form(world.center).published()
    by_label = {
        "What course is this for?": "  ENG 101  ",
        "What kind of writing is it?": "Essay",
        "What would you like to work on?": ["Citations", "Ideas"],
        "When is it due?": "2026-12-01",
        "How many pages is it?": "12.5",
    }
    page = Questions(version, prefix="intake", data=post(version, by_label))
    assert page.is_valid(), page.form.errors
    got = {q.label: page.values().get(q.key) for q in version.ordered_questions()}
    assert got["What course is this for?"] == "ENG 101"
    assert got["How many pages is it?"] == Decimal("12.5")
    assert str(got["When is it due?"]) == "2026-12-01"
    work = question(version, "What would you like to work on?")
    # Choices keep the options' order, whatever order they were posted in.
    assert got["What would you like to work on?"] == [
        o.key for o in work.options.all() if o.label in ("Ideas", "Citations")
    ]
    assert got["Anything else your tutor should know?"] is None


def test_staff_can_skip_by_leaving_everything_blank(world) -> None:
    version = make_form(world.center).published()
    blank = Questions(version, prefix="intake", data=post(version, {}), skippable=True)
    assert blank.is_valid() and blank.skipped and blank.values() == {}
    partly = Questions(
        version,
        prefix="intake",
        data=post(version, {"When is it due?": "2026-12-01"}),
        skippable=True,
    )
    assert not partly.skipped and not partly.is_valid()


def test_earlier_answers_fill_in_a_newer_version(world) -> None:
    form = make_form(world.center)
    other = {
        "What kind of writing is it?": "Something else",
        "What kind of writing, then?": "A zine",
    }
    first = answer(form, world.student, {**INTAKE_ANSWERS, **other})
    draft = new_draft(form)
    builder.remove_question(draft=draft, question=question(draft, "How many pages is it?"))
    publish(draft)
    page = Questions(form.published(), prefix="intake", initial=services.values_of(first))
    # The follow-up shows because the earlier answer chose its option.
    assert "What kind of writing, then?" in [q.label for q in page.shown]
    course = question(form.published(), "What course is this for?")
    assert page.form[f"q-{course.key.hex}"].value() == "ENG 101"
