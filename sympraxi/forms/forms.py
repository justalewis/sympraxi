"""Django forms for the builder pages. The forms people answer are in answering.py."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Center
from sympraxi.forms.builder import QuestionData, earlier_choice_questions
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Form,
    FormKind,
    FormVersion,
    Question,
    QuestionKind,
)
from sympraxi.scheduling.models import Schedule

REGISTRATION = "registration"
INTAKE_DEFAULT = "intake"


def places_free(center: Center) -> list[tuple[str, str]]:
    """The forms a center doesn't have yet, as choices: registration, default intake,
    and an intake form for each schedule."""
    taken = {(f.kind, f.schedule_id) for f in Form.objects.filter(center=center)}
    choices = []
    if (FormKind.REGISTRATION, None) not in taken:
        choices.append((REGISTRATION, str(_("Registration: asked the first time a student books"))))
    if (FormKind.INTAKE, None) not in taken:
        choices.append(
            (INTAKE_DEFAULT, str(_("Intake: asked when booking on any schedule without its own")))
        )
    for schedule in Schedule.objects.filter(center=center).order_by("name"):
        if (FormKind.INTAKE, schedule.pk) not in taken:
            choices.append(
                (
                    str(schedule.pk),
                    str(_("Intake: asked when booking on %(name)s")) % {"name": schedule.name},
                )
            )
    return choices


class NewFormForm(forms.Form):
    place = forms.ChoiceField(label=_("Which form"))

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        cast(forms.ChoiceField, self.fields["place"]).choices = places_free(center)

    def kind_and_schedule(self) -> tuple[str, Schedule | None]:
        place = self.cleaned_data["place"]
        if place == REGISTRATION:
            return FormKind.REGISTRATION, None
        if place == INTAKE_DEFAULT:
            return FormKind.INTAKE, None
        return FormKind.INTAKE, Schedule.objects.get(center=self.center, pk=UUID(place))


class IntroForm(forms.ModelForm):
    class Meta:
        model = FormVersion
        fields = ["intro"]
        widgets = {"intro": forms.Textarea(attrs={"rows": 3})}


class QuestionForm(forms.Form):
    """Add or change one question in a draft.

    Each existing option has a field named `option-<key>` for its wording, a
    `place-<key>` to reorder it and a `remove-<key>` box. New options are typed
    one per line and go after the others.
    """

    label = forms.CharField(label=_("Question"), max_length=500)
    help_text = forms.CharField(
        label=_("Hint"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Shown under the question, for example what kind of answer helps."),
    )
    kind = forms.ChoiceField(label=_("Kind of answer"), choices=QuestionKind.choices)
    new_options = forms.CharField(
        label=_("Add options, one per line"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    required = forms.BooleanField(label=_("An answer is required"), required=False)
    is_demographic = forms.BooleanField(
        label=_("This asks about who the student is, so it's always optional"),
        required=False,
        help_text=_("For example gender, race or ethnicity, age, or first-generation status."),
    )
    shown_to_staff = forms.BooleanField(
        label=_("Tutors and front desk staff see the answer"),
        required=False,
        help_text=_("Directors see every answer either way."),
    )
    condition = forms.ChoiceField(label=_("When to show this question"), required=False)

    def __init__(
        self,
        *args: Any,
        draft: FormVersion,
        question: Question | None,
        kind_locked: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.draft = draft
        self.question = question
        if draft.form.kind != FormKind.REGISTRATION:
            del self.fields["shown_to_staff"]
        if kind_locked:
            kind = self.fields["kind"]
            kind.disabled = True
            kind.help_text = _(
                "People may have answered this question, so its kind of answer can't change. "
                "To ask for a different kind of answer, remove it and add a new question."
            )
        sources = earlier_choice_questions(draft, question)
        choices: list[Any] = [("", _("Always"))]
        for source in sources:
            choices.append(
                (
                    source.label,
                    [
                        (
                            f"{source.key.hex}:{o.key.hex}",
                            _("Only when “%(question)s” is answered “%(option)s”")
                            % {"question": source.label, "option": o.label},
                        )
                        for o in source.options.all()
                    ],
                )
            )
        condition = cast(forms.ChoiceField, self.fields["condition"])
        condition.choices = choices
        if not sources:
            condition.help_text = _(
                "A question can follow up on a choice question above it. There isn't one yet."
            )
        self.options = list(question.options.order_by("position")) if question else []
        places = [(str(n), str(n)) for n in range(1, len(self.options) + 1)]
        for number, option in enumerate(self.options, start=1):
            key = option.key.hex
            self.fields[f"option-{key}"] = forms.CharField(
                label=_("Option %(n)s") % {"n": number},
                max_length=300,
                required=False,
                initial=option.label,
            )
            self.fields[f"place-{key}"] = forms.ChoiceField(
                label=_("Place of option %(n)s") % {"n": number},
                choices=places,
                initial=str(number),
            )
            self.fields[f"remove-{key}"] = forms.BooleanField(
                label=_("Remove option %(n)s") % {"n": number}, required=False
            )

    def option_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "label": self[f"option-{o.key.hex}"],
                "place": self[f"place-{o.key.hex}"],
                "remove": self[f"remove-{o.key.hex}"],
            }
            for o in self.options
        ]

    def clean_condition(self) -> tuple[UUID, UUID] | None:
        value = self.cleaned_data.get("condition") or ""
        if not value:
            return None
        source, option = value.split(":")
        return UUID(hex=source), UUID(hex=option)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("is_demographic") and data.get("required"):
            self.add_error("required", _("Questions about who the student is are always optional."))
        return data

    def question_data(self) -> QuestionData:
        data = self.cleaned_data
        # A disabled field takes its initial value, which the page sets from the question.
        kind = data["kind"]
        options: list[tuple[UUID | None, str]] = []
        if kind in CHOICE_KINDS and kind != QuestionKind.YES_NO:
            kept = [o for o in self.options if not data.get(f"remove-{o.key.hex}")]
            order = {o.key: i for i, o in enumerate(self.options)}
            kept.sort(key=lambda o: (int(data.get(f"place-{o.key.hex}") or 0), order[o.key]))
            options = [(o.key, data.get(f"option-{o.key.hex}", "")) for o in kept]
            options += [(None, line) for line in data.get("new_options", "").splitlines()]
        return QuestionData(
            kind=kind,
            label=data["label"],
            help_text=data.get("help_text", ""),
            required=bool(data.get("required")),
            is_demographic=bool(data.get("is_demographic")),
            shown_to_staff=bool(data.get("shown_to_staff")),
            condition=data.get("condition"),
            options=options,
        )
