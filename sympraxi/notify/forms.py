from datetime import timedelta
from typing import Any, cast

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.centers.models import Center
from sympraxi.notify.models import Announcement
from sympraxi.notify.news import GROUPS, Reach
from sympraxi.scheduling.forms import DateInput
from sympraxi.scheduling.models import Schedule


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "body", "audience", "starts_on", "ends_on"]
        widgets = {"starts_on": DateInput(), "ends_on": DateInput()}
        help_texts = {
            "body": gettext_lazy(
                "Plain text, up to 2,000 characters. Line breaks and web addresses are kept."
            ),
            "ends_on": gettext_lazy("It stops showing after this day."),
        }

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        first, last = data.get("starts_on"), data.get("ends_on")
        if first and last:
            if last < first:
                self.add_error("ends_on", _("The last day can't be before the first day."))
            elif last - first > timedelta(days=366):
                self.add_error("ends_on", _("Show it for a year or less."))
        return data


class MassEmailForm(forms.Form):
    group = forms.ChoiceField(
        label=gettext_lazy("Who it goes to"),
        choices=[(key, label) for key, (label, _roles) in GROUPS.items()],
        initial="students",
    )
    first = forms.DateField(
        label=gettext_lazy("Only those with an appointment from"),
        required=False,
        widget=DateInput(),
        help_text=gettext_lazy(
            "Optional, for students only. Give both days to reach just the students who "
            "booked then."
        ),
    )
    last = forms.DateField(label=gettext_lazy("To"), required=False, widget=DateInput())
    schedule = forms.ChoiceField(label=gettext_lazy("On the schedule"), required=False)
    subject = forms.CharField(label=gettext_lazy("Subject"), max_length=150)
    body = forms.CharField(
        label=gettext_lazy("Message"),
        max_length=10000,
        widget=forms.Textarea(attrs={"rows": 12}),
        help_text=gettext_lazy("Plain text. Each person gets their own copy."),
    )

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        self.schedules = {
            str(s.pk): s
            for s in Schedule.objects.filter(center=center)
            .select_related("term")
            .order_by("-starts_on", "name")
        }
        cast(forms.ChoiceField, self.fields["schedule"]).choices = [
            ("", _("Every schedule")),
            *[(pk, f"{s.name} ({s.term.name})") for pk, s in self.schedules.items()],
        ]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        first, last = data.get("first"), data.get("last")
        if (first is None) != (last is None):
            raise forms.ValidationError(_("Give both a first and a last day, or neither."))
        if first is not None and last is not None:
            if data.get("group") != "students":
                self.add_error("first", _("Appointment days only narrow down students."))
            elif last < first:
                self.add_error("last", _("The last day can't be before the first day."))
            elif (last - first).days > 366:
                self.add_error("last", _("Choose a year or less."))
        if data.get("schedule") and first is None:
            self.add_error("schedule", _("Choose the appointment days too."))
        return data

    def reach(self) -> Reach:
        data = self.cleaned_data
        return Reach(
            group=data["group"],
            first=data.get("first"),
            last=data.get("last"),
            schedule=self.schedules.get(data.get("schedule") or ""),
        )
