"""The time clock page for staff, and timesheets and corrections for directors."""

from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.reports.tables import FORMATS, Column, Report, Table
from sympraxi.reports.views import download, downloads
from sympraxi.scheduling.timeutils import local_date, week_start
from sympraxi.staff import timeclock
from sympraxi.staff.forms import (
    ApprovalForm,
    ClockInForm,
    EntryForm,
    NetworksForm,
    PayrollForm,
    RemoveForm,
    TimesheetForm,
    WithdrawForm,
)
from sympraxi.staff.models import Category, PayrollDetail, TimeEntry, TimesheetApproval


def _today(center: Center) -> date:
    return local_date(timezone.now(), ZoneInfo(center.time_zone))


def _center_crumb(center: Center) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url())]


def _timesheet_crumbs(center: Center) -> list[tuple[str, str]]:
    return [
        *_center_crumb(center),
        (_("Timesheets"), reverse("staff:entries", kwargs={"slug": center.slug})),
    ]


@login_required
def clock(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.CLOCK_OWN, Center.objects.all(), slug=slug)
    me = cast(User, request.user)
    here = reverse("staff:clock", kwargs={"slug": slug})
    form = ClockInForm(request.POST if request.POST.get("action") == "in" else None)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "out":
                entry = timeclock.clock_out(center, me, request=request)
                messages.success(
                    request,
                    _("You're clocked out: %(hours)s hours.") % {"hours": entry.hours},
                )
                return redirect(here)
            if form.is_valid():
                timeclock.clock_in(
                    center,
                    me,
                    form.cleaned_data["category"],
                    form.cleaned_data["note"],
                    request=request,
                )
                messages.success(request, _("You're clocked in."))
                return redirect(here)
        except timeclock.TimeClockError as problem:
            messages.error(request, str(problem))
            return redirect(here)
    today = _today(center)
    this_week = week_start(today)
    weeks = []
    for label, first in ((_("This week"), this_week), (_("Last week"), this_week - timedelta(7))):
        entries = list(
            timeclock.entries_started_between(center, first, first + timedelta(days=6)).filter(
                person=me
            )
        )
        weeks.append(
            {
                "label": label,
                "first": first,
                "entries": entries,
                "total": sum((e.hours for e in entries if e.hours is not None), start=0),
            }
        )
    context = {
        "center": center,
        "open": timeclock.open_entry(center, me),
        "form": form,
        "weeks": weeks,
        "crumbs": _center_crumb(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "staff/clock.html", context)


def timesheet(
    center: Center, first: date, last: date, *, approved_only: bool = False
) -> tuple[Report, list[TimeEntry]]:
    """The totals and the entries, as tables for the page and the downloads."""
    entries = list(timeclock.entries_started_between(center, first, last))
    tz = ZoneInfo(center.time_zone)
    approvals = list(
        TimesheetApproval.objects.filter(
            center=center, withdrawn_at__isnull=True, first_day__lte=last, last_day__gte=first
        )
    )
    details = {d.person_id: d for d in PayrollDetail.objects.filter(center=center)}

    def approved(entry: TimeEntry) -> bool:
        day = entry.started_at.astimezone(tz).date()
        return any(
            a.person_id == entry.person_id and a.first_day <= day <= a.last_day for a in approvals
        )

    approved_ids = {e.pk for e in entries if approved(e)}
    for e in entries:
        e.is_approved = e.pk in approved_ids  # type: ignore[attr-defined]
    if approved_only:
        entries = [e for e in entries if e.pk in approved_ids]
    categories = list(Category)
    totals = Table(
        name="totals",
        title=_("Hours by person"),
        columns=[
            Column("person", _("Person")),
            Column("email", _("Email")),
            Column("payroll_id", _("Payroll ID")),
            Column("pay_code", _("Pay code")),
            *[Column(c.value, str(c.label), numeric=True) for c in categories],
            Column("total", _("Total hours"), numeric=True),
            Column("entries", _("Entries"), numeric=True),
            Column("approved", _("Approved")),
        ],
        note=_(
            "Each entry counts on the day it started. Anyone still clocked in is counted "
            "once they clock out."
        ),
    )
    for person in timeclock.totals(entries):
        mine = [e for e in entries if e.person_id == person.person.pk]
        done = sum(1 for e in mine if e.pk in approved_ids)
        state = _("Yes") if done == len(mine) else (_("Partly") if done else _("No"))
        detail = details.get(person.person.pk)
        totals.rows.append(
            [
                person.person.full_name,
                person.person.email,
                detail.payroll_id if detail else "",
                detail.pay_code if detail else "",
                *[person.by_category.get(c.value, 0) for c in categories],
                person.total,
                person.entries,
                state,
            ]
        )
    listed = Table(
        name="entries",
        title=_("Entries"),
        columns=[
            Column("person", _("Person")),
            Column("day", _("Day")),
            Column("started", _("Started")),
            Column("ended", _("Ended")),
            Column("hours", _("Hours"), numeric=True),
            Column("category", _("What for")),
            Column("note", _("Note")),
            Column("how", _("How it was entered")),
            Column("approved", _("Approved")),
        ],
    )
    for entry in entries:
        start = entry.started_at.astimezone(tz)
        end = entry.ended_at.astimezone(tz) if entry.ended_at else None
        listed.rows.append(
            [
                entry.person.full_name,
                start.date(),
                start.strftime("%H:%M"),
                (
                    end.strftime("%H:%M")
                    if end.date() == start.date()
                    else end.strftime("%Y-%m-%d %H:%M")
                )
                if end
                else _("Still clocked in"),
                entry.hours,
                entry.get_category_display(),
                entry.note,
                _how(entry),
                _("Yes") if entry.pk in approved_ids else _("No"),
            ]
        )
    report = Report(
        kind="timesheet",
        title=_("Timesheet"),
        described={
            _("Period"): _("%(first)s to %(last)s")
            % {"first": date_format(first, "F j, Y"), "last": date_format(last, "F j, Y")},
            **({_("Hours shown"): _("Approved hours only")} if approved_only else {}),
        },
        tables=[totals, listed],
        meta={"from": first, "to": last},
    )
    return report, entries


def _how(entry: TimeEntry) -> str:
    if entry.closed_automatically:
        return _("Clocked out at midnight; needs a director to check")
    if entry.changed_by_id:
        return _("Changed by a director")
    if entry.added_by_id:
        return _("Added by a director")
    return _("Clocked in and out")


@login_required
def entries(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TIME, Center.objects.all(), slug=slug)
    form = TimesheetForm(request.GET or None, today=_today(center))
    first, last = form.period()
    approved_only = request.GET.get("approved") == "1"
    report, listed = timesheet(center, first, last, approved_only=approved_only)
    fmt = request.GET.get("format", "")
    if fmt in FORMATS and not form.errors:
        return download(report, fmt, stem=f"timesheet-{first.isoformat()}-to-{last.isoformat()}")
    clocked_in = (
        TimeEntry.objects.filter(center=center, ended_at__isnull=True, removed_at__isnull=True)
        .select_related("person")
        .order_by("started_at")
    )
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "first": first,
        "last": last,
        "totals": report.tables[0],
        "entries": listed,
        "clocked_in": clocked_in,
        "to_check": sum(1 for e in listed if e.closed_automatically),
        "approved_only": approved_only,
        "downloads": downloads(request),
        "crumbs": _center_crumb(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "staff/entries.html", context)


@login_required
def entry_add(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TIME, Center.objects.all(), slug=slug)
    form = EntryForm(request.POST or None, center=center)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            timeclock.add_entry(
                center,
                data["person_user"],
                category=data["category"],
                started_at=data["started_at"],
                ended_at=data["ended_at"],
                note=data["note"],
                reason=data["reason"],
                request=request,
            )
        except timeclock.TimeClockError as problem:
            form.add_error(None, str(problem))
        else:
            messages.success(request, _("Entry added."))
            return redirect(reverse("staff:entries", kwargs={"slug": slug}))
    context = {
        "center": center,
        "form": form,
        "title": _("Add time for someone"),
        "submit": _("Add the entry"),
        "crumbs": _timesheet_crumbs(center),
    }
    return render(request, "staff/entry_form.html", context)


@login_required
def entry_edit(request: HttpRequest, slug: str, entry_id: UUID) -> HttpResponse:
    entry = get_for_action(
        request.user,
        Action.MANAGE_TIME,
        TimeEntry.objects.filter(center__slug=slug, removed_at__isnull=True).select_related(
            "center", "person"
        ),
        pk=entry_id,
    )
    center = entry.center
    back = reverse("staff:entries", kwargs={"slug": slug})
    removing = request.method == "POST" and "remove" in request.POST
    form = EntryForm(
        request.POST if request.method == "POST" and not removing else None,
        center=center,
        entry=entry,
    )
    remove_form = RemoveForm(request.POST if removing else None, prefix="remove")
    if removing and remove_form.is_valid():
        timeclock.remove_entry(entry, reason=remove_form.cleaned_data["reason"], request=request)
        messages.success(request, _("Entry removed."))
        return redirect(back)
    if request.method == "POST" and not removing and form.is_valid():
        data = form.cleaned_data
        try:
            timeclock.change_entry(
                entry,
                category=data["category"],
                started_at=data["started_at"],
                ended_at=data["ended_at"],
                note=data["note"],
                reason=data["reason"],
                request=request,
            )
        except timeclock.TimeClockError as problem:
            form.add_error(None, str(problem))
        else:
            messages.success(request, _("Entry saved."))
            return redirect(back)
    context = {
        "center": center,
        "entry": entry,
        "form": form,
        "remove_form": remove_form,
        "title": _("Change %(name)s's time") % {"name": entry.person.display_name},
        "submit": _("Save the entry"),
        "crumbs": _timesheet_crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "staff/entry_form.html", context)


@login_required
def approvals(request: HttpRequest, slug: str) -> HttpResponse:
    """Approve or withdraw people's hours for the days chosen."""
    center = get_for_action(request.user, Action.MANAGE_TIME, Center.objects.all(), slug=slug)
    form = TimesheetForm(request.GET or None, today=_today(center))
    first, last = form.period()
    here = reverse("staff:approvals", kwargs={"slug": slug})
    back = f"{here}?first={first.isoformat()}&last={last.isoformat()}"
    withdraw_form = WithdrawForm(prefix="w")
    if request.method == "POST":
        if request.POST.get("action") == "approve":
            posted = ApprovalForm(request.POST)
            person = None
            if posted.is_valid():
                person = (
                    User.objects.filter(
                        pk=posted.cleaned_data["person"], time_entries__center=center
                    )
                    .distinct()
                    .first()
                )
            if person is None:
                messages.error(request, _("That person has no hours in this center."))
            else:
                try:
                    timeclock.approve_period(
                        center,
                        person,
                        posted.cleaned_data["first"],
                        posted.cleaned_data["last"],
                        request=request,
                    )
                except timeclock.TimeClockError as problem:
                    messages.error(request, str(problem))
                else:
                    messages.success(request, _("Hours approved."))
            return redirect(back)
        if request.POST.get("action") == "withdraw":
            withdraw_form = WithdrawForm(request.POST, prefix="w")
            if withdraw_form.is_valid():
                approval = TimesheetApproval.objects.filter(
                    center=center,
                    pk=withdraw_form.cleaned_data["approval"],
                    withdrawn_at__isnull=True,
                ).first()
                if approval is not None:
                    timeclock.withdraw_approval(
                        approval, reason=withdraw_form.cleaned_data["reason"], request=request
                    )
                    messages.success(request, _("The approval is withdrawn."))
                    return redirect(back)
                messages.error(request, _("That approval is no longer in place."))
                return redirect(back)
    _report, listed = timesheet(center, first, last)
    approvals_now = {
        (a.person_id): a
        for a in TimesheetApproval.objects.filter(
            center=center, withdrawn_at__isnull=True, first_day__lte=last, last_day__gte=first
        ).select_related("approved_by")
    }
    people = []
    for row in timeclock.totals(listed):
        people.append(
            {
                "person": row.person,
                "hours": row.total,
                "entries": row.entries,
                "open": row.open,
                "approval": approvals_now.get(row.person.pk),
            }
        )
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "first": first,
        "last": last,
        "people": people,
        "crumbs": _timesheet_crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "staff/approvals.html", context)


@login_required
def settings(request: HttpRequest, slug: str) -> HttpResponse:
    """Where staff may clock in from, and the payroll ID and pay code for each person."""
    center = get_for_action(request.user, Action.MANAGE_TIME, Center.objects.all(), slug=slug)
    here = reverse("staff:settings", kwargs={"slug": slug})
    saving = request.POST.get("save")
    networks = NetworksForm(
        request.POST if saving == "networks" else None,
        initial={"clock_networks": center.clock_networks},
    )
    payroll = PayrollForm(request.POST if saving == "payroll" else None, center=center)
    if request.method == "POST" and saving == "networks" and networks.is_valid():
        center.clock_networks = networks.cleaned_data["clock_networks"]
        center.save(update_fields=["clock_networks"])
        record(
            AuditAction.TIME_SETTINGS_CHANGED,
            request=request,
            center=center,
            target=center,
            networks=len(center.clock_networks.splitlines()),
        )
        messages.success(request, _("Saved."))
        return redirect(here)
    if request.method == "POST" and saving == "payroll" and payroll.is_valid():
        changed = 0
        for person, id_field, code_field in payroll.rows():
            detail, _made = PayrollDetail.objects.get_or_create(center=center, person=person)
            new_id, new_code = (
                payroll.cleaned_data[id_field.name],
                payroll.cleaned_data[code_field.name],
            )
            if (detail.payroll_id, detail.pay_code) != (new_id, new_code):
                detail.payroll_id, detail.pay_code = new_id, new_code
                detail.save(update_fields=["payroll_id", "pay_code"])
                changed += 1
        record(
            AuditAction.TIME_SETTINGS_CHANGED,
            request=request,
            center=center,
            target=center,
            payroll_details_changed=changed,
        )
        messages.success(request, _("Saved."))
        return redirect(here)
    context = {
        "center": center,
        "networks": networks,
        "payroll": payroll,
        "your_address": request.META.get("REMOTE_ADDR", ""),
        "crumbs": _timesheet_crumbs(center),
    }
    return render(request, "staff/settings.html", context)
