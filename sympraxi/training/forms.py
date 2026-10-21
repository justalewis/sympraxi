from typing import Any, cast

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.centers.models import Center
from sympraxi.training.models import Item, ItemKind, Module

CHOICE_SLOTS = 5


class ModuleForm(forms.ModelForm):
    class Meta:
        model = Module
        fields = ["title", "summary", "is_published", "is_required"]
        widgets = {"summary": forms.Textarea(attrs={"rows": 3})}


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["kind", "title", "body", "url", "minutes", "pass_percent"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}
        labels = {"body": gettext_lazy("Text (for a page) or a note (for a link)")}
        help_texts = {
            "url": gettext_lazy("For a reading or video elsewhere."),
            "pass_percent": gettext_lazy("For a quiz."),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["url"].required = False
        if not self.instance._state.adding:
            self.fields["kind"].disabled = True

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        kind = data.get("kind") or getattr(self.instance, "kind", "")
        if kind == ItemKind.LINK and not data.get("url"):
            self.add_error("url", _("A reading or video needs an address."))
        if kind == ItemKind.PAGE and not (data.get("body") or "").strip():
            self.add_error("body", _("A page needs some text."))
        return data


class QuestionForm(forms.Form):
    prompt = forms.CharField(
        label=gettext_lazy("Question"), widget=forms.Textarea(attrs={"rows": 2})
    )
    correct = forms.ChoiceField(
        label=gettext_lazy("Which answer is right"),
        choices=[],
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["correct"]).choices = [
            (str(i), _("Answer %(n)s") % {"n": i}) for i in range(1, CHOICE_SLOTS + 1)
        ]
        for i in range(1, CHOICE_SLOTS + 1):
            self.fields[f"choice_{i}"] = forms.CharField(
                label=_("Answer %(n)s") % {"n": i}, max_length=300, required=i <= 2
            )
        order = ["prompt"] + [f"choice_{i}" for i in range(1, CHOICE_SLOTS + 1)] + ["correct"]
        self.order_fields(order)

    def choices(self) -> list[tuple[str, bool]]:
        """The filled-in answers with whether each is right."""
        right = self.cleaned_data["correct"]
        return [
            (self.cleaned_data[f"choice_{i}"].strip(), str(i) == right)
            for i in range(1, CHOICE_SLOTS + 1)
            if self.cleaned_data.get(f"choice_{i}", "").strip()
        ]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        right = data.get("correct")
        if right and not data.get(f"choice_{right}", "").strip():
            self.add_error("correct", _("The right answer has to be one you filled in."))
        return data


class HoursForm(forms.Form):
    training_hours_required = forms.DecimalField(
        label=gettext_lazy("Hours of training a new tutor completes"),
        required=False,
        min_value=0,
        max_digits=6,
        decimal_places=2,
        help_text=gettext_lazy(
            "Counted from time clocked under Training. Leave empty if there is no requirement."
        ),
    )

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        kwargs.setdefault("initial", {"training_hours_required": center.training_hours_required})
        super().__init__(*args, **kwargs)
