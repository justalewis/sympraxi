"""The director's page for moving a day's in-person appointments online."""

from datetime import timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action
from sympraxi.scheduling import online_switch
from sympraxi.scheduling.forms import DateInput
from sympraxi.scheduling.models import Schedule
from sympraxi.scheduling.timeutils import local_date
from sympraxi.scheduling.views import center_for, crumbs


class SwitchOnlineForm(forms.Form):
    day = forms.DateField(label=gettext_lazy("Day"), widget=DateInput())
    schedule = forms.ChoiceField(label=gettext_lazy("Schedule"), required=False)
    note = forms.CharField(
        label=gettext_lazy("A note for the email"),
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=gettext_lazy("For example: The library is closed because of snow."),
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

    def clean_day(self) -> Any:
        day = self.cleaned_data["day"]
        today = local_date(timezone.now(), ZoneInfo(self.center.time_zone))
        if day < today:
            raise forms.ValidationError(_("That day is over."))
        if day > today + timedelta(days=366):
            raise forms.ValidationError(_("Choose a day within a year."))
        return day

    def chosen_schedule(self) -> Schedule | None:
        return self.schedules.get(self.cleaned_data.get("schedule") or "")


@login_required
def switch_online(request: HttpRequest, slug: str) -> HttpResponse:
    """Choose the day, see what would move, then move it."""
    center = center_for(request, Action.MANAGE_SCHEDULES, slug)
    form = SwitchOnlineForm(request.POST or None, center=center)
    title = _("Move a day online")
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "title": title,
        "crumbs": crumbs(center)[:1],
    }
    if request.method != "POST" or not form.is_valid() or "edit" in request.POST:
        return render(request, "scheduling/switch_online.html", context)
    day = form.cleaned_data["day"]
    schedule = form.chosen_schedule()
    if "confirm" in request.POST:
        moved = online_switch.switch(
            center,
            day,
            schedule=schedule,
            note=form.cleaned_data["note"],
            actor=cast(User, request.user),
            request=request,
        )
        messages.success(
            request,
            ngettext(
                "Moved %(n)s appointment online. Its students and tutor are being emailed.",
                "Moved %(n)s appointments online. Their students and tutors are being emailed.",
                moved,
            )
            % {"n": moved},
        )
        return redirect("scheduling:schedules", slug=slug)
    found = online_switch.plan(center, day, schedule)
    context.update(
        {
            "title": _("Check what moves online"),
            "day": day,
            "schedule": schedule,
            "plan": found,
        }
    )
    with timezone.override(center.time_zone):
        return render(request, "scheduling/switch_online_review.html", context)
