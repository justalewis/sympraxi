"""Pages for booking and seeing appointments.

The booking page is a list first (build plan §7): pick a date, see open times
as a list, choose one. Filters work without JavaScript; with it, htmx swaps in
only the list and announces how many times are open.
"""

from datetime import date, timedelta
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.centers.views import HtmxHttpRequest
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.scheduling import ics, notifications, services
from sympraxi.scheduling.availability import OpenTime, lengths_offered, open_times
from sympraxi.scheduling.forms import (
    BookForm,
    ConfirmForm,
    DayForm,
    OpenTimesForm,
    SlotForm,
    StaffBookForm,
    format_start,
)
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Participant,
    Schedule,
    TutorAssignment,
    Visibility,
)
from sympraxi.scheduling.timeutils import day_bounds, has_transition, local_date


def signed_in(request: HttpRequest) -> User:
    """`request.user` in a view behind `login_required`."""
    return cast(User, request.user)


def crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    """Breadcrumb trail: the center, then the schedules list, then `extra`."""
    return [
        (center.name, center.get_absolute_url()),
        (_("Schedules"), reverse("scheduling:schedules", kwargs={"slug": center.slug})),
        *extra,
    ]


def schedule_crumbs(schedule: Schedule) -> list[tuple[str, str]]:
    url = reverse(
        "scheduling:open_times", kwargs={"slug": schedule.center.slug, "schedule_id": schedule.pk}
    )
    return crumbs(schedule.center, (schedule.name, url))


def center_for(request: HttpRequest, action: Action, slug: str) -> Center:
    return get_for_action(request.user, action, Center.objects.all(), slug=slug)


def schedule_for(request: HttpRequest, action: Action, slug: str, schedule_id: UUID) -> Schedule:
    return get_for_action(
        request.user,
        action,
        Schedule.objects.select_related("center", "term").filter(center__slug=slug),
        pk=schedule_id,
    )


def appointment_for(
    request: HttpRequest, action: Action, slug: str, appointment_id: UUID
) -> Appointment:
    return get_for_action(
        request.user,
        action,
        Appointment.objects.select_related(
            "schedule__center", "schedule__term", "tutor", "room", "assignment"
        ).filter(schedule__center__slug=slug),
        pk=appointment_id,
    )


# --- Schedules and open times ---------------------------------------------------


@login_required
def schedule_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.VIEW_SCHEDULES, slug)
    user = request.user
    schedules = [
        s
        for s in Schedule.objects.filter(center=center).select_related("center", "term")
        if can(user, Action.VIEW_SCHEDULES, s)
    ]
    context = {
        "center": center,
        "schedules": schedules,
        "can_manage": can(user, Action.MANAGE_SCHEDULES, center),
        "can_manage_exemptions": can(user, Action.MANAGE_EXEMPTIONS, center),
        "can_see_day": can(user, Action.VIEW_ALL_APPOINTMENTS, center),
        "can_book_own": can(user, Action.BOOK_OWN, center),
        "can_tutor": can(user, Action.VIEW_OWN_TUTORING, center),
        "Visibility": Visibility,
        "crumbs": crumbs(center)[:1],
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/schedule_list.html", context)


def bookable_dates(schedule: Schedule, today: date, as_staff: bool) -> tuple[date, date]:
    """The first and last dates someone may pick on the booking page."""
    if as_staff:
        return schedule.starts_on, schedule.ends_on
    first = max(today, schedule.starts_on)
    last = min(schedule.ends_on, today + timedelta(days=schedule.horizon_days))
    return first, last


def _open_times_context(
    request: HttpRequest,
    schedule: Schedule,
    *,
    as_staff: bool,
    link_base: str,
    modality: str | None = None,
    minutes: int | None = None,
    groups: bool = True,
) -> dict[str, Any]:
    """The filters and open-times list shared by the booking and change pages.

    `link_base` is where each time's link goes (with the time in the query string),
    or "" for no links. `modality`, when given, overrides the filter. `groups=False`
    leaves out seats in existing group appointments.
    """
    now = timezone.now()
    tz = schedule.tz
    today = local_date(now, tz)
    first, last = bookable_dates(schedule, today, as_staff)
    lengths = lengths_offered(schedule)
    assignments = list(
        schedule.assignments.filter(is_active=True)
        .select_related("tutor")
        .order_by("tutor__full_name")
    )
    day = min(max(today, first), last)
    if minutes not in lengths:
        minutes = lengths[0] if lengths else None
    defaults = {"date": day.isoformat(), "minutes": str(minutes or "")}
    # A link may name only the date ("?date=2026-11-02"); the rest keep their defaults.
    data = {**defaults, **request.GET.dict()} if request.GET else None
    form = OpenTimesForm(
        data,
        lengths=lengths,
        assignments=assignments,
        first=first,
        last=last,
        initial={"date": day, "minutes": minutes},
    )
    tutor: UUID | None = None
    chosen_modality = modality
    if form.is_bound and form.is_valid():
        day = form.cleaned_data["date"]
        minutes = form.cleaned_data["minutes"]
        tutor = UUID(form.cleaned_data["tutor"]) if form.cleaned_data["tutor"] else None
        chosen_modality = modality or form.cleaned_data["modality"] or None
    elif form.is_bound:
        minutes = None  # show the errors instead of a list

    times: list[OpenTime] = []
    open_for_booking = first <= last and bool(lengths) and schedule.accepts_bookings
    if open_for_booking and minutes:
        earliest = None if as_staff else now + timedelta(minutes=schedule.min_notice_minutes)
        times = open_times(
            schedule,
            day,
            minutes,
            earliest=earliest,
            assignment_id=tutor,
            modality=chosen_modality,
        )
    rows = [
        {"time": t, "book_url": f"{link_base}?{_slot_query(t)}" if link_base else ""}
        for t in times
        if groups or t.appointment is None
    ]
    query = request.GET.copy()
    return {
        "form": form,
        "day": day,
        "rows": rows,
        "first": first,
        "last": last,
        "open_for_booking": open_for_booking,
        "minutes": minutes,
        "show_zone": has_transition(day, tz),
        "previous_url": _day_link(query, day - timedelta(days=1)) if day > first else "",
        "next_url": _day_link(query, day + timedelta(days=1)) if day < last else "",
    }


@login_required
def open_times_page(request: HtmxHttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    schedule = schedule_for(request, Action.VIEW_SCHEDULES, slug, schedule_id)
    user = request.user
    as_staff = can(user, Action.BOOK_FOR_OTHERS, schedule)
    kwargs = {"slug": slug, "schedule_id": schedule.pk}
    if as_staff:
        book_url = reverse("scheduling:staff_book", kwargs=kwargs)
    elif can(user, Action.BOOK_OWN, schedule):
        book_url = reverse("scheduling:book", kwargs=kwargs)
    else:
        book_url = ""
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "crumbs": crumbs(schedule.center),
        "htmx": bool(request.htmx),
        **_open_times_context(request, schedule, as_staff=as_staff, link_base=book_url),
        "as_staff": as_staff,
        "can_see_day": can(user, Action.VIEW_ALL_APPOINTMENTS, schedule),
    }
    template = "scheduling/_open_times.html" if request.htmx else "scheduling/open_times.html"
    with timezone.override(schedule.center.time_zone):
        return render(request, template, context)


def _slot_query(t: OpenTime) -> str:
    return urlencode(
        {
            "assignment": str(t.assignment.pk),
            "start": format_start(t.starts_at),
            "minutes": t.minutes,
        }
    )


def _day_link(query: Any, day: date) -> str:
    query = query.copy()
    query["date"] = day.isoformat()
    return "?" + query.urlencode()


def _chosen_time(
    schedule: Schedule, data: Any, *, as_staff: bool
) -> tuple[SlotForm, OpenTime | None]:
    """The open time named in `data`, if it is still open."""
    slot = SlotForm(data)
    if not slot.is_valid():
        return slot, None
    assignment = (
        TutorAssignment.objects.filter(schedule=schedule, pk=slot.cleaned_data["assignment"])
        .select_related("tutor", "schedule__center", "room")
        .first()
    )
    if assignment is None:
        return slot, None
    start = slot.cleaned_data["start"]
    minutes = slot.cleaned_data["minutes"]
    now = timezone.now()
    earliest = None if as_staff else now + timedelta(minutes=schedule.min_notice_minutes)
    for t in open_times(
        schedule,
        local_date(start, schedule.tz),
        minutes,
        earliest=earliest,
        assignment_id=assignment.pk,
    ):
        if t.starts_at == start:
            return slot, t
    return slot, None


def _gone(request: HttpRequest, schedule: Schedule) -> HttpResponseRedirect:
    messages.warning(request, _("That time isn't open anymore. Choose another."))
    return redirect("scheduling:open_times", slug=schedule.center.slug, schedule_id=schedule.pk)


@login_required
def book(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    """A student books an open time for themselves."""
    schedule = schedule_for(request, Action.BOOK_OWN, slug, schedule_id)
    data = request.POST if request.method == "POST" else request.GET
    slot, chosen = _chosen_time(schedule, data, as_staff=False)
    if chosen is None:
        return _gone(request, schedule)
    form = BookForm(
        request.POST if request.method == "POST" else None,
        modalities=chosen.modalities,
        initial={**slot.cleaned_data, "start": format_start(chosen.starts_at)},
    )
    if request.method == "POST" and form.is_valid():
        try:
            appointment = services.book(
                actor=signed_in(request),
                assignment=chosen.assignment,
                starts_at=chosen.starts_at,
                minutes=chosen.minutes,
                modality=form.cleaned_data["modality"],
                students=[signed_in(request)],
                as_staff=False,
                request=request,
            )
        except services.BookingError as exc:
            form.add_error(None, exc.message)
        else:
            if appointment.is_tentative:
                messages.success(
                    request, _("Your request is in. The center will confirm it by email.")
                )
            else:
                messages.success(request, _("You're booked. A confirmation email is on its way."))
            return redirect("scheduling:appointment", slug=slug, appointment_id=appointment.pk)
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "crumbs": schedule_crumbs(schedule),
        "time": chosen,
        "form": form,
    }
    with timezone.override(schedule.center.time_zone):
        return render(request, "scheduling/book.html", context)


@login_required
def staff_book(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    """Front desk and directors book for a student, or hold time with a placeholder."""
    schedule = schedule_for(request, Action.BOOK_FOR_OTHERS, slug, schedule_id)
    data = request.POST if request.method == "POST" else request.GET
    slot, chosen = _chosen_time(schedule, data, as_staff=True)
    if chosen is None:
        return _gone(request, schedule)
    form = StaffBookForm(
        request.POST if request.method == "POST" else None,
        center=schedule.center,
        modalities=chosen.modalities,
        initial={**slot.cleaned_data, "start": format_start(chosen.starts_at), "weeks": 1},
    )
    if chosen.appointment is not None:
        # Joining a group: placeholders and repeats don't apply.
        for name in ("is_placeholder", "weeks"):
            form.fields[name].disabled = True
    if request.method == "POST" and form.is_valid():
        student = form.cleaned_data["student"]
        try:
            booked = services.book_series(
                weeks=form.cleaned_data["weeks"] or 1,
                actor=signed_in(request),
                assignment=chosen.assignment,
                starts_at=chosen.starts_at,
                minutes=chosen.minutes,
                modality=form.cleaned_data["modality"],
                students=[student] if student else [],
                as_staff=True,
                is_walk_in=form.cleaned_data["is_walk_in"],
                is_placeholder=form.cleaned_data["is_placeholder"],
                is_tentative=form.cleaned_data["is_tentative"],
                override_limits=form.cleaned_data["override_limits"],
                request=request,
            )
        except services.BookingError as exc:
            form.add_error(None, exc.message)
        else:
            if len(booked) > 1:
                messages.success(
                    request, _("Booked %(n)s weekly appointments.") % {"n": len(booked)}
                )
            else:
                messages.success(request, _("Booked."))
            return redirect("scheduling:appointment", slug=slug, appointment_id=booked[0].pk)
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "crumbs": schedule_crumbs(schedule),
        "time": chosen,
        "form": form,
    }
    with timezone.override(schedule.center.time_zone):
        return render(request, "scheduling/staff_book.html", context)


# --- Appointments -----------------------------------------------------------------


def appointment_crumbs(user: Any, appointment: Appointment) -> list[tuple[str, str]]:
    center = appointment.schedule.center
    home = (center.name, center.get_absolute_url())
    if appointment.tutor_id == user.pk:
        url = reverse("scheduling:my_tutoring", kwargs={"slug": center.slug})
        return [home, (_("Your tutoring"), url)]
    if can(user, Action.VIEW_ALL_APPOINTMENTS, center):
        url = reverse(
            "scheduling:day_sheet",
            kwargs={"slug": center.slug, "schedule_id": appointment.schedule_id},
        )
        day = local_date(appointment.starts_at, appointment.schedule.tz)
        return [*schedule_crumbs(appointment.schedule)[:2], (_("Day sheet"), f"{url}?date={day}")]
    url = reverse("scheduling:my_appointments", kwargs={"slug": center.slug})
    return [home, (_("Your appointments"), url)]


def _detail_crumbs(user: Any, appointment: Appointment) -> list[tuple[str, str]]:
    url = reverse(
        "scheduling:appointment",
        kwargs={"slug": appointment.schedule.center.slug, "appointment_id": appointment.pk},
    )
    return [*appointment_crumbs(user, appointment), (_("Appointment"), url)]


def _people(user: Any, appointment: Appointment, center: Center) -> list[Participant] | None:
    """Who is in the appointment, for staff and the tutor. Students never see other students."""
    if can(user, Action.VIEW_ALL_APPOINTMENTS, center) or appointment.tutor_id == user.pk:
        return list(appointment.participants.select_related("student"))
    return None


@login_required
def appointment_detail(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = appointment_for(request, Action.VIEW_APPOINTMENT, slug, appointment_id)
    center = appointment.schedule.center
    user = request.user
    staff = can(user, Action.BOOK_FOR_OTHERS, center)
    may_cancel = can(user, Action.CANCEL_APPOINTMENT, appointment) and appointment.is_active
    mine = None
    if not staff:
        mine = appointment.participants.filter(student=signed_in(request)).first()
    in_it = mine is not None and mine.canceled_at is None
    taken = appointment.active_participants().count()
    within_cutoff = not staff and not services.can_change(appointment)
    context = {
        "center": center,
        "appointment": appointment,
        "crumbs": appointment_crumbs(user, appointment),
        "people": _people(user, appointment, center),
        "mine": mine,
        "taken": taken,
        "may_cancel": may_cancel and (staff or in_it),
        "may_change": appointment.is_active
        and can(user, Action.CHANGE_APPOINTMENT, appointment)
        and (staff or (in_it and taken == 1 and not within_cutoff)),
        "within_cutoff": within_cutoff,
        "may_confirm": staff and appointment.is_active and appointment.is_tentative,
        "is_tutor": appointment.tutor_id == user.pk,
        "location": ics.location(appointment),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/appointment_detail.html", context)


@login_required
def appointment_cancel(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = appointment_for(request, Action.CANCEL_APPOINTMENT, slug, appointment_id)
    center = appointment.schedule.center
    staff = can(request.user, Action.BOOK_FOR_OTHERS, center)
    form = ConfirmForm(request.POST or None)
    error = ""
    if request.method == "POST":
        try:
            services.cancel(
                actor=signed_in(request),
                appointment=appointment,
                as_staff=staff,
                request=request,
            )
        except services.BookingError as exc:
            error = exc.message
        else:
            messages.success(request, _("Canceled. A confirmation email is on its way."))
            return redirect("scheduling:appointment", slug=slug, appointment_id=appointment.pk)
    context = {
        "center": center,
        "appointment": appointment,
        "crumbs": _detail_crumbs(request.user, appointment),
        "form": form,
        "error": error,
        "staff": staff,
        "allowed": appointment.is_active and (staff or services.can_change(appointment)),
        "location": ics.location(appointment),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/appointment_cancel.html", context)


@login_required
def appointment_change(request: HtmxHttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """Move an appointment to another open time on the same schedule.

    Students can move their own one-to-one appointments until the change cutoff.
    Staff can move any appointment. The list works like the booking page; picking
    a time shows a confirmation, and posting it moves the appointment.
    """
    appointment = appointment_for(request, Action.CHANGE_APPOINTMENT, slug, appointment_id)
    schedule = appointment.schedule
    center = schedule.center
    user = signed_in(request)
    staff = can(user, Action.BOOK_FOR_OTHERS, center)
    alone = appointment.active_participants().count() == 1
    allowed = appointment.is_active and (staff or (alone and services.can_change(appointment)))
    url = reverse(
        "scheduling:appointment_change", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    context: dict[str, Any] = {
        "center": center,
        "schedule": schedule,
        "appointment": appointment,
        "crumbs": _detail_crumbs(user, appointment),
        "location": ics.location(appointment),
        "staff": staff,
        "allowed": allowed,
        "group": not alone and not appointment.is_placeholder,
        "page_url": url,
        "link_label": _("Move here"),
    }
    template = "scheduling/appointment_change.html"
    data = request.POST if request.method == "POST" else request.GET
    if allowed and "start" in data:
        slot, chosen = _chosen_time(schedule, data, as_staff=staff)
        if (
            chosen is None
            or chosen.appointment is not None
            or appointment.modality not in chosen.modalities
        ):
            messages.warning(request, _("That time isn't open anymore. Choose another."))
            return redirect(url)
        if request.method == "POST":
            try:
                services.reschedule(
                    actor=user,
                    appointment=appointment,
                    assignment=chosen.assignment,
                    starts_at=chosen.starts_at,
                    minutes=chosen.minutes,
                    as_staff=staff,
                    request=request,
                )
            except services.BookingError as exc:
                context["error"] = exc.message
            else:
                messages.success(request, _("Moved. An email with the new time is on its way."))
                return redirect("scheduling:appointment", slug=slug, appointment_id=appointment.pk)
        context.update({"mode": "confirm", "time": chosen, "slot": slot})
    elif allowed:
        context.update(
            _open_times_context(
                request,
                schedule,
                as_staff=staff,
                link_base=url,
                modality=appointment.modality,
                minutes=appointment.minutes,
                groups=False,
            )
        )
        context.update({"mode": "choose", "htmx": bool(request.htmx)})
        if request.htmx:
            template = "scheduling/_open_times.html"
    with timezone.override(center.time_zone):
        return render(request, template, context)


@login_required
def appointment_confirm(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """Staff confirm a tentative booking."""
    appointment = appointment_for(request, Action.BOOK_FOR_OTHERS, slug, appointment_id)
    center = appointment.schedule.center
    form = ConfirmForm(request.POST or None)
    error = ""
    if request.method == "POST":
        try:
            services.confirm(
                actor=signed_in(request),
                appointment=appointment,
                request=request,
            )
        except services.BookingError as exc:
            error = exc.message
        else:
            messages.success(request, _("Confirmed. The student will get an email."))
            return redirect("scheduling:appointment", slug=slug, appointment_id=appointment.pk)
    context = {
        "center": center,
        "appointment": appointment,
        "crumbs": _detail_crumbs(request.user, appointment),
        "form": form,
        "error": error,
        "location": ics.location(appointment),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/appointment_confirm.html", context)


@login_required
def appointment_ics(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = appointment_for(request, Action.VIEW_APPOINTMENT, slug, appointment_id)
    body = ics.calendar(
        [appointment],
        method="PUBLISH",
        for_tutor=appointment.tutor_id == request.user.pk,
        base_url=notifications.base_url(request),
    )
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="appointment.ics"'
    return response


def _upcoming_first(appointments: QuerySet[Appointment]) -> tuple[list, list]:
    now = timezone.now()
    upcoming = [a for a in appointments if a.is_active and a.ends_at >= now]
    past = [a for a in appointments if not (a.is_active and a.ends_at >= now)]
    past.sort(key=lambda a: a.starts_at, reverse=True)
    return upcoming, past[:20]


@login_required
def my_appointments(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.BOOK_OWN, slug)
    rows = (
        Appointment.objects.filter(
            schedule__center=center, participants__student=signed_in(request)
        )
        .select_related("schedule", "tutor", "room")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.filter(student=signed_in(request)),
                to_attr="mine",
            )
        )
        .order_by("starts_at")
    )
    upcoming, past = _upcoming_first(rows)
    schedules = [
        s
        for s in Schedule.objects.filter(center=center, visibility=Visibility.EVERYONE)
        if can(request.user, Action.BOOK_OWN, s)
    ]
    context = {
        "center": center,
        "crumbs": crumbs(center)[:1],
        "upcoming": upcoming,
        "past": past,
        "schedules": schedules,
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/my_appointments.html", context)


@login_required
def my_tutoring(request: HttpRequest, slug: str) -> HttpResponse:
    """A tutor's own appointments in this center, with who is coming."""
    center = center_for(request, Action.VIEW_OWN_TUTORING, slug)
    now = timezone.now()
    rows = list(
        Appointment.objects.filter(
            schedule__center=center, tutor=signed_in(request), ends_at__gte=now
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .select_related("schedule", "room")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.filter(canceled_at__isnull=True).select_related(
                    "student"
                ),
                to_attr="active",
            )
        )
        .order_by("starts_at")[:200]
    )
    days: dict[date, list[Appointment]] = {}
    tz = center.time_zone
    with timezone.override(tz):
        for appointment in rows:
            days.setdefault(timezone.localdate(appointment.starts_at), []).append(appointment)
        context = {"center": center, "crumbs": crumbs(center)[:1], "days": sorted(days.items())}
        return render(request, "scheduling/my_tutoring.html", context)


@login_required
def day_sheet(request: HttpRequest, slug: str, schedule_id: UUID) -> HttpResponse:
    """Every appointment on one schedule for one day, for the front desk."""
    schedule = schedule_for(request, Action.VIEW_ALL_APPOINTMENTS, slug, schedule_id)
    tz = schedule.tz
    today = local_date(timezone.now(), tz)
    form = DayForm(request.GET or None, initial={"date": today})
    day = form.cleaned_data["date"] if form.is_bound and form.is_valid() else today
    start, end = day_bounds(day, tz)
    rows = (
        Appointment.objects.filter(schedule=schedule, starts_at__gte=start, starts_at__lt=end)
        .select_related("tutor", "room")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.select_related("student"),
                to_attr="people",
            )
        )
        .order_by("starts_at", "tutor__full_name")
    )
    query = request.GET.copy()
    context = {
        "center": schedule.center,
        "schedule": schedule,
        "crumbs": crumbs(schedule.center),
        "form": form,
        "day": day,
        "appointments": list(rows),
        "show_zone": has_transition(day, tz),
        "previous_url": _day_link(query, day - timedelta(days=1)),
        "next_url": _day_link(query, day + timedelta(days=1)),
        "can_book": can(request.user, Action.BOOK_FOR_OTHERS, schedule),
        "open_times_query": urlencode({"date": day.isoformat()}),
    }
    with timezone.override(schedule.center.time_zone):
        return render(request, "scheduling/day_sheet.html", context)
