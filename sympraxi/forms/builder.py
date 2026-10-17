"""What directors do to forms: create one, edit its draft, publish it.

Only a draft version changes. Editing a published form starts a draft that copies
the published version, keys and all (`start_draft`), so a question keeps its key
however it is moved or reworded. Publishing makes the draft the version people
answer; the database then freezes it (migration 0002).

Rules that keep earlier answers meaningful:

- A question's kind of answer can change only until it has been published.
  After that, a different kind of answer is a different question: remove it and
  add a new one, which gets a new key.
- A follow-up depends on an earlier choice question in the same version. It
  can't be moved above that question, and neither the question nor the option
  it depends on can be removed while the follow-up remains.
- Demographic questions are always optional.
"""

from dataclasses import dataclass, field
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Form,
    FormKind,
    FormVersion,
    Option,
    Question,
    QuestionKind,
)
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Schedule


class BuilderError(Exception):
    """A rule refused the change. `message` is written for the director."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class QuestionData:
    """Everything the question form sets. `options` lists (key or None, label) in order;
    None makes a new option."""

    kind: str
    label: str
    help_text: str = ""
    required: bool = False
    is_demographic: bool = False
    shown_to_staff: bool = False
    condition: tuple[UUID, UUID] | None = None
    options: list[tuple[UUID | None, str]] = field(default_factory=list)


# --- Forms and drafts -------------------------------------------------------------


def create_form(
    *,
    actor: User,
    center: Center,
    kind: str,
    schedule: Schedule | None = None,
    request: HttpRequest | None = None,
) -> Form:
    """A new form with an empty draft, version 1."""
    if kind not in (FormKind.REGISTRATION, FormKind.INTAKE):
        raise BuilderError(_("Only registration and intake forms can be added here."))
    if kind != FormKind.INTAKE:
        schedule = None
    if Form.objects.filter(center=center, kind=kind, schedule=schedule).exists():
        raise BuilderError(_("That form already exists. Edit it instead."))
    try:
        with transaction.atomic():
            form = Form.objects.create(
                center=center, kind=kind, schedule=schedule, created_by=actor
            )
            FormVersion.objects.create(form=form, number=1, created_by=actor)
    except IntegrityError as exc:
        raise BuilderError(_("That form already exists. Edit it instead.")) from exc
    record(
        AuditAction.FORM_CREATED,
        request=request,
        actor=actor,
        center=center,
        target=form,
        kind=kind,
        schedule=str(schedule.pk) if schedule else None,
    )
    return form


def start_draft(*, actor: User, form: Form) -> FormVersion:
    """The form's draft, copied from the published version if there isn't one yet."""
    with transaction.atomic():
        Form.objects.select_for_update().get(pk=form.pk)
        draft = form.draft()
        if draft is not None:
            return draft
        current = form.published()
        number = (form.versions.aggregate(n=Max("number"))["n"] or 0) + 1
        draft = FormVersion.objects.create(
            form=form,
            number=number,
            intro=current.intro if current else "",
            created_by=actor,
        )
        if current is not None:
            for question in current.ordered_questions():
                copy = Question.objects.create(
                    version=draft,
                    key=question.key,
                    position=question.position,
                    kind=question.kind,
                    label=question.label,
                    help_text=question.help_text,
                    required=question.required,
                    is_demographic=question.is_demographic,
                    shown_to_staff=question.shown_to_staff,
                    show_if_question=question.show_if_question,
                    show_if_option=question.show_if_option,
                )
                Option.objects.bulk_create(
                    Option(question=copy, key=o.key, label=o.label, position=o.position)
                    for o in question.options.all()
                )
    return draft


def discard_draft(*, form: Form) -> None:
    """Throw away the draft. A form that was never published keeps an empty one."""
    with transaction.atomic():
        Form.objects.select_for_update().get(pk=form.pk)
        draft = form.draft()
        if draft is None:
            return
        if form.published() is None:
            draft.questions.all().delete()
            draft.intro = ""
            draft.save(update_fields=["intro"])
            return
        draft.delete()


def save_intro(*, draft: FormVersion, intro: str) -> None:
    _check_draft(draft)
    draft.intro = intro.strip()
    draft.save(update_fields=["intro"])


def set_active(
    *, actor: User, form: Form, active: bool, request: HttpRequest | None = None
) -> None:
    form.is_active = active
    form.save(update_fields=["is_active"])
    record(
        AuditAction.FORM_CHANGED,
        request=request,
        actor=actor,
        center=form.center,
        target=form,
        is_active=active,
    )


# --- Questions --------------------------------------------------------------------


def published_keys(form: Form) -> set[UUID]:
    """Keys of questions that have been in any published version of the form."""
    return set(
        Question.objects.filter(
            version__form=form, version__published_at__isnull=False
        ).values_list("key", flat=True)
    )


def kind_locked(question: Question) -> bool:
    """Whether people may have answered this question, so its kind can't change."""
    return question.key in published_keys(question.version.form)


def earlier_choice_questions(draft: FormVersion, before: Question | None) -> list[Question]:
    """Questions a follow-up placed at `before` (or at the end) could depend on."""
    questions = draft.ordered_questions()
    if before is not None:
        questions = [q for q in questions if q.position < before.position]
    return [q for q in questions if q.kind in CHOICE_KINDS]


def dependents(draft: FormVersion, question: Question) -> list[Question]:
    return list(draft.questions.filter(show_if_question=question.key).order_by("position"))


def save_question(*, draft: FormVersion, question: Question | None, data: QuestionData) -> Question:
    """Add a question to the end of the draft, or change one already in it."""
    _check_draft(draft)
    label = data.label.strip()
    if not label:
        raise BuilderError(_("Write the question."))
    with transaction.atomic():
        FormVersion.objects.select_for_update().get(pk=draft.pk)
        if question is not None and question.kind != data.kind and kind_locked(question):
            raise BuilderError(
                _(
                    "People may have answered this question already, so its kind of answer "
                    "can't change. Remove it and add a new question instead."
                )
            )
        if question is None:
            last = draft.questions.aggregate(n=Max("position"))["n"] or 0
            question = Question(version=draft, position=last + 1)
        _check_condition(draft, question, data.condition)
        previous_kind = None if question._state.adding else question.kind
        if previous_kind in CHOICE_KINDS and data.kind not in CHOICE_KINDS:
            followers = dependents(draft, question)
            if followers:
                raise BuilderError(
                    _("“%(label)s” follows up on this question, so it has to stay a choice.")
                    % {"label": followers[0].label}
                )
        question.kind = data.kind
        question.label = label
        question.help_text = data.help_text.strip()
        question.is_demographic = data.is_demographic
        question.required = data.required and not data.is_demographic
        question.shown_to_staff = data.shown_to_staff and draft.form.kind == FormKind.REGISTRATION
        question.show_if_question, question.show_if_option = data.condition or (None, None)
        question.save()
        _save_options(draft, question, data, previous_kind)
    return question


def _check_condition(
    draft: FormVersion, question: Question, condition: tuple[UUID, UUID] | None
) -> None:
    if condition is None:
        return
    source_key, option_key = condition
    before = None if question._state.adding else question
    source = next((q for q in earlier_choice_questions(draft, before) if q.key == source_key), None)
    if source is None:
        raise BuilderError(_("A follow-up can only depend on a choice question above it."))
    if option_key not in source.option_labels():
        raise BuilderError(_("Choose one of that question's options."))


def _save_options(
    draft: FormVersion, question: Question, data: QuestionData, previous_kind: str | None
) -> None:
    existing = {o.key: o for o in question.options.all()}
    if data.kind == QuestionKind.YES_NO:
        if previous_kind == QuestionKind.YES_NO and existing:
            return  # Yes and No keep their keys and wording.
        wanted: list[tuple[UUID | None, str]] = [(None, _("Yes")), (None, _("No"))]
    elif data.kind in CHOICE_KINDS:
        wanted = [(key, label.strip()) for key, label in data.options if label.strip()]
        if len(wanted) < 2:
            raise BuilderError(_("Give at least two options."))
        labels = [label.casefold() for _key, label in wanted]
        if len(set(labels)) != len(labels):
            raise BuilderError(_("Each option needs a different wording."))
    else:
        wanted = []
    kept = {key for key, _label in wanted if key is not None}
    unknown = kept - set(existing)
    if unknown:
        raise BuilderError(_("The options changed while you were editing. Try again."))
    used = set(
        draft.questions.filter(show_if_question=question.key).values_list(
            "show_if_option", flat=True
        )
    )
    dropped = used - kept
    if dropped:
        follower = draft.questions.filter(
            show_if_question=question.key, show_if_option__in=dropped
        ).first()
        raise BuilderError(
            _("“%(label)s” follows up on an option you removed. Change it first.")
            % {"label": follower.label if follower else ""}
        )
    for key, option in existing.items():
        if key not in kept:
            option.delete()
    for position, (wanted_key, label) in enumerate(wanted, start=1):
        if wanted_key is None:
            Option.objects.create(question=question, label=label, position=position)
        else:
            option = existing[wanted_key]
            if option.label != label or option.position != position:
                option.label, option.position = label, position
                option.save(update_fields=["label", "position"])


def move_question(*, draft: FormVersion, question: Question, up: bool) -> None:
    """Swap the question with the one above or below it."""
    _check_draft(draft)
    with transaction.atomic():
        FormVersion.objects.select_for_update().get(pk=draft.pk)
        questions = draft.ordered_questions()
        index = next(i for i, q in enumerate(questions) if q.pk == question.pk)
        other_index = index - 1 if up else index + 1
        if not 0 <= other_index < len(questions):
            raise BuilderError(
                _("That question is already first.") if up else _("That question is already last.")
            )
        other = questions[other_index]
        upper, lower = (other, questions[index]) if up else (questions[index], other)
        if lower.show_if_question == upper.key:
            raise BuilderError(
                _("“%(follow)s” follows up on “%(source)s”, so it has to stay below it.")
                % {"follow": lower.label, "source": upper.label}
            )
        upper.position, lower.position = lower.position, upper.position
        Question.objects.bulk_update([upper, lower], ["position"])


def remove_question(*, draft: FormVersion, question: Question) -> None:
    _check_draft(draft)
    with transaction.atomic():
        FormVersion.objects.select_for_update().get(pk=draft.pk)
        followers = dependents(draft, question)
        if followers:
            raise BuilderError(
                _("“%(label)s” follows up on this question. Change or remove it first.")
                % {"label": followers[0].label}
            )
        question.delete()
        _renumber(draft)


def _renumber(draft: FormVersion) -> None:
    questions = draft.ordered_questions()
    for position, question in enumerate(questions, start=1):
        question.position = position
    Question.objects.bulk_update(questions, ["position"])


# --- Publishing -------------------------------------------------------------------


def publish_problems(draft: FormVersion) -> list[str]:
    """What has to be fixed before the draft can be published."""
    problems: list[str] = []
    questions = draft.ordered_questions()
    if not questions:
        problems.append(_("Add at least one question."))
    seen: dict[UUID, Question] = {}
    for question in questions:
        options = question.option_labels()
        if question.kind in CHOICE_KINDS and len(options) < 2:
            problems.append(
                _("“%(label)s” needs at least two options.") % {"label": question.label}
            )
        if question.show_if_question is not None:
            source = seen.get(question.show_if_question)
            if (
                source is None
                or source.kind not in CHOICE_KINDS
                or question.show_if_option not in source.option_labels()
            ):
                problems.append(
                    _("“%(label)s” follows up on a question or option that isn't above it.")
                    % {"label": question.label}
                )
        seen[question.key] = question
    return problems


def publish(*, actor: User, draft: FormVersion, request: HttpRequest | None = None) -> FormVersion:
    """Make the draft the version people answer from now on."""
    with transaction.atomic():
        draft = (
            FormVersion.objects.select_for_update().select_related("form__center").get(pk=draft.pk)
        )
        _check_draft(draft)
        problems = publish_problems(draft)
        if problems:
            raise BuilderError(" ".join(problems))
        draft.published_at = timezone.now()
        draft.published_by = actor
        draft.save(update_fields=["published_at", "published_by"])
    form = draft.form
    record(
        AuditAction.FORM_PUBLISHED,
        request=request,
        actor=actor,
        center=form.center,
        target=form,
        kind=form.kind,
        version=draft.number,
        questions=draft.questions.count(),
    )
    return draft


def _check_draft(draft: FormVersion) -> None:
    if draft.is_published:
        raise BuilderError(_("This version is published and can't change. Edit the draft."))
