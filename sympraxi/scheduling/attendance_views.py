"""Pages for attendance, possible no-shows, waivers and booking blocks.

Marking attendance and waiving are forms that post to the appointment's own
pages, so they work by keyboard and without JavaScript. The list of possible
no-shows posts one student's mark at a time to the same page and comes back.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records.models import RecordStatus, SessionRecord
from sympraxi.scheduling import attendance, blocks
from sympraxi.scheduling.forms import AttendanceForm, CenterBlockForm, LiftBlockForm, WaiveForm
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Block,
    Participant,
)
from sympraxi.scheduling.views import (
    appointment_crumbs,
    appointment_for,
    center_for,
    crumbs,
    signed_in,
)

# How far back the list of recent no-shows and late cancellations goes.
RECENT_DAYS = 30


def _posted(request: HttpRequest) -> Any:
    """The posted data, or None for a GET. An empty POST still binds the form."""
    return request.POST if request.method == "POST" else None


def _next_url(request: HttpRequest, center: Center) -> str:
    """A `next` address on this site, inside this center, or ""."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return ""
    return target if target.startswith(f"/centers/{center.slug}/") else ""


def _appointment_url(appointment: Appointment) -> str:
    return reverse(
        "scheduling:appointment",
        kwargs={"slug": appointment.schedule.center.slug, "appointment_id": appointment.pk},
    )


def _detail_crumbs(request: HttpRequest, appointment: Appointment) -> list[tuple[str, str]]:
    url = _appointment_url(appointment)
    return [*appointment_crumbs(request.user, appointment), (_("Appointment"), url)]


@login_required
def appointment_attendance(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """Mark each student in an appointment as here, a no-show, or not marked."""
    appointment = appointment_for(request, Action.MARK_ATTENDANCE, slug, appointment_id)
    center = appointment.schedule.center
    participants: list[Any] = list(
        appointment.participants.filter(canceled_at__isnull=True)
        .select_related("student")
        .order_by("student__full_name", "student__email")
    )
    final = set(
        SessionRecord.objects.filter(
            appointment=appointment, status=RecordStatus.FINAL
        ).values_list("student_id", flat=True)
    )
    for p in participants:
        p.has_final_record = p.student_id in final
    form = AttendanceForm(_posted(request), participants=participants)
    back = _next_url(request, center) or _appointment_url(appointment)
    error = ""
    if request.method == "POST" and form.is_valid():
        try:
            changed = attendance.mark(
                actor=signed_in(request),
                appointment=appointment,
                marks=form.marks(),
                request=request,
            )
        except attendance.AttendanceError as exc:
            error = exc.message
        else:
            if changed:
                messages.success(request, _("Attendance saved."))
            else:
                messages.info(request, _("Nothing changed."))
            return redirect(back)
    context = {
        "center": center,
        "appointment": appointment,
        "crumbs": _detail_crumbs(request, appointment),
        "form": form,
        "participants": participants,
        "started": appointment.starts_at <= timezone.now(),
        "error": error,
        "back": back,
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/attendance.html", context)


def _student_param(request: HttpRequest) -> UUID | None:
    try:
        return UUID(request.POST.get("student") or request.GET.get("student") or "")
    except ValueError:
        return None


@login_required
def waive(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """Waive one student's late cancellation or no-show, with a reason."""
    appointment = appointment_for(request, Action.WAIVE_ATTENDANCE, slug, appointment_id)
    center = appointment.schedule.center
    student_id = _student_param(request)
    participant = (
        appointment.participants.select_related("student", "waived_by")
        .filter(student=student_id)
        .first()
        if student_id
        else None
    )
    form = WaiveForm(_posted(request))
    back = _next_url(request, center) or _appointment_url(appointment)
    if request.method == "POST" and participant is not None and form.is_valid():
        try:
            attendance.waive(
                actor=signed_in(request),
                appointment=appointment,
                student_id=participant.student_id,
                reason=form.cleaned_data["reason"],
                request=request,
            )
        except attendance.AttendanceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, _("Waived. It no longer counts toward a booking block."))
            return redirect(back)
    context = {
        "center": center,
        "appointment": appointment,
        "crumbs": _detail_crumbs(request, appointment),
        "participant": participant,
        "form": form,
        "back": back,
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/waive.html", context)


@login_required
def attendance_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Possible no-shows waiting for someone to confirm or clear them.

    Tutors see their own appointments. Staff who can waive also see the last
    month's no-shows and late cancellations.
    """
    center = center_for(request, Action.MARK_ATTENDANCE, slug)
    user = request.user
    flagged = (
        Participant.objects.filter(
            appointment__schedule__center=center,
            flagged_at__isnull=False,
            flag_cleared_at__isnull=True,
            attendance=Attendance.UNMARKED,
            canceled_at__isnull=True,
        )
        .exclude(appointment__status=AppointmentStatus.CANCELED)
        .select_related("student", "appointment__tutor", "appointment__schedule__center")
        .order_by("appointment__starts_at", "student__full_name")
    )
    if not can(user, Action.VIEW_ALL_APPOINTMENTS, center):
        flagged = flagged.filter(appointment__tutor=signed_in(request))
    rows = [p for p in flagged[:300] if can(user, Action.MARK_ATTENDANCE, p.appointment)]
    recent: list[Participant] = []
    may_waive = can(user, Action.WAIVE_ATTENDANCE, center)
    if may_waive:
        since = timezone.now() - timedelta(days=RECENT_DAYS)
        recent = list(
            Participant.objects.filter(
                appointment__schedule__center=center, appointment__starts_at__gte=since
            )
            .filter(
                Q(late_cancel=True) | Q(attendance=Attendance.NO_SHOW, canceled_at__isnull=True)
            )
            .select_related(
                "student", "appointment__tutor", "appointment__schedule__center", "waived_by"
            )
            .order_by("-appointment__starts_at")[:200]
        )
    context: dict[str, Any] = {
        "center": center,
        "crumbs": crumbs(center)[:1],
        "rows": rows,
        "recent": recent,
        "recent_days": RECENT_DAYS,
        "may_waive": may_waive,
        "here": request.get_full_path(),
        "can_manage_blocks": can(user, Action.MANAGE_BLOCKS, center),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/attendance_list.html", context)


def _block_crumbs(center: Center) -> list[tuple[str, str]]:
    return [
        *crumbs(center)[:1],
        (_("Booking blocks"), reverse("scheduling:blocks", kwargs={"slug": center.slug})),
    ]


@login_required
def block_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_BLOCKS, slug)
    today = timezone.localdate(timezone=ZoneInfo(center.time_zone))
    found = Block.objects.filter(center=center).select_related("student", "schedule", "lifted_by")
    context = {
        "center": center,
        "crumbs": crumbs(center)[:1],
        "active": list(
            found.filter(lifted_at__isnull=True, ends_on__gte=today).order_by(
                "ends_on", "student__full_name"
            )
        ),
        "ended": list(
            found.filter(Q(lifted_at__isnull=False) | Q(ends_on__lt=today)).order_by("-created_at")[
                :50
            ]
        ),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/block_list.html", context)


@login_required
def block_lift(request: HttpRequest, slug: str, block_id: UUID) -> HttpResponse:
    block = get_for_action(
        request.user,
        Action.MANAGE_BLOCKS,
        Block.objects.select_related("center", "student", "schedule", "term").filter(
            center__slug=slug
        ),
        pk=block_id,
    )
    center = block.center
    form = LiftBlockForm(_posted(request))
    back = reverse("scheduling:blocks", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid() and block.lifted_at is None:
        blocks.lift(
            actor=signed_in(request), block=block, note=form.cleaned_data["note"], request=request
        )
        messages.success(
            request,
            _("Lifted. %(name)s can book online again.") % {"name": block.student.display_name},
        )
        return redirect(back)
    context = {
        "center": center,
        "booking_block": block,
        "crumbs": _block_crumbs(center),
        "counted": list(blocks.what_counted(block)),
        "form": form,
        "back": back,
        "is_active": block.is_active(timezone.localdate(timezone=ZoneInfo(center.time_zone))),
    }
    with timezone.override(center.time_zone):
        return render(request, "scheduling/block_lift.html", context)


@login_required
def block_settings(request: HttpRequest, slug: str) -> HttpResponse:
    """The center's limits, which count every schedule. Schedules set their own on their page."""
    center = center_for(request, Action.MANAGE_BLOCKS, slug)
    form = CenterBlockForm(_posted(request), instance=center)
    back = reverse("scheduling:blocks", kwargs={"slug": slug})
    if request.method == "POST" and form.is_valid():
        form.save()
        record(
            AuditAction.ATTENDANCE_SETTINGS_CHANGED,
            request=request,
            center=center,
            target=center,
            changed=form.changed_data,
        )
        messages.success(
            request, _("Saved. The new limits apply from the next no-show or late cancellation.")
        )
        return redirect(back)
    context = {
        "center": center,
        "form": form,
        "title": _("Booking block limits"),
        "crumbs": _block_crumbs(center),
        "submit": _("Save"),
        "back": back,
        "intro": _(
            "When a student reaches a limit, they can't book online until the end of the term, "
            "unless you lift the block sooner. Staff can still book for them. These limits count "
            "every schedule in the center. Each schedule can also set its own limits, which "
            "count only that schedule."
        ),
    }
    return render(request, "scheduling/form_page.html", context)
