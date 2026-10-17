"""Answering a form version on a page, follow-up questions included.

A follow-up shows only when the question it depends on was answered with its
option. Without JavaScript the server works that out. Each page carries a hidden
list of the questions it showed. When the answers posted reveal a follow-up the
page didn't show, nothing is saved: the page comes back with the follow-up for
the person to answer (the "Continue" step). A "Continue" button does the same on
purpose. With htmx, changing a choice that has follow-ups posts the form with
`continue` set and swaps in the new set of questions in place.

Answers to questions that end up hidden are dropped, so a response never holds
an answer to a question the person didn't see.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.http import QueryDict
from django.utils.safestring import SafeString
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from sympraxi.forms.models import (
    CHOICE_KINDS,
    LONG_TEXT_LENGTH,
    SHORT_TEXT_LENGTH,
    FormKind,
    FormVersion,
    Question,
    QuestionKind,
)
from sympraxi.forms.services import AnswerValue

# The submit button (and the name htmx posts) that shows follow-ups without saving.
CONTINUE = "continue"

DIRECTORS_ONLY = gettext_lazy("Only the center's directors see your answer.")
STUDENT_SEES = gettext_lazy("The student sees your answer, with the summary.")


def field_name(question: Question) -> str:
    return f"q-{question.key.hex}"


def visible(questions: list[Question], chosen: dict[UUID, set[UUID]]) -> list[Question]:
    """The questions that show, in order, given the options chosen so far.

    A follow-up shows when the question it depends on shows and was answered
    with its option. Follow-ups always come after their question, so one pass works.
    """
    shown: list[Question] = []
    keys: set[UUID] = set()
    for question in questions:
        if question.show_if_question is not None and (
            question.show_if_question not in keys
            or question.show_if_option not in chosen.get(question.show_if_question, set())
        ):
            continue
        shown.append(question)
        keys.add(question.key)
    return shown


def _hex_keys(values: list[str], allowed: set[UUID]) -> set[UUID]:
    keys = set()
    for value in values:
        try:
            key = UUID(hex=value)
        except ValueError:
            continue
        if key in allowed:
            keys.add(key)
    return keys


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self) -> None:
        super().__init__(format="%Y-%m-%d")


def _field(question: Question, *, required: bool) -> forms.Field:
    label = question.label
    help_text = question.help_text
    if question.kind == QuestionKind.LONG_TEXT:
        return forms.CharField(
            label=label,
            help_text=help_text,
            required=required,
            max_length=LONG_TEXT_LENGTH,
            widget=forms.Textarea(attrs={"rows": 4}),
        )
    if question.kind in (QuestionKind.ONE_CHOICE, QuestionKind.YES_NO):
        return forms.ChoiceField(
            label=label,
            help_text=help_text,
            required=required,
            choices=[(o.key.hex, o.label) for o in question.options.all()],
            widget=forms.RadioSelect,
        )
    if question.kind == QuestionKind.SEVERAL_CHOICES:
        return forms.MultipleChoiceField(
            label=label,
            help_text=help_text,
            required=required,
            choices=[(o.key.hex, o.label) for o in question.options.all()],
            widget=forms.CheckboxSelectMultiple,
        )
    if question.kind == QuestionKind.NUMBER:
        return forms.DecimalField(
            label=label,
            help_text=help_text,
            required=required,
            max_digits=15,
            decimal_places=4,
            widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}),
        )
    if question.kind == QuestionKind.DATE:
        return forms.DateField(
            label=label, help_text=help_text, required=required, widget=DateInput()
        )
    return forms.CharField(
        label=label, help_text=help_text, required=required, max_length=SHORT_TEXT_LENGTH
    )


def _initial(question: Question, value: AnswerValue) -> Any:
    """A stored value as the field's initial value, dropping options this version lacks."""
    if question.kind in CHOICE_KINDS:
        chosen = value if isinstance(value, list) else []
        keys = [k.hex for k in chosen if k in question.option_labels()]
        if question.kind == QuestionKind.SEVERAL_CHOICES:
            return keys
        return keys[0] if keys else None
    if isinstance(value, Decimal | date | str):
        # A string for a number or a date is what someone typed, shown back to them.
        return value
    return None


@dataclass
class Row:
    """One question as the page shows it."""

    question: Question
    field: forms.BoundField
    # Changing this answer can show or hide follow-ups.
    leads_on: bool
    note: str
    # Required, but only when the session record is finalized.
    needed_to_finalize: bool = False

    @property
    def is_choice(self) -> bool:
        return self.question.kind in CHOICE_KINDS

    @property
    def is_multiple(self) -> bool:
        return self.question.kind == QuestionKind.SEVERAL_CHOICES

    @property
    def described_by(self) -> str:
        """The ids of the hint, the note and the error, for aria-describedby."""
        base = self.field.auto_id
        ids = []
        if self.field.help_text:
            ids.append(f"{base}_helptext")
        if self.note:
            ids.append(f"{base}_note")
        if self.field.errors:
            ids.append(f"{base}_error")
        return " ".join(ids)

    def widget(self) -> SafeString:
        """The input for a question that isn't a choice, with USWDS classes and ARIA."""
        textarea = self.question.kind == QuestionKind.LONG_TEXT
        classes = "usa-textarea" if textarea else "usa-input"
        attrs: dict[str, str | bool] = {}
        if self.described_by:
            attrs["aria-describedby"] = self.described_by
        if self.field.errors:
            classes += " usa-input--error"
            attrs["aria-invalid"] = "true"
        attrs["class"] = classes
        return self.field.as_widget(attrs=attrs)


class Questions:
    """One form version's questions on a page.

    `data` is the posted form, or None for a fresh page. `initial` holds earlier
    answers by question key, to start from. With `skippable`, a staff member can
    leave every question blank to skip the form; answering any of them makes the
    required ones required again. Without `enforce_required`, required questions
    may be left blank for now and are marked as needed to finalize (a draft
    session record checks them when it is finalized).
    """

    def __init__(
        self,
        version: FormVersion,
        *,
        prefix: str,
        data: QueryDict | None = None,
        initial: dict[UUID, AnswerValue] | None = None,
        skippable: bool = False,
        enforce_required: bool = True,
    ) -> None:
        self.version = version
        self.prefix = prefix
        self.data = data
        self.enforce_required = enforce_required
        self.all = version.ordered_questions()
        self.has_follow_ups = any(q.is_follow_up for q in self.all)
        initial = initial or {}
        if data is None:
            chosen = {
                q.key: set(v)
                for q in self.all
                if q.kind in CHOICE_KINDS and isinstance(v := initial.get(q.key), list)
            }
            before = None
        else:
            chosen = self._chosen(data)
            before = _hex_keys(data.get(self.shown_name, "").split(","), {q.key for q in self.all})
        self.shown = visible(self.all, chosen)
        shown_keys = {q.key for q in self.shown}
        self.revealed = [] if before is None else [q for q in self.shown if q.key not in before]
        self.removed = 0 if before is None else len(before - shown_keys)
        self.skipped = skippable and data is not None and not self._answered_any(data)
        self.form = forms.Form(
            data,
            prefix=prefix,
            initial={
                field_name(q): _initial(q, initial[q.key]) for q in self.shown if q.key in initial
            },
        )
        for question in self.shown:
            self.form.fields[field_name(question)] = _field(
                question, required=question.required and not self.skipped and enforce_required
            )

    @property
    def shown_name(self) -> str:
        return f"{self.prefix}-shown"

    @property
    def shown_value(self) -> str:
        return ",".join(q.key.hex for q in self.shown)

    @property
    def needs_another_look(self) -> bool:
        """Follow-ups appeared that the page didn't show, so nothing can be saved yet."""
        return bool(self.revealed)

    @property
    def status(self) -> str:
        """What changed in the questions, for the page's status line."""
        parts = []
        if self.revealed:
            parts.append(
                ngettext(
                    "%(n)s follow-up question appeared.",
                    "%(n)s follow-up questions appeared.",
                    len(self.revealed),
                )
                % {"n": len(self.revealed)}
            )
        if self.removed:
            parts.append(
                ngettext(
                    "%(n)s follow-up question was removed.",
                    "%(n)s follow-up questions were removed.",
                    self.removed,
                )
                % {"n": self.removed}
            )
        return " ".join(parts)

    def rows(self) -> list[Row]:
        sources = {q.show_if_question for q in self.all if q.show_if_question is not None}
        return [
            Row(
                question=q,
                field=self.form[field_name(q)],
                leads_on=q.key in sources,
                note=self._note(q),
                needed_to_finalize=q.required and not self.enforce_required,
            )
            for q in self.shown
        ]

    def _note(self, question: Question) -> str:
        kind = self.version.form.kind
        if kind == FormKind.REGISTRATION and not question.shown_to_staff:
            return str(DIRECTORS_ONLY)
        if kind == FormKind.REPORT and question.shown_to_student:
            return str(STUDENT_SEES)
        return ""

    def is_valid(self) -> bool:
        return self.form.is_valid()

    def values(self) -> dict[UUID, AnswerValue]:
        """The answers given to the questions shown, by key. Blank ones are left out."""
        data = self.form.cleaned_data
        values: dict[UUID, AnswerValue] = {}
        for question in self.shown:
            value = data.get(field_name(question))
            if question.kind in CHOICE_KINDS:
                picked = value if isinstance(value, list) else [value] if value else []
                keys = _hex_keys(picked, set(question.option_labels()))
                order = [o.key for o in question.options.all()]
                if keys:
                    values[question.key] = [k for k in order if k in keys]
            elif question.kind in (QuestionKind.NUMBER, QuestionKind.DATE):
                if value is not None:
                    values[question.key] = value
            elif isinstance(value, str) and value.strip():
                values[question.key] = value.strip()
        return values

    def _chosen(self, data: QueryDict) -> dict[UUID, set[UUID]]:
        chosen = {}
        for question in self.all:
            if question.kind in CHOICE_KINDS:
                name = self.form_name(question)
                chosen[question.key] = _hex_keys(data.getlist(name), set(question.option_labels()))
        return chosen

    def _answered_any(self, data: QueryDict) -> bool:
        return any(any(v.strip() for v in data.getlist(self.form_name(q))) for q in self.shown)

    def form_name(self, question: Question) -> str:
        return f"{self.prefix}-{field_name(question)}"

    def again(self) -> "Questions":
        """The questions redrawn from what was posted, unsaved and without error messages.

        For the Continue step: the follow-ups the answers call for now show.
        """
        data = self.data
        if data is None:
            return self
        posted: dict[UUID, AnswerValue] = {}
        for question in self.all:
            values = [v for v in data.getlist(self.form_name(question)) if v.strip()]
            if question.kind in CHOICE_KINDS:
                posted[question.key] = list(_hex_keys(values, set(question.option_labels())))
            elif values:
                posted[question.key] = values[0]
        redrawn = Questions(
            self.version,
            prefix=self.prefix,
            initial=posted,
            enforce_required=self.enforce_required,
        )
        redrawn.revealed, redrawn.removed = self.revealed, self.removed
        return redrawn


def follow_up_message(submit: str) -> str:
    """What to tell someone whose answers revealed follow-ups they haven't seen yet."""
    return _("Follow-up questions appeared below. Answer them, then press “%(submit)s” again.") % {
        "submit": submit
    }


def any_follow_ups(groups: list[Questions]) -> bool:
    return any(g.has_follow_ups for g in groups)


def status_of(groups: list[Questions]) -> str:
    return " ".join(s for g in groups if (s := g.status))
