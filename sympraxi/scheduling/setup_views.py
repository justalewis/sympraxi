"""Director pages for setting up scheduling: terms, closures, rooms, schedules,
tutors and their hours, and booking-limit exemptions.

Most are a form on one generic page (`scheduling/form_page.html`).
"""

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.forms import (
    AssignmentForm,
    ClosureForm,
    ConfirmForm,
    ExceptionForm,
    ExemptionForm,
    RoomForm,
    ScheduleForm,
    TermForm,
    WeeklyHoursFormSet,
)
from sympraxi.scheduling.models import (
    AvailabilityException,
    Closure,
    Exemption,
    Room,
    Schedule,
    Term,
    TutorAssignment,
)
from sympraxi.scheduling.views import center_for, crumbs, schedule_for, signed_in

MANAGE = Action.MANAGE_SCHEDULES


def _form_page(
    request: HttpRequest,
    center: Center,
    form: forms.BaseForm,
    *,
    title: str,
    crumbs: list[tuple[str, str]],
    submit: str,
    back: str,
    intro: str = "",
) -> HttpResponse:
    context = {
        "center": center,
        "form": form,
        "title": title,
        "crumbs": crumbs,
        "submit": submit,
        "back": back,
        "intro": intro,
    }
    return render(request, "scheduling/form_page.html", context)


def _setup_crumbs(center: Center) -> list[tuple[str, str]]:
    return crumbs(center)


def _manage_url(schedule: Schedule) -> str:
    return reverse(
        "scheduling:manage", kwargs={"slug": schedule.center.slug, "schedule_id": schedule.pk}
    )


def _assignment_for(request: HttpRequest, slug: str, assignment_id: UUID) -> TutorAssignment:
    return get_for_action(
        request.user,
        MANAGE,
        TutorAssignment.objects.select_related("schedule__center", "tutor").filter(
            schedule__center__slug=slug
        ),
        pk=assignment_id,
    )


# --- Terms and closures -------------------------------------------------------------


@login_required
def term_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, MANAGE, slug)
    terms = Term.objects.filter(center=center).prefetch_related("closures")
    context = {"center": center, "terms": terms, "crumbs": crumbs(center)}
    return render(request, "scheduling/term_list.html", context)


@login_required
def term_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, MANAGE, slug)
    form = TermForm(request.POST or None, instance=Term(center=center))
    back = reverse("scheduling:terms", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Term added."))
        return redirect(back)
    return _form_page(
        request,
        center,
        form,
        title=_("Add a term"),
        crumbs=[*_setup_crumbs(center), (_("Terms"), back)],
        submit=_("Add term"),
        back=back,
    )


@login_required
def term_edit(request: HttpRequest, slug: str, term_id: UUID) -> HttpResponse:
    term = get_for_action(request.user, MANAGE, Term.objects.filter(center__slug=slug), pk=term_id)
    form = TermForm(request.POST or None, instance=term)
    back = reverse("scheduling:terms", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Term saved."))
        return redirect(back)
    return _form_page(
        request,
        term.center,
        form,
        title=_("Edit %(term)s") % {"term": term.name},
        crumbs=[*_setup_crumbs(term.center), (_("Terms"), back)],
        submit=_("Save"),
        back=back,
    )


@login_required
def closure_create(request: HttpRequest, slug: str, term_id: UUID) -> HttpResponse:
    term = get_for_action(request.user, MANAGE, Term.objects.filter(center__slug=slug), pk=term_id)
    form = ClosureForm(request.POST or None, instance=Closure(term=term))
    back = reverse("scheduling:terms", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Closure added. No one can book on those days."))
        return redirect(back)
    return _form_page(
        request,
        term.center,
        form,
        title=_("Add a closure to %(term)s") % {"term": term.name},
        crumbs=[*_setup_crumbs(term.center), (_("Terms"), back)],
        submit=_("Add closure"),
        back=back,
        intro=_("Every schedule in the term is closed on these days. Existing appointments stay."),
    )


@login_required
def closure_delete(request: HttpRequest, slug: str, closure_id: UUID) -> HttpResponse:
    closure = get_for_action(
        request.user,
        MANAGE,
        Closure.objects.select_related("term__center").filter(term__center__slug=slug),
        pk=closure_id,
    )
    form = ConfirmForm(request.POST or None)
    back = reverse("scheduling:terms", kwargs={"slug": slug})
    if request.method == "POST":
        closure.delete()
        messages.success(request, _("Closure removed."))
        return redirect(back)
    return _form_page(
        request,
        closure.center,
        form,
        title=_("Remove the closure “%(label)s”?") % {"label": closure.label},
        crumbs=[*_setup_crumbs(closure.center), (_("Terms"), back)],
        submit=_("Remove closure"),
        back=back,
    )


# --- Rooms --------------------------------------------------------------------------


@login_required
def room_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, MANAGE, slug)
    context = {
        "center": center,
        "rooms": Room.objects.filter(center=center),
        "crumbs": crumbs(center),
    }
    return render(request, "scheduling/room_list.html", context)


@login_required
def room_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, MANAGE, slug)
    form = RoomForm(request.POST or None, instance=Room(center=center))
    back = reverse("scheduling:rooms", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Room added."))
        return redirect(back)
    return _form_page(
        request,
        center,
        form,
        title=_("Add a room"),
        crumbs=[*_setup_crumbs(center), (_("Rooms"), back)],
        submit=_("Add room"),
        back=back,
    )


@login_required
def room_edit(request: HttpRequest, slug: str, room_id: UUID) -> HttpResponse:
    room = get_for_action(request.user, MANAGE, Room.objects.filter(center__slug=slug), pk=room_id)
    form = RoomForm(request.POST or None, instance=room)
    back = reverse("scheduling:rooms", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Room saved."))
        return redirect(back)
    return _form_page(
        request,
        room.center,
        form,
        title=_("Edit %(room)s") % {"room": room.name},
        crumbs=[*_setup_crumbs(room.center), (_("Rooms"), back)],
        submit=_("Save"),
        back=back,
    )


# --- Schedules ------------------------------------------------------------------------


@login_required
def schedule_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, MANAGE, slug)
    back = reverse("scheduling:schedules", kwargs={"slug": slug})
    if not Term.objects.filter(center=center).exists():
        messages.info(request, _("Add a term first. Every schedule belongs to one."))
        return redirect("scheduling:term_create", slug=slug)
    form = ScheduleForm(request.POST or None, instance=Schedule(center=center))
    if request.method == "POST" and form.is_valid():
        schedule = form.save()
        messages.success(request, _("Schedule added. Now add tutors and their hours."))
        return redirect(_manage_url(schedule))
    return _form_page(
        request,
        center,
        form,
        title=_("Add a schedule"),
        crumbs=_setup_crumbs(center),
        submit=_("Add schedule"),
        back=back,
    )


@login_required
def schedule_edit(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    schedule = schedule_for(request, MANAGE, slug, schedule_id)
    form = ScheduleForm(request.POST or None, instance=schedule)
    back = _manage_url(schedule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Schedule saved. Existing appointments keep their times."))
        return redirect(back)
    return _form_page(
        request,
        schedule.center,
        form,
        title=_("Edit %(schedule)s") % {"schedule": schedule.name},
        crumbs=[*_setup_crumbs(schedule.center), (schedule.name, back)],
        submit=_("Save"),
        back=back,
    )


@login_required
def schedule_manage(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    schedule = schedule_for(request, MANAGE, slug, schedule_id)
    assignments = (
        schedule.assignments.select_related("tutor", "room")
        .prefetch_related(
            "weekly_hours",
            Prefetch(
                "exceptions",
                queryset=AvailabilityException.objects.filter(ends_at__gte=timezone.now()),
            ),
        )
        .order_by("tutor__full_name")
    )
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "crumbs": crumbs(schedule.center),
        "assignments": assignments,
        "closures": schedule.term.closures.all(),
    }
    with timezone.override(schedule.center.time_zone):
        return render(request, "scheduling/schedule_manage.html", context)


# --- Tutors, hours and exceptions -----------------------------------------------------


@login_required
def assignment_create(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    schedule = schedule_for(request, MANAGE, slug, schedule_id)
    form = AssignmentForm(request.POST or None, instance=TutorAssignment(schedule=schedule))
    back = _manage_url(schedule)
    if request.method == "POST" and form.is_valid():
        assignment = form.save()
        messages.success(request, _("Tutor added. Now set their weekly hours."))
        return redirect("scheduling:hours", slug=slug, assignment_id=assignment.pk)
    return _form_page(
        request,
        schedule.center,
        form,
        title=_("Add a tutor to %(schedule)s") % {"schedule": schedule.name},
        crumbs=[*_setup_crumbs(schedule.center), (schedule.name, back)],
        submit=_("Add tutor"),
        back=back,
        intro=_("Only people with the tutor role in this center are listed."),
    )


@login_required
def assignment_edit(request: HttpRequest, slug: str, assignment_id: UUID) -> HttpResponse:
    assignment = _assignment_for(request, slug, assignment_id)
    schedule = assignment.schedule
    form = AssignmentForm(request.POST or None, instance=assignment)
    back = _manage_url(schedule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved. Existing appointments aren't changed."))
        return redirect(back)
    return _form_page(
        request,
        schedule.center,
        form,
        title=_("%(tutor)s on %(schedule)s")
        % {"tutor": assignment.tutor.display_name, "schedule": schedule.name},
        crumbs=[*_setup_crumbs(schedule.center), (schedule.name, back)],
        submit=_("Save"),
        back=back,
    )


@login_required
def hours_edit(request: HttpRequest, slug: str, assignment_id: UUID) -> HttpResponse:
    assignment = _assignment_for(request, slug, assignment_id)
    schedule = assignment.schedule
    formset = WeeklyHoursFormSet(request.POST or None, instance=assignment)
    back = _manage_url(schedule)
    if request.method == "POST" and formset.is_valid():
        with transaction.atomic():
            formset.save()
        messages.success(request, _("Weekly hours saved. Existing appointments aren't changed."))
        return redirect(back)
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "assignment": assignment,
        "formset": formset,
        "back": back,
        "crumbs": [*_setup_crumbs(schedule.center), (schedule.name, back)],
    }
    return render(request, "scheduling/hours_edit.html", context)


@login_required
def exception_create(request: HttpRequest, slug: str, assignment_id: UUID) -> HttpResponse:
    assignment = _assignment_for(request, slug, assignment_id)
    schedule = assignment.schedule
    back = _manage_url(schedule)
    with timezone.override(schedule.center.time_zone):
        form = ExceptionForm(
            request.POST or None,
            instance=AvailabilityException(assignment=assignment, created_by=signed_in(request)),
        )
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, _("Saved."))
            return redirect(back)
        return _form_page(
            request,
            schedule.center,
            form,
            title=_("Time off or extra hours for %(tutor)s")
            % {"tutor": assignment.tutor.display_name},
            crumbs=[*_setup_crumbs(schedule.center), (schedule.name, back)],
            submit=_("Save"),
            back=back,
            intro=_("Times are in the center's time zone (%(zone)s).")
            % {"zone": schedule.center.time_zone},
        )


@login_required
def exception_delete(request: HttpRequest, slug: str, exception_id: UUID) -> HttpResponse:
    exception = get_for_action(
        request.user,
        MANAGE,
        AvailabilityException.objects.select_related(
            "assignment__schedule__center", "assignment__tutor"
        ).filter(assignment__schedule__center__slug=slug),
        pk=exception_id,
    )
    schedule = exception.assignment.schedule
    back = _manage_url(schedule)
    form = ConfirmForm(request.POST or None)
    if request.method == "POST":
        exception.delete()
        messages.success(request, _("Removed."))
        return redirect(back)
    with timezone.override(schedule.center.time_zone):
        when = timezone.localtime(exception.starts_at)
        return _form_page(
            request,
            schedule.center,
            form,
            title=_("Remove this change to %(tutor)s's hours?")
            % {"tutor": exception.assignment.tutor.display_name},
            crumbs=[*_setup_crumbs(schedule.center), (schedule.name, back)],
            submit=_("Remove"),
            back=back,
            intro=f"{exception.get_kind_display()}: {when:%Y-%m-%d %H:%M}",
        )


# --- Exemptions -----------------------------------------------------------------------


@login_required
def exemption_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_EXEMPTIONS, slug)
    today = timezone.localdate(timezone=ZoneInfo(center.time_zone))
    exemptions = Exemption.objects.filter(center=center).select_related("student", "schedule")
    context: dict[str, Any] = {
        "center": center,
        "crumbs": crumbs(center),
        "active": [e for e in exemptions if e.revoked_at is None and e.expires_on >= today],
        "ended": [e for e in exemptions if e.revoked_at is not None or e.expires_on < today][:50],
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/exemption_list.html", context)


@login_required
def exemption_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_EXEMPTIONS, slug)
    form = ExemptionForm(
        request.POST or None, instance=Exemption(center=center, created_by=signed_in(request))
    )
    back = reverse("scheduling:exemptions", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        exemption = form.save()
        record(
            AuditAction.EXEMPTION_ADDED,
            request=request,
            center=center,
            target=exemption,
            student=str(exemption.student_id),
            expires_on=exemption.expires_on.isoformat(),
        )
        messages.success(request, _("Exemption added."))
        return redirect(back)
    return _form_page(
        request,
        center,
        form,
        title=_("Exempt a student from booking limits"),
        crumbs=[*_setup_crumbs(center), (_("Exemptions"), back)],
        submit=_("Add exemption"),
        back=back,
        intro=_(
            "The student can book past the schedule's limits for appointments through the "
            "date you choose. Notice, horizon and cutoff still apply."
        ),
    )


@login_required
def exemption_revoke(request: HttpRequest, slug: str, exemption_id: UUID) -> HttpResponse:
    exemption = get_for_action(
        request.user,
        Action.MANAGE_EXEMPTIONS,
        Exemption.objects.select_related("center", "student").filter(center__slug=slug),
        pk=exemption_id,
    )
    back = reverse("scheduling:exemptions", kwargs={"slug": slug})
    form = ConfirmForm(request.POST or None)
    if request.method == "POST":
        if exemption.revoked_at is None:
            exemption.revoked_at = timezone.now()
            exemption.revoked_by = signed_in(request)
            exemption.save(update_fields=["revoked_at", "revoked_by"])
            record(
                AuditAction.EXEMPTION_REVOKED,
                request=request,
                center=exemption.center,
                target=exemption,
                student=str(exemption.student_id),
            )
        messages.success(request, _("Exemption ended."))
        return redirect(back)
    return _form_page(
        request,
        exemption.center,
        form,
        title=_("End %(name)s's exemption?") % {"name": exemption.student.display_name},
        crumbs=[*_setup_crumbs(exemption.center), (_("Exemptions"), back)],
        submit=_("End exemption"),
        back=back,
        intro=_("Their limits apply again to new bookings. Existing appointments stay."),
    )
