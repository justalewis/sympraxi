"""Workshops: the list, a workshop's page, setup for directors, and the roster."""

import csv
from datetime import timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.workshops import services
from sympraxi.workshops.forms import AddStudentForm, CancelForm, WorkshopForm
from sympraxi.workshops.models import Attendance, Registration, Workshop, WorkshopStatus


def _me(request: HttpRequest) -> User:
    return cast(User, request.user)


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [
        (center.name, center.get_absolute_url()),
        (_("Workshops"), reverse("workshops:list", kwargs={"slug": center.slug})),
        *extra,
    ]


def _uuid(raw: str | None) -> UUID:
    try:
        return UUID(raw or "")
    except ValueError:
        return UUID(int=0)


@login_required
def workshop_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_WORKSHOPS, Center.objects.all(), slug=slug)
    me = _me(request)
    managing = can(me, Action.MANAGE_WORKSHOPS, center)
    now = timezone.now()
    shown = Workshop.objects.filter(center=center).select_related("leader")
    if not managing:
        shown = shown.filter(status=WorkshopStatus.PUBLISHED) | shown.filter(leader=me).exclude(
            status=WorkshopStatus.CANCELED
        )
    upcoming = shown.filter(ends_at__gte=now).exclude(status=WorkshopStatus.CANCELED)
    past = (
        shown.filter(ends_at__lt=now, starts_at__gte=now - timedelta(days=60)).order_by(
            "-starts_at"
        )
        if managing or can(me, Action.TAKE_WORKSHOP_ATTENDANCE, center)
        else shown.none()
    )
    mine = {
        r.workshop_id: r
        for r in Registration.objects.filter(
            student=me, workshop__center=center, canceled_at__isnull=True
        )
    }
    context: dict[str, Any] = {
        "center": center,
        "upcoming": list(upcoming),
        "past": list(past),
        "mine": mine,
        "managing": managing,
        "can_register": can(me, Action.REGISTER_WORKSHOP, center),
        "crumbs": [(center.name, center.get_absolute_url())],
    }
    with timezone.override(center.time_zone):
        return render(request, "workshops/list.html", context)


def _workshop(request: HttpRequest, action: Action, slug: str, workshop_id: UUID) -> Workshop:
    return get_for_action(
        request.user,
        action,
        Workshop.objects.select_related("center", "leader").filter(center__slug=slug),
        pk=workshop_id,
    )


@login_required
def workshop_detail(request: HttpRequest, slug: str, workshop_id: UUID) -> HttpResponse:
    workshop = _workshop(request, Action.VIEW_WORKSHOPS, slug, workshop_id)
    center = workshop.center
    me = _me(request)
    mine = Registration.objects.filter(
        workshop=workshop, student=me, canceled_at__isnull=True
    ).first()
    context: dict[str, Any] = {
        "center": center,
        "workshop": workshop,
        "mine": mine,
        "can_register": can(me, Action.REGISTER_WORKSHOP, workshop),
        "can_manage": can(me, Action.MANAGE_WORKSHOPS, workshop),
        "can_roster": can(me, Action.TAKE_WORKSHOP_ATTENDANCE, workshop),
        "seats_left": workshop.seats_left(),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "workshops/detail.html", context)


@login_required
def register(request: HttpRequest, slug: str, workshop_id: UUID) -> HttpResponse:
    workshop = _workshop(request, Action.REGISTER_WORKSHOP, slug, workshop_id)
    here = reverse("workshops:detail", kwargs={"slug": slug, "workshop_id": workshop.pk})
    if request.method != "POST":
        return workshop_detail(request, slug, workshop_id)
    if request.method == "POST":
        me = _me(request)
        try:
            if request.POST.get("action") == "cancel":
                registration = Registration.objects.filter(workshop=workshop, student=me).first()
                if registration is not None and services.unregister(
                    registration, by=me, request=request
                ):
                    messages.info(request, _("Your registration is canceled."))
            else:
                services.register(workshop, me, by=me, request=request)
                messages.success(request, _("You're registered. We emailed you the details."))
        except services.WorkshopError as problem:
            messages.error(request, str(problem))
    return redirect(here)


def _save(workshop: Workshop, form: WorkshopForm, request: HttpRequest) -> None:
    data = form.cleaned_data
    workshop.title = data["title"]
    workshop.description = data["description"]
    workshop.starts_at, workshop.ends_at = data["starts_at"], data["ends_at"]
    workshop.location = data["location"]
    workshop.meeting_url = data["meeting_url"]
    workshop.leader = form.leader_chosen()
    workshop.capacity = data["capacity"]
    workshop.status = data["status"]
    workshop.save()


@login_required
def workshop_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_WORKSHOPS, Center.objects.all(), slug=slug)
    form = WorkshopForm(request.POST or None, center=center)
    if request.method == "POST" and form.is_valid():
        workshop = Workshop(center=center, created_by=_me(request))
        _save(workshop, form, request)
        record(
            AuditAction.WORKSHOP_SAVED,
            request=request,
            center=center,
            target=workshop,
            created=True,
            status=workshop.status,
        )
        messages.success(request, _("Workshop saved."))
        return redirect(
            reverse("workshops:detail", kwargs={"slug": slug, "workshop_id": workshop.pk})
        )
    context = {
        "center": center,
        "form": form,
        "title": _("New workshop"),
        "submit": _("Save the workshop"),
        "crumbs": _crumbs(center),
    }
    return render(request, "workshops/form.html", context)


@login_required
def workshop_edit(request: HttpRequest, slug: str, workshop_id: UUID) -> HttpResponse:
    workshop = _workshop(request, Action.MANAGE_WORKSHOPS, slug, workshop_id)
    center = workshop.center
    canceling = request.method == "POST" and "cancel_workshop" in request.POST
    form = WorkshopForm(
        request.POST if request.method == "POST" and not canceling else None,
        center=center,
        workshop=workshop,
    )
    cancel_form = CancelForm(request.POST if canceling else None, prefix="cancel")
    back = reverse("workshops:detail", kwargs={"slug": slug, "workshop_id": workshop.pk})
    if canceling and cancel_form.is_valid():
        services.cancel_workshop(
            workshop, reason=cancel_form.cleaned_data["reason"], by=_me(request), request=request
        )
        messages.success(request, _("The workshop is canceled."))
        return redirect(back)
    if request.method == "POST" and not canceling and not workshop.is_canceled and form.is_valid():
        _save(workshop, form, request)
        record(
            AuditAction.WORKSHOP_SAVED,
            request=request,
            center=center,
            target=workshop,
            created=False,
            status=workshop.status,
        )
        messages.success(request, _("Workshop saved."))
        return redirect(back)
    context = {
        "center": center,
        "workshop": workshop,
        "form": form,
        "cancel_form": None if workshop.is_canceled else cancel_form,
        "title": _("Change %(title)s") % {"title": workshop.title},
        "submit": _("Save the workshop"),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "workshops/form.html", context)


def _find_student(center: Center, text: str) -> User | None:
    text = text.strip()
    return (
        User.objects.filter(
            Q(email__iexact=text) | Q(student_id__iexact=text),
            memberships__center=center,
            memberships__role=Role.STUDENT,
            memberships__is_active=True,
            is_active=True,
        )
        .distinct()
        .first()
    )


@login_required
def workshop_roster(request: HttpRequest, slug: str, workshop_id: UUID) -> HttpResponse:
    workshop = _workshop(request, Action.TAKE_WORKSHOP_ATTENDANCE, slug, workshop_id)
    center = workshop.center
    me = _me(request)
    here = reverse("workshops:roster", kwargs={"slug": slug, "workshop_id": workshop.pk})
    add_form = AddStudentForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "add" else None
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "mark":
                registration = Registration.objects.get(
                    pk=_uuid(request.POST.get("registration")), workshop=workshop
                )
                services.mark(
                    registration, request.POST.get("attendance", ""), by=me, request=request
                )
                return redirect(here)
            if action == "remove":
                registration = Registration.objects.get(
                    pk=_uuid(request.POST.get("registration")), workshop=workshop
                )
                services.unregister(registration, by=me, request=request)
                return redirect(here)
            if action == "add" and add_form.is_valid():
                student = _find_student(center, add_form.cleaned_data["student"])
                if student is None:
                    add_form.add_error(
                        "student", _("No student in this center has that email or ID.")
                    )
                else:
                    services.register(workshop, student, by=me, request=request)
                    messages.success(
                        request, _("%(name)s is registered.") % {"name": student.display_name}
                    )
                    return redirect(here)
        except Registration.DoesNotExist:
            messages.error(request, _("That registration isn't on this roster."))
            return redirect(here)
        except services.WorkshopError as problem:
            messages.error(request, str(problem))
            return redirect(here)
    registrations = list(services.roster(workshop))
    if request.GET.get("format") == "csv":
        record(
            AuditAction.WORKSHOP_ROSTER_DOWNLOADED,
            request=request,
            center=center,
            target=workshop,
            students=len(registrations),
        )
        return _roster_csv(workshop, registrations)
    context: dict[str, Any] = {
        "center": center,
        "workshop": workshop,
        "registrations": registrations,
        "add_form": add_form,
        "started": workshop.has_started,
        "choices": Attendance.choices,
        "came": sum(1 for r in registrations if r.attendance == Attendance.ATTENDED),
        "crumbs": _crumbs(
            center,
            (
                workshop.title,
                reverse("workshops:detail", kwargs={"slug": slug, "workshop_id": workshop.pk}),
            ),
        ),
    }
    with timezone.override(center.time_zone):
        return render(request, "workshops/roster.html", context)


def _roster_csv(workshop: Workshop, registrations: list[Registration]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="workshop-roster.csv"'
    response.write("﻿")
    tz = ZoneInfo(workshop.center.time_zone)
    out = csv.writer(response)
    out.writerow([workshop.title, workshop.starts_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")])
    out.writerow(["Name", "Email", "Student ID", "Registered", "Attendance"])
    for r in registrations:
        out.writerow(
            [
                r.student.full_name,
                r.student.email,
                r.student.student_id or "",
                r.registered_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                r.get_attendance_display(),
            ]
        )
    return response
