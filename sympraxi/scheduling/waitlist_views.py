"""The waiting list: students ask for a time; the front desk can see and clear the list."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action
from sympraxi.scheduling import availability, waitlist
from sympraxi.scheduling.forms import DateInput, TimeInput
from sympraxi.scheduling.models import (
    ACTIVE_WAITING,
    Modality,
    Schedule,
    TutorAssignment,
    Visibility,
    WaitingEntry,
)
from sympraxi.scheduling.views import center_for, crumbs, signed_in


def waiting_schedules(center: Center) -> list[Schedule]:
    return list(
        Schedule.objects.filter(center=center, waitlist_enabled=True)
        .exclude(visibility=Visibility.HIDDEN)
        .select_related("center")
    )


class JoinForm(forms.Form):
    schedule = forms.ChoiceField(label=gettext_lazy("Schedule"))
    day = forms.DateField(label=gettext_lazy("Day"), widget=DateInput())
    earliest = forms.TimeField(label=gettext_lazy("No earlier than"), widget=TimeInput())
    latest = forms.TimeField(label=gettext_lazy("Finishing by"), widget=TimeInput())
    minutes = forms.ChoiceField(label=gettext_lazy("How long"))
    modality = forms.ChoiceField(label=gettext_lazy("How you'd meet"), choices=Modality.choices)
    tutor = forms.ChoiceField(
        label=gettext_lazy("Tutor"),
        required=False,
        help_text=gettext_lazy("Leave on any tutor to be offered more times."),
    )

    def __init__(self, *args: Any, schedules: list[Schedule], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.by_id = {str(s.pk): s for s in schedules}
        cast(forms.ChoiceField, self.fields["schedule"]).choices = [
            (pk, s.name) for pk, s in self.by_id.items()
        ]
        lengths = sorted({m for s in schedules for m in availability.lengths_offered(s)})
        cast(forms.ChoiceField, self.fields["minutes"]).choices = [
            (str(m), _("%(n)s minutes") % {"n": m}) for m in lengths
        ]
        tutors = {
            a.tutor_id: a.tutor
            for a in TutorAssignment.objects.filter(schedule__in=schedules, is_active=True)
            .select_related("tutor")
            .order_by("tutor__full_name")
        }
        self.tutors = {str(pk): user for pk, user in tutors.items()}
        cast(forms.ChoiceField, self.fields["tutor"]).choices = [("", _("Any tutor"))] + [
            (pk, user.display_name) for pk, user in self.tutors.items()
        ]

    def schedule_chosen(self) -> Schedule:
        return self.by_id[self.cleaned_data["schedule"]]

    def tutor_chosen(self) -> User | None:
        return self.tutors.get(self.cleaned_data.get("tutor") or "")


@login_required
def waiting_list(request: HttpRequest, slug: str) -> HttpResponse:
    """A student's wishes, the time held for them (if any), and a form to ask for another."""
    center = center_for(request, Action.BOOK_OWN, slug)
    me = signed_in(request)
    schedules = waiting_schedules(center)
    here = reverse("scheduling:waiting_list", kwargs={"slug": slug})
    form = JoinForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "join" else None,
        schedules=schedules,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "join" and schedules and form.is_valid():
            try:
                waitlist.join(
                    me,
                    form.schedule_chosen(),
                    day=form.cleaned_data["day"],
                    earliest=form.cleaned_data["earliest"],
                    latest=form.cleaned_data["latest"],
                    minutes=int(form.cleaned_data["minutes"]),
                    modality=form.cleaned_data["modality"],
                    tutor=form.tutor_chosen(),
                    request=request,
                )
            except waitlist.WaitlistError as problem:
                form.add_error(None, str(problem))
            else:
                messages.success(
                    request, _("You're on the waiting list. We'll email you if a time opens.")
                )
                return redirect(here)
        elif action in ("accept", "decline", "leave"):
            entry = WaitingEntry.objects.filter(
                student=me, schedule__center=center, pk=_uuid(request.POST.get("entry"))
            ).first()
            if entry is None:
                messages.error(request, _("That request is no longer on your list."))
                return redirect(here)
            if action == "accept":
                try:
                    appointment = waitlist.accept(entry, request=request)
                except waitlist.WaitlistError as problem:
                    messages.error(request, str(problem))
                    return redirect(here)
                messages.success(request, _("The time is yours. You're booked."))
                return redirect(
                    reverse(
                        "scheduling:appointment",
                        kwargs={"slug": slug, "appointment_id": appointment.pk},
                    )
                )
            if action == "decline":
                waitlist.decline(entry, request=request)
                messages.info(request, _("The time is released for the next person."))
            else:
                waitlist.leave(entry, actor=me, request=request)
                messages.info(request, _("You're off the waiting list for that day."))
            return redirect(here)
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "has_lists": bool(schedules),
        "entries": list(waitlist.active_for(me, center)),
        "active_statuses": ACTIVE_WAITING,
        "crumbs": crumbs(center, (_("Waiting list"), here)),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/waiting_list.html", context)


@login_required
def waiting_list_manage(request: HttpRequest, slug: str) -> HttpResponse:
    """Everyone waiting on the center's schedules, for the front desk."""
    center = center_for(request, Action.BOOK_FOR_OTHERS, slug)
    here = reverse("scheduling:waiting_list_manage", kwargs={"slug": slug})
    if request.method == "POST":
        entry = WaitingEntry.objects.filter(
            schedule__center=center, pk=_uuid(request.POST.get("entry"))
        ).first()
        if entry is not None and waitlist.leave(entry, actor=signed_in(request), request=request):
            messages.success(request, _("Removed from the waiting list."))
        return redirect(here)
    entries = (
        WaitingEntry.objects.filter(schedule__center=center, status__in=ACTIVE_WAITING)
        .select_related("student", "schedule", "tutor", "hold", "hold__tutor")
        .order_by("schedule__name", "day", "joined_at")
    )
    context = {
        "center": center,
        "entries": entries,
        "crumbs": crumbs(center, (_("Waiting list"), here)),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/waiting_list_manage.html", context)


def _uuid(raw: str | None) -> UUID:
    """The UUID in a posted value; one that never matches a row if it isn't one."""
    try:
        return UUID(raw or "")
    except ValueError:
        return UUID(int=0)
