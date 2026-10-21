"""The check-in kiosk and the waiting room."""

from datetime import time
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

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
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.scheduling.timeutils import first_instant, local_date
from sympraxi.staff import kiosk, timeclock
from sympraxi.staff.models import Arrival, ArrivalStatus


class CheckInForm(forms.Form):
    student_id = forms.CharField(
        label=gettext_lazy("Student ID"),
        max_length=100,
        widget=forms.TextInput(
            attrs={"autocomplete": "off", "autofocus": True, "autocapitalize": "off"}
        ),
    )
    reason = forms.CharField(
        label=gettext_lazy("What are you working on?"),
        max_length=200,
        required=False,
        help_text=gettext_lazy("Optional. Only if you don't have an appointment."),
    )


def _midnight(center: Center):
    tz = ZoneInfo(center.time_zone)
    return first_instant(local_date(timezone.now(), tz), time(0), tz)


@login_required
def kiosk_page(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.RUN_KIOSK, Center.objects.all(), slug=slug)
    form = CheckInForm(request.POST if request.method == "POST" else None)
    context: dict[str, Any] = {"center": center, "form": form, "seconds": kiosk.RESULT_SECONDS}
    if request.method == "POST" and form.is_valid():
        try:
            timeclock.require_network(center, cast(User, request.user), request)
        except timeclock.TimeClockError:
            context["problem"] = _(
                "Check-in only works from the center's network. Please see the front desk."
            )
            return render(request, "staff/kiosk.html", context)
        student = kiosk.find_student(center, form.cleaned_data["student_id"])
        if student is None:
            context["problem"] = _("We couldn't find that ID. Please see the front desk.")
            context["form"] = CheckInForm()
            return render(request, "staff/kiosk.html", context)
        result = kiosk.check_in(
            center,
            student,
            request=request,
            via="kiosk",
            reason=form.cleaned_data["reason"],
        )
        context.update(
            {
                "checked_in": result,
                "name": kiosk.first_name(student),
                "form": CheckInForm(),
            }
        )
    with timezone.override(center.time_zone):
        return render(request, "staff/kiosk.html", context)


@login_required
def waiting_room(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_WAITING_ROOM, Center.objects.all(), slug=slug)
    here = reverse("staff:waiting_room", kwargs={"slug": slug})
    can_check_in = can(request.user, Action.RUN_KIOSK, center)
    form = CheckInForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") == "check_in"
        else None
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("seen", "left"):
            try:
                arrival = Arrival.objects.get(
                    pk=UUID(request.POST.get("arrival", "")), center=center
                )
            except (Arrival.DoesNotExist, ValueError):
                messages.error(request, _("That student is no longer in the waiting room."))
                return redirect(here)
            done = kiosk.mark(
                arrival,
                ArrivalStatus.SEEN if action == "seen" else ArrivalStatus.LEFT,
                request=request,
            )
            if not done:
                messages.info(request, _("Someone else already updated that."))
            return redirect(here)
        if action == "check_in" and can_check_in and form.is_valid():
            student = kiosk.find_student(center, form.cleaned_data["student_id"])
            if student is None:
                form.add_error("student_id", _("No student in this center has that ID."))
            else:
                result = kiosk.check_in(
                    center,
                    student,
                    request=request,
                    via="staff",
                    reason=form.cleaned_data["reason"],
                )
                name = kiosk.first_name(student)
                if result.already:
                    messages.info(
                        request, _("%(name)s is already in the waiting room.") % {"name": name}
                    )
                else:
                    messages.success(request, _("%(name)s is checked in.") % {"name": name})
                return redirect(here)
    with timezone.override(center.time_zone):
        context = {
            "center": center,
            "waiting": kiosk.waiting_now(center),
            "handled": kiosk.handled_today(center, _midnight(center)),
            "form": form if can_check_in else None,
            "can_check_in": can_check_in,
            "describe": kiosk.describe,
        }
        return render(request, "staff/waiting_room.html", context)
