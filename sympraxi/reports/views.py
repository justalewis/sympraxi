"""Report pages: statistics, utilization, the master lists, and the full-center export.

Each report page downloads as CSV, XLSX or JSON from the same address with
`?format=`, so a download always holds what the page shows for the same
filters. Pages and downloads that name students are audited with the students
named; statistics and utilization name only tutors and aren't.
"""

from datetime import date
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.reports import (
    center_export,
    demographics,
    lists,
    resources,
    statistics,
    tables,
    tasks,
    utilization,
)
from sympraxi.reports.forms import (
    AppointmentListForm,
    Period,
    PeriodForm,
    ReportQuestionsForm,
    list_choice,
)
from sympraxi.reports.models import UNFINISHED, CenterExport, ExportStatus
from sympraxi.reports.tables import FORMATS, Report
from sympraxi.scheduling.notifications import base_url
from sympraxi.scheduling.views import signed_in

# Students per page on the student list. Downloads hold every one.
STUDENTS_PER_PAGE = 250
FORMAT_LABELS = {"csv": "CSV", "xlsx": "Excel (XLSX)", "json": "JSON"}


def _center(request: HttpRequest, action: Action, slug: str) -> Center:
    return get_for_action(request.user, action, Center.objects.all(), slug=slug)


def _crumbs(center: Center) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url())]


def _today(center: Center) -> date:
    return timezone.localdate(timezone=ZoneInfo(center.time_zone))


def _wanted_format(request: HttpRequest) -> str | None:
    fmt = request.GET.get("format", "")
    return fmt if fmt in FORMATS else None


def downloads(request: HttpRequest) -> list[tuple[str, str]]:
    """Links to this page's report in each format, with the same filters."""
    query = request.GET.copy()
    query.pop("format", None)
    query.pop("page", None)
    links = []
    for fmt in FORMATS:
        query["format"] = fmt
        links.append((FORMAT_LABELS[fmt], "?" + query.urlencode()))
    return links


def download(report: Report, fmt: str, *, stem: str) -> HttpResponse:
    response = HttpResponse(tables.render(report, fmt), content_type=tables.CONTENT_TYPES[fmt])
    response["Content-Disposition"] = f'attachment; filename="{stem}.{fmt}"'
    # Reports hold names and counts; no browser or proxy should keep a copy.
    add_never_cache_headers(response)
    return response


def _stem(kind: str, first: date, last: date) -> str:
    if first == last:
        return f"{kind}-{first.isoformat()}"
    return f"{kind}-{first.isoformat()}-to-{last.isoformat()}"


def _period_page(
    request: HttpRequest,
    center: Center,
    build: Any,
    template: str,
    extra: Any = None,
) -> HttpResponse:
    form = PeriodForm(request.GET or None, center=center, today=_today(center))
    invalid = form.is_bound and not form.is_valid()
    period = form.period()
    form.initial_for(period)
    fmt = _wanted_format(request)
    report = None if invalid else build(center, period)
    if report is not None and fmt is not None:
        return download(report, fmt, stem=_stem(report.kind, period.first, period.last))
    context = {
        "center": center,
        "form": form,
        "period": period,
        "report": report,
        "downloads": downloads(request),
        "crumbs": _crumbs(center),
    }
    if extra is not None:
        context.update(extra(period))
    with timezone.override(center.time_zone):
        return render(request, template, context)


@login_required
def statistics_page(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.VIEW_STATISTICS, slug)
    return _period_page(request, center, statistics.build, "reports/statistics.html")


@login_required
def resources_page(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.VIEW_STATISTICS, slug)
    return _period_page(request, center, resources.build, "reports/resources.html")


@login_required
def by_course_page(request: HttpRequest, slug: str) -> HttpResponse:
    """Topics by class standing and course, and across terms."""
    center = _center(request, Action.VIEW_STATISTICS, slug)
    standing = request.GET.get("standing", "").strip()[:200]

    def build(center: Center, period: Any) -> Report:
        return demographics.build(center, period, standing=standing)

    return _period_page(
        request,
        center,
        build,
        "reports/by_course.html",
        extra=lambda period: {
            "standing": standing,
            "standings": demographics.standings_seen(center, period),
            "configured": demographics.settings_for(center),
        },
    )


@login_required
def report_questions(request: HttpRequest, slug: str) -> HttpResponse:
    """A director chooses which questions the by-course report reads."""
    center = _center(request, Action.MANAGE_FORMS, slug)
    current = demographics.settings_for(center)
    choices = [(str(key), label) for key, label in demographics.candidates(center)]
    initial = {
        "standing": str(current.standing_key or ""),
        "course": str(current.course_key or ""),
    }
    form = ReportQuestionsForm(
        request.POST if request.method == "POST" else None, choices=choices, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        current.standing_key = (
            UUID(form.cleaned_data["standing"]) if form.cleaned_data["standing"] else None
        )
        current.course_key = (
            UUID(form.cleaned_data["course"]) if form.cleaned_data["course"] else None
        )
        current.save()
        record(AuditAction.REPORT_QUESTIONS_CHANGED, request=request, center=center, target=current)
        messages.success(request, _("The reports will read these questions."))
        return redirect("reports:report_questions", slug=slug)
    context = {"center": center, "form": form, "crumbs": _crumbs(center)}
    return render(request, "reports/report_questions.html", context)


@login_required
def utilization_page(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.VIEW_STATISTICS, slug)
    return _period_page(request, center, utilization.build, "reports/utilization.html")


# --- Master lists -------------------------------------------------------------------


def _audit_list(
    request: HttpRequest,
    center: Center,
    which: str,
    fmt: str | None,
    students: list[str],
    **details: Any,
) -> None:
    record(
        AuditAction.LIST_EXPORTED if fmt else AuditAction.LIST_VIEWED,
        request=request,
        center=center,
        list=which,
        format=fmt or "page",
        students=students,
        **details,
    )


@login_required
def appointment_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Every appointment on a day or in a week, for the front desk to print."""
    center = _center(request, Action.VIEW_MASTER_LISTS, slug)
    today = _today(center)
    data = None
    if request.GET:
        # A link that leaves out the day or the span means today, one day.
        data = request.GET.copy()
        data.setdefault("date", today.isoformat())
        data.setdefault("span", "day")
    form = AppointmentListForm(data, center=center, initial={"date": today, "span": "day"})
    choice = list_choice(form, today)
    found = lists.appointment_list(center, choice)
    fmt = _wanted_format(request)
    _audit_list(
        request,
        center,
        "appointments",
        fmt,
        found.student_ids,
        first=choice.first.isoformat(),
        last=choice.last.isoformat(),
        schedule=str(choice.schedule.pk) if choice.schedule else None,
    )
    if fmt is not None:
        return download(found.report, fmt, stem=_stem("appointments", choice.first, choice.last))
    context = {
        "center": center,
        "form": form,
        "choice": choice,
        "found": found,
        "limit": lists.APPOINTMENT_LIMIT,
        "downloads": downloads(request),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        response = render(request, "reports/appointment_list.html", context)
    add_never_cache_headers(response)
    return response


@login_required
def student_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Students who booked in a period, with how many times they came."""
    center = _center(request, Action.VIEW_MASTER_LISTS, slug)
    form = PeriodForm(request.GET or None, center=center, today=_today(center))
    invalid = form.is_bound and not form.is_valid()
    period: Period = form.period()
    form.initial_for(period)
    fmt = _wanted_format(request)
    found = lists.students(center, period)
    details = {
        "first": period.first.isoformat(),
        "last": period.last.isoformat(),
        "schedule": str(period.schedule.pk) if period.schedule else None,
    }
    if fmt is not None and not invalid:
        members = list(found)
        _audit_list(request, center, "students", fmt, [str(m.user_id) for m in members], **details)
        report = lists.student_report(center, period, members)
        return download(report, fmt, stem=_stem("students", period.first, period.last))
    page = Paginator(found, STUDENTS_PER_PAGE).get_page(request.GET.get("page"))
    members = [] if invalid else list(page.object_list)
    _audit_list(
        request,
        center,
        "students",
        None,
        [str(m.user_id) for m in members],
        page=page.number,
        **details,
    )
    query = request.GET.copy()
    query.pop("page", None)
    query.pop("format", None)
    context = {
        "center": center,
        "form": form,
        "period": period,
        "invalid": invalid,
        "page": page,
        "table": lists.student_report(center, period, members).tables[0],
        "page_query": query.urlencode(),
        "downloads": downloads(request),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        response = render(request, "reports/student_list.html", context)
    add_never_cache_headers(response)
    return response


# --- The full-center export -----------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def center_export_page(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.EXPORT_CENTER, slug)
    exports = CenterExport.objects.filter(center=center).select_related("requested_by")
    if request.method == "POST":
        try:
            with transaction.atomic():
                export = CenterExport.objects.create(center=center, requested_by=signed_in(request))
                record(
                    AuditAction.CENTER_EXPORT_REQUESTED,
                    request=request,
                    center=center,
                    target=export,
                )
                tasks.build_center_export.enqueue(str(export.pk), base_url(request))
        except IntegrityError:
            messages.warning(request, _("An export is already being built. It will be ready soon."))
        else:
            messages.success(
                request,
                _("The export is being built. We'll email you when it's ready to download."),
            )
        return redirect("reports:center_export", slug=center.slug)
    now = timezone.now()
    shown = list(exports[:10])
    context = {
        "center": center,
        "exports": shown,
        "now": now,
        "building": any(e.status in UNFINISHED for e in shown),
        "link_hours": center_export.LINK_HOURS,
        "ExportStatus": ExportStatus,
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        response = render(request, "reports/center_export.html", context)
    add_never_cache_headers(response)
    return response


@login_required
def center_export_download(request: HttpRequest, slug: str, export_id: UUID) -> HttpResponseBase:
    export = get_for_action(
        request.user,
        Action.EXPORT_CENTER,
        CenterExport.objects.select_related("center").filter(center__slug=slug),
        pk=export_id,
    )
    center = export.center
    stream = None
    if export.can_download(timezone.now()):
        try:
            stream = center_export.storage().open(export.file_name, "rb")
        except FileNotFoundError:
            stream = None
    if stream is None:
        context = {
            "center": center,
            "crumbs": _crumbs(center),
            "link_hours": center_export.LINK_HOURS,
        }
        return render(request, "reports/center_export_gone.html", context, status=410)
    record(
        AuditAction.CENTER_EXPORT_DOWNLOADED,
        request=request,
        center=center,
        target=export,
        size=export.size,
    )
    ready = timezone.localdate(
        export.ready_at or timezone.now(), timezone=ZoneInfo(center.time_zone)
    )
    response = FileResponse(
        stream,
        as_attachment=True,
        filename=f"{center.slug}-export-{ready.isoformat()}.zip",
        content_type="application/zip",
    )
    add_never_cache_headers(response)
    return response
