from typing import Any, cast
from zoneinfo import ZoneInfo

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.scheduling.forms import DateInput, TimeInput
from sympraxi.scheduling.timeutils import first_instant, local_date
from sympraxi.workshops.models import Workshop, WorkshopStatus


class WorkshopForm(forms.Form):
    title = forms.CharField(label=gettext_lazy("Title"), max_length=200)
    description = forms.CharField(
        label=gettext_lazy("What it covers"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    day = forms.DateField(label=gettext_lazy("Day"), widget=DateInput())
    start = forms.TimeField(label=gettext_lazy("Starts"), widget=TimeInput())
    end = forms.TimeField(label=gettext_lazy("Ends"), widget=TimeInput())
    location = forms.CharField(label=gettext_lazy("Where"), required=False, max_length=200)
    meeting_url = forms.URLField(label=gettext_lazy("Meeting link"), required=False)
    leader = forms.ChoiceField(label=gettext_lazy("Led by"), required=False)
    capacity = forms.IntegerField(
        label=gettext_lazy("Seats"),
        required=False,
        min_value=1,
        max_value=1000,
        help_text=gettext_lazy("Leave empty for no limit."),
    )
    status = forms.ChoiceField(
        label=gettext_lazy("Who can see it"),
        choices=[
            (WorkshopStatus.DRAFT, gettext_lazy("Draft: only staff see it")),
            (WorkshopStatus.PUBLISHED, gettext_lazy("Published: students can register")),
        ],
        initial=WorkshopStatus.DRAFT,
    )

    def __init__(self, *args: Any, center: Center, workshop: Workshop | None = None, **kwargs: Any):
        if workshop is not None and not args and "initial" not in kwargs:
            tz = ZoneInfo(center.time_zone)
            start = workshop.starts_at.astimezone(tz)
            kwargs["initial"] = {
                "title": workshop.title,
                "description": workshop.description,
                "day": local_date(workshop.starts_at, tz),
                "start": start.time().replace(second=0, microsecond=0),
                "end": workshop.ends_at.astimezone(tz).time().replace(second=0, microsecond=0),
                "location": workshop.location,
                "meeting_url": workshop.meeting_url,
                "leader": str(workshop.leader_id) if workshop.leader_id else "",
                "capacity": workshop.capacity,
                "status": workshop.status,
            }
        super().__init__(*args, **kwargs)
        self.center = center
        self.workshop = workshop
        leaders = (
            User.objects.filter(
                memberships__center=center,
                memberships__is_active=True,
                memberships__role__in=[Role.TUTOR, Role.DIRECTOR, Role.FRONT_DESK],
                is_active=True,
            )
            .distinct()
            .order_by("full_name")
        )
        self.leaders = {str(u.pk): u for u in leaders}
        cast(forms.ChoiceField, self.fields["leader"]).choices = [("", _("Not chosen yet"))] + [
            (pk, u.display_name) for pk, u in self.leaders.items()
        ]
        if workshop is not None and workshop.is_canceled:
            for field in self.fields.values():
                field.disabled = True

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        day, start, end = data.get("day"), data.get("start"), data.get("end")
        if day and start and end:
            tz = ZoneInfo(self.center.time_zone)
            starts, ends = first_instant(day, start, tz), first_instant(day, end, tz)
            if ends <= starts:
                self.add_error("end", _("The end must be after the start."))
            else:
                data["starts_at"], data["ends_at"] = starts, ends
        if data.get("capacity") and self.workshop is not None:
            taken = self.workshop.taken()
            if data["capacity"] < taken:
                self.add_error(
                    "capacity",
                    _("%(n)s students are already registered; the seats can't be fewer.")
                    % {"n": taken},
                )
        return data

    def leader_chosen(self) -> User | None:
        return self.leaders.get(self.cleaned_data.get("leader") or "")


class CancelForm(forms.Form):
    reason = forms.CharField(
        label=gettext_lazy("Why it's canceled"),
        max_length=300,
        help_text=gettext_lazy("Registered students are emailed that it's canceled."),
    )


class AddStudentForm(forms.Form):
    student = forms.CharField(label=gettext_lazy("Student's email or student ID"), max_length=254)
