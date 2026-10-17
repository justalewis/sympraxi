from datetime import date
from typing import Any, cast

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.privacy.models import Disclosure
from sympraxi.scheduling.forms import DateInput


class DisclosureForm(forms.ModelForm):
    class Meta:
        model = Disclosure
        fields = [
            "disclosed",
            "requested_on",
            "disclosed_on",
            "party",
            "interest",
            "basis",
            "records",
            "note",
            "corrects",
        ]
        widgets = {
            "requested_on": DateInput(),
            "disclosed_on": DateInput(),
            "interest": forms.Textarea(attrs={"rows": 3}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "disclosed": gettext_lazy(
                "Leave unchecked for a request that was refused or is still open."
            ),
            "party": gettext_lazy("A person, an office or an organization."),
            "interest": gettext_lazy(
                "Why they asked, and why they had a right to the records. For a health or "
                "safety emergency, describe the threat."
            ),
            "records": gettext_lazy("For example: session records from fall 2026."),
            "corrects": gettext_lazy(
                "Entries can't be changed. To fix one, add a new entry that corrects it."
            ),
        }

    def __init__(self, *args: Any, earlier: Any, today: date, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.today = today
        corrects = cast(forms.ModelChoiceField, self.fields["corrects"])
        corrects.queryset = earlier
        corrects.required = False
        corrects.empty_label = _("Nothing, this is a new entry")

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        requested, disclosed_on = data.get("requested_on"), data.get("disclosed_on")
        if data.get("disclosed") and disclosed_on is None:
            self.add_error("disclosed_on", _("Give the day the records were disclosed."))
        if not data.get("disclosed") and disclosed_on is not None:
            self.add_error("disclosed", _("Check this box if the records were disclosed."))
        if requested is None and disclosed_on is None:
            self.add_error("requested_on", _("Give the day it was asked for or disclosed."))
        for name in ("requested_on", "disclosed_on"):
            day = data.get(name)
            if day is not None and day > self.today:
                self.add_error(name, _("That day hasn't come yet."))
        if requested and disclosed_on and disclosed_on < requested:
            self.add_error("disclosed_on", _("It can't be disclosed before it was asked for."))
        return data
