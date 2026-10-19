from datetime import date
from typing import Any, cast

from django import forms
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.feedback import services
from sympraxi.feedback.models import QueueSettings, Submission
from sympraxi.files import services as file_services


def _file_help() -> str:
    return gettext("PDF, Word, OpenDocument, rich text or plain text, up to %(n)s MB.") % {
        "n": file_services.max_bytes() // (1024 * 1024)
    }


class SubmitForm(forms.Form):
    title = forms.CharField(
        label=_("Title"),
        max_length=200,
        help_text=_("For example: Essay 2, first draft."),
    )
    course = forms.CharField(
        label=_("Course"), max_length=100, required=False, help_text=_("Optional.")
    )
    wanted = forms.CharField(
        label=Submission._meta.get_field("wanted").verbose_name,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=Submission._meta.get_field("wanted").help_text,
    )
    day = forms.DateField(label=_("Needed by"), widget=forms.DateInput(attrs={"type": "date"}))
    file = forms.FileField(label=_("Your draft"))

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        first = services.earliest_day(center)
        last = services.latest_day(center)
        day = cast(forms.DateField, self.fields["day"])
        day.widget.attrs.update({"min": first.isoformat(), "max": last.isoformat()})
        day.initial = first
        day.help_text = gettext(
            "The day you need the feedback back. This center needs at least %(n)s days."
        ) % {"n": services.settings_for(center).lead_days}
        self.fields["file"].help_text = _file_help()

    def clean_day(self) -> date:
        day: date = self.cleaned_data["day"]
        if not services.earliest_day(self.center) <= day <= services.latest_day(self.center):
            raise forms.ValidationError(
                gettext("Choose a day at least %(n)s days from now, and within a year.")
                % {"n": services.settings_for(self.center).lead_days}
            )
        return day


class FileForm(forms.Form):
    file = forms.FileField(label=_("File"))

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["file"].help_text = _file_help()


class AssignForm(forms.Form):
    tutor = forms.ChoiceField(label=_("Give it to"))

    def __init__(self, *args: Any, people: list[User], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.people = {str(p.pk): p for p in people}
        field = cast(forms.ChoiceField, self.fields["tutor"])
        field.choices = [(pk, p.display_name) for pk, p in self.people.items()]

    def chosen(self) -> User:
        return self.people[self.cleaned_data["tutor"]]


class QueueSettingsForm(forms.ModelForm[QueueSettings]):
    class Meta:
        model = QueueSettings
        fields = ["is_open", "lead_days", "max_files", "auto_assign", "max_open_per_tutor"]


class RangeForm(forms.Form):
    start = forms.DateField(label=_("From"), widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label=_("Through"), widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end = data.get("start"), data.get("end")
        if start and end and end < start:
            self.add_error("end", gettext("Choose a day on or after the first day."))
        return data
