"""Early alerts for staff and shared visit confirmations for students and instructors."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.alerts import services
from sympraxi.alerts.models import Alert, AlertKind, AlertStatus, VisitShare
from sympraxi.centers.models import Center, Membership
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.scheduling.models import Appointment, Participant


def _me(request: HttpRequest) -> User:
    return cast(User, request.user)


def _uuid(raw: str | None) -> UUID:
    try:
        return UUID(raw or "")
    except ValueError:
        return UUID(int=0)


class AlertForm(forms.Form):
    kind = forms.ChoiceField(label=gettext_lazy("What it's about"), choices=AlertKind.choices)
    reason = forms.CharField(
        label=gettext_lazy("Why you're raising it"),
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=gettext_lazy(
            "In your own words, what you saw or heard. Only directors and you can read this."
        ),
    )
    student_informed = forms.BooleanField(
        label=gettext_lazy("The student knows I'm raising this"),
        required=False,
        help_text=gettext_lazy(
            "Tick this if you have told the student. It is recorded either way."
        ),
    )


class CloseForm(forms.Form):
    outcome = forms.CharField(
        label=gettext_lazy("What was done"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=gettext_lazy("Needed to close the alert, and kept with it."),
    )


class ShareForm(forms.Form):
    instructor_email = forms.EmailField(label=gettext_lazy("Your instructor's email"))
    course = forms.CharField(label=gettext_lazy("Course"), required=False, max_length=100)


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url()), *extra]


# --- Raising and handling alerts ----------


@login_required
def raise_alert(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    membership = get_for_action(
        request.user,
        Action.RAISE_ALERT,
        Membership.objects.select_related("center", "user").filter(
            center__slug=slug, role="student"
        ),
        pk=membership_id,
    )
    center = membership.center
    form = AlertForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            alert = services.raise_alert(
                center,
                membership.user,
                _me(request),
                kind=form.cleaned_data["kind"],
                reason=form.cleaned_data["reason"],
                student_informed=form.cleaned_data["student_informed"],
                request=request,
            )
        except services.AlertError as problem:
            form.add_error("reason", str(problem))
        else:
            messages.success(request, _("The alert is raised. Directors have been told."))
            return redirect(reverse("alerts:detail", kwargs={"slug": slug, "alert_id": alert.pk}))
    context = {
        "center": center,
        "student": membership.user,
        "form": form,
        "crumbs": _crumbs(center),
    }
    return render(request, "alerts/raise.html", context)


@login_required
def alert_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.HANDLE_ALERTS, Center.objects.all(), slug=slug)
    rows = Alert.objects.filter(center=center).select_related("student", "raised_by")
    context = {
        "center": center,
        "open": [a for a in rows if a.status != AlertStatus.CLOSED],
        "closed": [a for a in rows if a.status == AlertStatus.CLOSED][:50],
        "crumbs": _crumbs(center),
    }
    return render(request, "alerts/list.html", context)


@login_required
def my_alerts(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.RAISE_ALERT, Center.objects.all(), slug=slug)
    rows = Alert.objects.filter(center=center, raised_by=_me(request)).select_related("student")
    context = {"center": center, "alerts": list(rows), "crumbs": _crumbs(center)}
    return render(request, "alerts/mine.html", context)


@login_required
def alert_detail(request: HttpRequest, slug: str, alert_id: UUID) -> HttpResponse:
    alert = get_for_action(
        request.user,
        Action.VIEW_ALERT,
        Alert.objects.select_related(
            "center", "student", "raised_by", "acknowledged_by", "closed_by"
        ).filter(center__slug=slug),
        pk=alert_id,
    )
    center = alert.center
    me = _me(request)
    handling = can(me, Action.HANDLE_ALERTS, alert)
    here = reverse("alerts:detail", kwargs={"slug": slug, "alert_id": alert.pk})
    close_form = CloseForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "close" else None
    )
    if request.method == "POST":
        if not handling:
            raise Http404
        action = request.POST.get("action")
        if action == "acknowledge":
            services.acknowledge(alert, me, request=request)
            return redirect(here)
        if action == "close" and close_form.is_valid():
            try:
                services.close(
                    alert, me, outcome=close_form.cleaned_data["outcome"], request=request
                )
            except services.AlertError as problem:
                close_form.add_error("outcome", str(problem))
            else:
                messages.success(request, _("Closed."))
                return redirect(here)
    context: dict[str, Any] = {
        "center": center,
        "alert": alert,
        "handling": handling,
        "close_form": close_form,
        "crumbs": _crumbs(center),
    }
    return render(request, "alerts/detail.html", context)


# --- Shared visits ----------


@login_required
def share_visit(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = get_for_action(
        request.user,
        Action.SHARE_VISIT,
        Appointment.objects.select_related("schedule__center").filter(schedule__center__slug=slug),
        pk=appointment_id,
    )
    center = appointment.schedule.center
    me = _me(request)
    participant = Participant.objects.filter(appointment=appointment, student=me).first()
    form = ShareForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and participant is not None:
        try:
            share, _token = services.share_visit(
                participant,
                instructor_email=form.cleaned_data["instructor_email"],
                course=form.cleaned_data["course"],
                request=request,
            )
        except services.AlertError as problem:
            form.add_error(None, str(problem))
        else:
            messages.success(
                request,
                _("Sent. %(email)s now has a link that confirms this visit.")
                % {"email": share.instructor_email},
            )
            return redirect(reverse("alerts:shares", kwargs={"slug": slug}))
    context = {
        "center": center,
        "appointment": appointment,
        "form": form,
        "enabled": center.visit_sharing_enabled,
        "crumbs": _crumbs(center),
    }
    return render(request, "alerts/share.html", context)


@login_required
def my_shares(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.SHARE_VISIT, Center.objects.all(), slug=slug)
    me = _me(request)
    here = reverse("alerts:shares", kwargs={"slug": slug})
    if request.method == "POST":
        share = VisitShare.objects.filter(
            center=center, student=me, pk=_uuid(request.POST.get("share"))
        ).first()
        if share is not None and services.revoke_share(share, me, request=request):
            messages.success(request, _("The link no longer works."))
        return redirect(here)
    context = {
        "center": center,
        "shares": list(
            VisitShare.objects.filter(center=center, student=me).select_related(
                "participant__appointment"
            )
        ),
        "enabled": center.visit_sharing_enabled,
        "crumbs": _crumbs(center),
    }
    return render(request, "alerts/shares.html", context)


def confirmation(request: HttpRequest, token: str) -> HttpResponse:
    """What an instructor sees. No sign-in; any dead link looks like any other."""
    share = services.open_share(token)
    if share is None:
        return render(request, "alerts/confirmation_gone.html", status=404)
    response = render(
        request, "alerts/confirmation.html", {"visit": services.describe_visit(share)}
    )
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex"
    return response
