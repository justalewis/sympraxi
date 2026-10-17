from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from sympraxi.centers.models import Role
from sympraxi.centers.people import MAX_ROWS, Checker, PersonRow, taken_student_ids

WELCOME_LABEL = _("Email each person to say they've been added and how to sign in")


class PersonForm(forms.Form):
    """Add one person in one role."""

    email = forms.EmailField(label=_("Email address"), max_length=254)
    full_name = forms.CharField(label=_("Full name"), max_length=200)
    preferred_name = forms.CharField(label=_("Preferred name"), max_length=100, required=False)
    pronouns = forms.CharField(label=_("Pronouns"), max_length=50, required=False)
    student_id = forms.CharField(label=_("Student ID"), max_length=64, required=False)
    role = forms.ChoiceField(label=_("Role"), choices=Role.choices, initial=Role.STUDENT)
    welcome = forms.BooleanField(label=WELCOME_LABEL, required=False, initial=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.person: PersonRow | None = None

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if self.errors:
            return data
        values = {k: data.get(k) or "" for k in self.fields if k not in ("role", "welcome")}
        checker = Checker(Role(data["role"]), taken_student_ids([values["student_id"]]))
        result = checker.check(1, values)
        if isinstance(result, PersonRow):
            self.person = result
        else:
            raise forms.ValidationError(result.problems)
        return data


class ImportForm(forms.Form):
    file = forms.FileField(
        label=_("CSV file"),
        help_text=_(
            "The first line names the columns. Email and Full name (or First name and Last "
            "name) are needed; Preferred name, Pronouns, Student ID and Role are optional. "
            "Up to %(n)s people a file."
        )
        % {"n": MAX_ROWS},
    )
    role = forms.ChoiceField(
        label=_("Role for lines that don't name one"),
        choices=[("", _("Every line names its role")), *Role.choices],
        initial=Role.STUDENT,
        required=False,
    )
    welcome = forms.BooleanField(label=WELCOME_LABEL, required=False, initial=False)

    def default_role(self) -> Role | None:
        value = self.cleaned_data.get("role")
        return Role(value) if value else None


class RolesOffForm(forms.Form):
    role = forms.ChoiceField(label=_("Turn off every"), choices=Role.choices)
