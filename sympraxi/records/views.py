"""Pages for writing and reading session records, and the student's My writing page.

Every staff read of a record or of a student's history is audited. The record
form works without JavaScript: attaching a resource or searching the catalog
posts the whole form back and redraws it, unsaved. With htmx, suggestions
follow the topic checkboxes, the search updates as the tutor types, and
attaching adds the resource without reloading.
"""

import json
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.centers.views import HtmxHttpRequest
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.forms import display as form_display
from sympraxi.forms import export as form_export
from sympraxi.forms import report
from sympraxi.forms.answering import CONTINUE, Questions, follow_up_message
from sympraxi.forms.models import Response
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import export, linkcheck, services, suggestions
from sympraxi.records.display import by_project, topic_axes, topics_by_axis
from sympraxi.records.forms import (
    Attached,
    ProjectForm,
    RecordForm,
    RecordSettingsForm,
    ResourceForm,
    StandaloneForm,
    attached_from_post,
    attached_from_record,
    topic_choices,
)
from sympraxi.records.models import (
    AttachedResource,
    LinkCheckRun,
    LinkState,
    RecordStatus,
    Resource,
    SessionRecord,
    Topic,
    WritingProject,
)
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Block, Participant
from sympraxi.scheduling.views import appointment_for, center_for, signed_in

# How far back the "records to write" list looks.
TO_WRITE_DAYS = 90


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url()), *extra]


def _to_write_crumb(center: Center) -> tuple[str, str]:
    return (_("Records to write"), reverse("records:to_write", kwargs={"slug": center.slug}))


def record_for(request: HttpRequest, action: Action, slug: str, record_id: UUID) -> SessionRecord:
    return get_for_action(
        request.user,
        action,
        SessionRecord.objects.select_related(
            "center", "student", "tutor", "appointment__schedule", "project", "finalized_by"
        ).filter(center__slug=slug),
        pk=record_id,
    )


def student_for(request: HttpRequest, action: Action, slug: str, membership_id: UUID) -> Membership:
    """A student in a center, the object that records "about a student" are checked against."""
    return get_for_action(
        request.user,
        action,
        Membership.objects.select_related("user", "center").filter(
            center__slug=slug, role=Role.STUDENT
        ),
        pk=membership_id,
    )


def membership_of(center: Center, student: User) -> Membership | None:
    return Membership.objects.filter(center=center, user=student, role=Role.STUDENT).first()


def _audit_read(
    request: HttpRequest, rec: SessionRecord, where: str, answers: Response | None = None
) -> None:
    """A staff read of a record. Names the record's report answers if they were shown."""
    details = {"report_answers": str(answers.pk)} if answers is not None else {}
    record(
        AuditAction.RECORD_VIEWED,
        request=request,
        center=rec.center,
        target=rec,
        student=str(rec.student_id),
        where=where,
        **details,
    )


# --- Writing a record -----------------------------------------------------------


@login_required
def record_start(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """The record form for one student in an appointment that has no record for them yet."""
    appointment = appointment_for(request, Action.WRITE_RECORDS, slug, appointment_id)
    back = reverse(
        "scheduling:appointment", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    if appointment.status == AppointmentStatus.CANCELED:
        messages.warning(request, _("This appointment was canceled, so it has no record."))
        return redirect(back)
    students = services.active_students(appointment)
    wanted = request.POST.get("student") or request.GET.get("student")
    if wanted:
        student = next((s for s in students if str(s.pk) == wanted), None)
        if student is None:
            raise Http404
    elif len(students) == 1:
        student = students[0]
    else:
        messages.info(request, _("Choose which student's record to write."))
        return redirect(back)
    existing = SessionRecord.objects.filter(appointment=appointment, student=student).first()
    if existing is not None:
        name = "records:amend" if existing.is_final else "records:edit"
        return redirect(name, slug=slug, record_id=existing.pk)
    return _record_page(
        request,
        center=appointment.schedule.center,
        appointment=appointment,
        student=student,
        rec=None,
    )


@login_required
def record_edit(request: HttpRequest, slug: str, record_id: UUID) -> HttpResponse:
    rec = record_for(request, Action.WRITE_RECORDS, slug, record_id)
    if rec.is_final:
        return redirect("records:amend", slug=slug, record_id=rec.pk)
    return _record_page(
        request, center=rec.center, appointment=rec.appointment, student=rec.student, rec=rec
    )


@login_required
def record_amend(request: HttpRequest, slug: str, record_id: UUID) -> HttpResponse:
    rec = record_for(request, Action.WRITE_RECORDS, slug, record_id)
    if not rec.is_final:
        return redirect("records:edit", slug=slug, record_id=rec.pk)
    return _record_page(
        request,
        center=rec.center,
        appointment=rec.appointment,
        student=rec.student,
        rec=rec,
        amend=True,
    )


def _record_initial(rec: SessionRecord | None, appointment: Appointment | None) -> dict[str, Any]:
    if rec is None:
        return {"minutes": appointment.minutes if appointment else None}
    return {
        "summary": rec.summary,
        "next_steps": rec.next_steps,
        "staff_notes": rec.staff_notes,
        "minutes": rec.minutes,
        "topics": list(rec.topics.values_list("slug", flat=True)),
        "project": str(rec.project_id) if rec.project_id else "",
    }


RECORD_FIELDS = (
    "summary",
    "next_steps",
    "staff_notes",
    "minutes",
    "project",
    "project_title",
    "project_course",
    "project_instructor",
    "project_due_on",
    "reason",
)


def _initial_from_post(data: QueryDict) -> dict[str, Any]:
    initial: dict[str, Any] = {name: data.get(name, "") for name in RECORD_FIELDS}
    initial["topics"] = data.getlist("topics")
    initial["copy_to_group"] = bool(data.get("copy_to_group"))
    return initial


def _others_without_records(appointment: Appointment | None, student: User) -> int:
    if appointment is None:
        return 0
    taken = set(
        SessionRecord.objects.filter(appointment=appointment).values_list("student_id", flat=True)
    )
    return sum(
        1 for s in services.active_students(appointment) if s.pk != student.pk and s.pk not in taken
    )


def _record_page(
    request: HttpRequest,
    *,
    center: Center,
    appointment: Appointment | None,
    student: User,
    rec: SessionRecord | None,
    amend: bool = False,
) -> HttpResponse:
    posted = request.method == "POST"
    questions = report.questions_for(
        center=center, rec=rec, amend=amend, data=request.POST if posted else None
    )
    # Follow-up questions the answers call for, which the page didn't show yet
    # (or Continue pressed): nothing is saved, and the page shows them.
    follow_ups = (
        posted
        and questions is not None
        and (CONTINUE in request.POST or questions.needs_another_look)
    )
    # Attaching, searching or showing follow-ups without JavaScript redraws the
    # form as posted, unsaved and without error messages: nothing has been
    # submitted yet.
    redraw = posted and ("attach" in request.POST or "search" in request.POST or follow_ups)
    form_status = ""
    if questions is not None and redraw:
        if follow_ups and CONTINUE not in request.POST:
            form_status = follow_up_message(_submit_label(request, amend))
        questions = questions.again()
    others = 0 if amend else _others_without_records(appointment, student)
    form = RecordForm(
        request.POST if posted and not redraw else None,
        rec=rec,
        center=center,
        student=student,
        amend=amend,
        group_others=others,
        initial=_initial_from_post(request.POST) if redraw else _record_initial(rec, appointment),
    )
    attached = (
        attached_from_post(request.POST, center, rec) if posted else attached_from_record(rec)
    )
    query = request.POST.get("resource_q", "").strip() if posted else ""

    if posted and "attach" in request.POST:
        resource = _available(center, request.POST.get("attach", ""))
        if resource is not None and all(a.resource.pk != resource.pk for a in attached):
            attached.append(Attached(resource=resource, note=""))
            messages.info(
                request,
                _("Attached %(title)s. Save the record to keep it.") % {"title": resource.title},
            )
    elif posted and not redraw:
        # Validate both, so each shows its own errors.
        valid = [form.is_valid(), questions is None or questions.is_valid()]
        if all(valid):
            saved = _save_record(
                request, form, center, appointment, student, rec, attached, amend, questions
            )
            if saved is not None:
                return saved
    elif not posted and rec is not None:
        _audit_read(request, rec, "record form", report.response_of(rec))

    chosen = Topic.objects.filter(slug__in=form.topic_slugs())
    attached_ids = [a.resource.pk for a in attached]
    if amend:
        title = _("Amend a session record")
    elif rec is not None and not rec.is_final:
        title = _("Session record draft")
    else:
        title = _("Write a session record")
    context = {
        "center": center,
        "appointment": appointment,
        "student": student,
        "rec": rec,
        "form": form,
        "amend": amend,
        "title": title,
        "attached": attached,
        "axes": topic_axes(topic_choices(rec.topics.all() if rec else None), form.topic_slugs()),
        "suggestions": suggestions.suggest(center, chosen, exclude=attached_ids),
        "has_concern": any(t.axis == "concern" for t in chosen),
        "query": query,
        "results": suggestions.search(center, query, exclude=attached_ids) if query else [],
        "searched": posted and "search" in request.POST,
        "others": others,
        "questions": questions,
        "form_status": form_status or (questions.status if questions else ""),
        "crumbs": _form_crumbs(center, appointment, rec),
        "back_url": _form_crumbs(center, appointment, rec)[-1][1],
    }
    with timezone.override(center.time_zone):
        return render(request, "records/record_form.html", context)


def _submit_label(request: HttpRequest, amend: bool) -> str:
    """The button the tutor pressed, for the message about follow-up questions."""
    if amend:
        return _("Save the amendment")
    if request.POST.get("action") == "finalize":
        return _("Save and finalize")
    return _("Save draft")


def _form_crumbs(
    center: Center, appointment: Appointment | None, rec: SessionRecord | None
) -> list[tuple[str, str]]:
    trail = _crumbs(center, _to_write_crumb(center))
    if appointment is not None:
        url = reverse(
            "scheduling:appointment",
            kwargs={"slug": center.slug, "appointment_id": appointment.pk},
        )
        trail.append((_("Appointment"), url))
    if rec is not None and rec.is_final:
        url = reverse("records:detail", kwargs={"slug": center.slug, "record_id": rec.pk})
        trail.append((_("Session record"), url))
    return trail


def _save_record(
    request: HttpRequest,
    form: RecordForm,
    center: Center,
    appointment: Appointment | None,
    student: User,
    rec: SessionRecord | None,
    attached: list[Attached],
    amend: bool,
    questions: Questions | None,
) -> HttpResponse | None:
    """Save from a valid form. Returns the redirect, or None to show the form's errors."""
    user = signed_in(request)
    finalizing = not amend and request.POST.get("action") == "finalize"
    copies: list[SessionRecord] = []
    try:
        with transaction.atomic():
            if rec is None:
                if appointment is None:
                    raise Http404
                saved = services.start_for_appointment(
                    actor=user, appointment=appointment, student=student, request=request
                )
            else:
                saved = rec
            project = form.chosen_project()
            if form.wants_new_project():
                project = services.new_project(
                    actor=user,
                    center=center,
                    student=student,
                    title=form.cleaned_data["project_title"].strip(),
                    course=form.cleaned_data["project_course"].strip(),
                    instructor=form.cleaned_data["project_instructor"].strip(),
                    due_on=form.cleaned_data["project_due_on"],
                    request=request,
                )
            content = form.content([(a.resource, a.note) for a in attached], project)
            if questions is not None:
                content.answers = report.ReportAnswers(
                    version=questions.version, values=questions.values()
                )
            if amend:
                services.amend(
                    actor=user,
                    rec=saved,
                    content=content,
                    reason=form.cleaned_data["reason"],
                    request=request,
                )
            else:
                saved = services.save_draft(actor=user, rec=saved, content=content, request=request)
            if form.cleaned_data.get("copy_to_group"):
                copies = services.copy_to_group(actor=user, rec=saved, request=request)
    except services.RecordError as exc:
        form.add_error(None, exc.message)
        return None

    detail = reverse("records:detail", kwargs={"slug": center.slug, "record_id": saved.pk})
    edit = reverse("records:edit", kwargs={"slug": center.slug, "record_id": saved.pk})
    if copies:
        messages.info(
            request,
            _(
                "Started drafts for %(n)s other students in the group. Read and finalize each "
                "one from the appointment page."
            )
            % {"n": len(copies)},
        )
    if amend:
        messages.success(request, _("Amendment saved. The earlier version is kept with it."))
        return redirect(detail)
    if finalizing:
        try:
            services.finalize(actor=user, rec=saved, request=request)
        except services.RecordError as exc:
            messages.warning(
                request,
                _("Saved as a draft. It isn't final yet: %(problems)s") % {"problems": exc.message},
            )
            return redirect(edit)
        messages.success(
            request,
            _("Finalized. %(name)s will get an email saying the summary is ready.")
            % {"name": student.display_name},
        )
        return redirect(detail)
    messages.success(request, _("Draft saved. The student can't see it until you finalize it."))
    return redirect(edit)


def _available(center: Center, value: str) -> Resource | None:
    try:
        pk = UUID(value)
    except ValueError:
        return None
    return Resource.objects.available_to(center).filter(pk=pk).first()


def _posted(request: HttpRequest) -> QueryDict:
    return request.POST if request.method == "POST" else request.GET


def _ids(values: list[str]) -> list[UUID]:
    result = []
    for value in values:
        try:
            result.append(UUID(value))
        except ValueError:
            continue
    return result


@login_required
def resource_panel(request: HtmxHttpRequest, slug: str) -> HttpResponse:
    """Suggestions for the chosen topics, and search results, for the record form."""
    center = center_for(request, Action.WRITE_RECORDS, slug)
    data = _posted(request)
    chosen = Topic.objects.filter(slug__in=data.getlist("topics"))
    exclude = _ids(data.getlist("resources"))
    query = data.get("resource_q", "").strip()
    found = suggestions.suggest(center, chosen, exclude=exclude)
    results = suggestions.search(center, query, exclude=exclude) if query else []
    context = {
        "center": center,
        "suggestions": found,
        "has_concern": any(t.axis == "concern" for t in chosen),
        "query": query,
        "results": results,
        "htmx": bool(request.htmx),
    }
    return render(request, "records/_resource_results.html", context)


@login_required
def attach_row(request: HtmxHttpRequest, slug: str) -> HttpResponse:
    """One attached-resource row for the record form, added without reloading."""
    center = center_for(request, Action.WRITE_RECORDS, slug)
    data = _posted(request)
    value = data.get("attach", "")
    already = value in data.getlist("resources")
    resource = None if already else _available(center, value)
    if already:
        status = _("That resource is already attached.")
    elif resource is None:
        status = ""
    else:
        status = _("Attached %(title)s. Save the record to keep it.") % {"title": resource.title}
    context = {
        "center": center,
        "a": Attached(resource=resource, note="") if resource else None,
        "status": status,
        "htmx": bool(request.htmx),
    }
    return render(request, "records/_attach_response.html", context)


@login_required
def standalone_start(request: HttpRequest, slug: str) -> HttpResponse:
    """Start a record for a meeting that wasn't booked."""
    center = center_for(request, Action.WRITE_RECORDS, slug)
    with timezone.override(center.time_zone):
        form = StandaloneForm(request.POST or None, center=center, initial={"minutes": 30})
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["held_at"] > timezone.now():
                form.add_error("held_at", _("Write the record after the meeting has started."))
            else:
                rec = services.start_standalone(
                    actor=signed_in(request),
                    center=center,
                    student=form.cleaned_data["student"],
                    held_at=form.cleaned_data["held_at"],
                    minutes=form.cleaned_data["minutes"],
                    request=request,
                )
                return redirect("records:edit", slug=slug, record_id=rec.pk)
        context = {
            "center": center,
            "form": form,
            "title": _("Record a meeting that wasn't booked"),
            "intro": _("Choose the student and when you met. Next you'll write the record itself."),
            "crumbs": _crumbs(center, _to_write_crumb(center)),
            "submit": _("Continue"),
            "back": reverse("records:to_write", kwargs={"slug": slug}),
        }
        return render(request, "scheduling/form_page.html", context)


# --- Reading records ------------------------------------------------------------


@login_required
def record_detail(request: HttpRequest, slug: str, record_id: UUID) -> HttpResponse:
    """A whole record for staff: staff notes, tags, resources and every amendment."""
    rec = record_for(request, Action.VIEW_RECORDS, slug, record_id)
    answers = report.for_staff(rec)
    _audit_read(request, rec, "record page", answers.response if answers else None)
    center = rec.center
    user = request.user
    membership = membership_of(center, rec.student)
    context = {
        "center": center,
        "rec": rec,
        "answers": answers,
        "topics": topics_by_axis(rec.topics.all()),
        "attachments": rec.attachments.select_related("resource"),
        "amendments": rec.amendments.select_related("author"),
        "can_write": can(user, Action.WRITE_RECORDS, rec),
        "problems": [] if rec.is_final else services.finalize_problems(rec),
        "history_url": reverse(
            "records:student_history", kwargs={"slug": slug, "membership_id": membership.pk}
        )
        if membership is not None and can(user, Action.VIEW_RECORDS, membership)
        else "",
        "crumbs": _detail_crumbs(rec),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/record_detail.html", context)


def _detail_crumbs(rec: SessionRecord) -> list[tuple[str, str]]:
    center = rec.center
    if rec.appointment_id is not None:
        url = reverse(
            "scheduling:appointment",
            kwargs={"slug": center.slug, "appointment_id": rec.appointment_id},
        )
        return _crumbs(center, (_("Appointment"), url))
    return _crumbs(center, _to_write_crumb(center))


@login_required
def to_write(request: HttpRequest, slug: str) -> HttpResponse:
    """Past appointments still missing a final record, and drafts not yet finalized."""
    center = center_for(request, Action.VIEW_RECORD_STATUS, slug)
    user = request.user
    now = timezone.now()
    appointments = (
        Appointment.objects.filter(
            schedule__center=center,
            starts_at__lte=now,
            starts_at__gte=now - timedelta(days=TO_WRITE_DAYS),
            is_placeholder=False,
        )
        .exclude(status=AppointmentStatus.CANCELED)
        .select_related("tutor", "schedule")
        .prefetch_related(
            Prefetch(
                "participants",
                queryset=Participant.objects.filter(canceled_at__isnull=True).select_related(
                    "student"
                ),
                to_attr="active",
            ),
            Prefetch("records", to_attr="written"),
        )
        .order_by("-starts_at")
    )
    if not can(user, Action.VIEW_ALL_APPOINTMENTS, center):
        appointments = appointments.filter(tutor=signed_in(request))
    rows = []
    for appointment in appointments[:500]:
        if not can(user, Action.VIEW_RECORD_STATUS, appointment):
            continue
        written = {r.student_id: r for r in appointment.written}
        writable = can(user, Action.WRITE_RECORDS, appointment)
        for participant in appointment.active:
            rec = written.get(participant.student_id)
            if rec is not None and rec.is_final:
                continue
            rows.append(
                {
                    "appointment": appointment,
                    "student": participant.student,
                    "rec": rec,
                    "write_url": _write_url(center, appointment, participant.student, rec)
                    if writable
                    else "",
                }
            )
    drafts: QuerySet[SessionRecord] = SessionRecord.objects.filter(
        center=center, appointment__isnull=True, status=RecordStatus.DRAFT
    ).select_related("student", "tutor")
    if not can(user, Action.VIEW_ALL_APPOINTMENTS, center):
        drafts = drafts.filter(tutor=signed_in(request))
    context = {
        "center": center,
        "rows": rows,
        "drafts": [d for d in drafts if can(user, Action.WRITE_RECORDS, d)],
        "days": TO_WRITE_DAYS,
        "can_write": can(user, Action.WRITE_RECORDS, center),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/to_write.html", context)


def _write_url(
    center: Center, appointment: Appointment, student: User, rec: SessionRecord | None
) -> str:
    if rec is not None:
        return reverse("records:edit", kwargs={"slug": center.slug, "record_id": rec.pk})
    url = reverse("records:start", kwargs={"slug": center.slug, "appointment_id": appointment.pk})
    return f"{url}?student={student.pk}"


def _student_records(center: Center, student: User, *, final_only: bool) -> list[SessionRecord]:
    records = (
        SessionRecord.objects.filter(center=center, student=student)
        .select_related("tutor", "project")
        .prefetch_related(
            Prefetch("attachments", queryset=AttachedResource.objects.select_related("resource")),
            "amendments",
            "topics",
        )
        .order_by("-held_at")
    )
    if final_only:
        records = records.filter(status=RecordStatus.FINAL)
    return list(records)


@login_required
def student_history(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    """Every record about one student in this center, for the staff who work with them."""
    membership = student_for(request, Action.VIEW_RECORDS, slug, membership_id)
    center = membership.center
    student = membership.user
    records = [
        r
        for r in _student_records(center, student, final_only=False)
        if can(request.user, Action.VIEW_RECORDS, r)
    ]
    record(
        AuditAction.HISTORY_VIEWED,
        request=request,
        center=center,
        target=membership,
        student=str(student.pk),
        where="student history",
        records=[str(r.pk) for r in records],
    )
    projects = WritingProject.objects.filter(center=center, student=student)
    context = {
        "center": center,
        "membership": membership,
        "student": student,
        "groups": by_project(records, projects),
        "can_export": can(request.user, Action.EXPORT_STUDENT_RECORD, membership),
        "can_record_disclosures": can(request.user, Action.RECORD_DISCLOSURES, membership),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/student_history.html", context)


@login_required
def my_writing(request: HttpRequest, slug: str) -> HttpResponse:
    """A student's own sessions: summaries, next steps and resources, grouped by project."""
    center = center_for(request, Action.VIEW_OWN_WRITING, slug)
    student = signed_in(request)
    records = _student_records(center, student, final_only=True)
    for rec in records:
        rec.student_answers = report.for_student(rec)  # type: ignore[attr-defined]
    projects = WritingProject.objects.filter(center=center, student=student)
    context = {
        "center": center,
        "groups": by_project(records, projects),
        "show_notes": center.students_see_staff_notes,
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/my_writing.html", context)


@login_required
def my_record(request: HttpRequest, slug: str, record_id: UUID) -> HttpResponse:
    """One of the student's own sessions. The summary email links here."""
    rec = record_for(request, Action.VIEW_OWN_WRITING, slug, record_id)
    rec.student_answers = report.for_student(rec)  # type: ignore[attr-defined]
    center = rec.center
    context = {
        "center": center,
        "rec": rec,
        "attachments": rec.attachments.select_related("resource"),
        "show_notes": center.students_see_staff_notes,
        "crumbs": _crumbs(
            center, (_("My writing"), reverse("records:my_writing", kwargs={"slug": slug}))
        ),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/my_record.html", context)


# --- FERPA export ---------------------------------------------------------------


def _audit_export(request: HttpRequest, membership: Membership, fmt: str) -> None:
    record(
        AuditAction.STUDENT_RECORD_EXPORTED,
        request=request,
        center=membership.center,
        target=membership,
        student=str(membership.user_id),
        format=fmt,
    )


@login_required
def export_page(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    """The student's complete record as a page to print or save as PDF."""
    membership = student_for(request, Action.EXPORT_STUDENT_RECORD, slug, membership_id)
    _audit_export(request, membership, "page")
    center = membership.center
    student = membership.user
    context = {
        "center": center,
        "membership": membership,
        "student": student,
        "exported_at": timezone.now(),
        "projects": WritingProject.objects.filter(center=center, student=student),
        "participations": export.participations(membership),
        "intake": _intake_rows(membership),
        "registration": [form_display.show(r) for r in form_export.registration(membership)],
        "records": export.records(membership),
        "blocks": Block.objects.filter(center=center, student=student)
        .select_related("schedule", "term", "lifted_by")
        .order_by("created_at"),
        "disclosures": export.disclosures(membership),
        "crumbs": _crumbs(
            membership.center,
            (
                membership.user.display_name,
                reverse(
                    "records:student_history",
                    kwargs={"slug": slug, "membership_id": membership.pk},
                ),
            ),
        ),
    }
    with timezone.override(membership.center.time_zone):
        return render(request, "records/export.html", context)


def _intake_rows(membership: Membership) -> list[dict[str, Any]]:
    """Each appointment that has intake answers, with every set given, oldest first."""
    found = form_export.intake_by_appointment(membership)
    return [
        {
            "appointment": p.appointment,
            "responses": [form_display.show(r) for r in found[p.appointment_id]],
        }
        for p in export.participations(membership)
        if p.appointment_id in found
    ]


@login_required
def export_json(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    membership = student_for(request, Action.EXPORT_STUDENT_RECORD, slug, membership_id)
    _audit_export(request, membership, "json")
    data = export.build(membership, exported_by=signed_in(request))
    body = json.dumps(data, indent=2, ensure_ascii=False)
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="student-record.json"'
    return response


# --- Projects, resources and settings ---------------------------------------------


@login_required
def project_edit(request: HttpRequest, slug: str, project_id: UUID) -> HttpResponse:
    project = get_for_action(
        request.user,
        Action.WRITE_RECORDS,
        WritingProject.objects.select_related("center", "student").filter(center__slug=slug),
        pk=project_id,
    )
    center = project.center
    membership = membership_of(center, project.student)
    back = (
        reverse("records:student_history", kwargs={"slug": slug, "membership_id": membership.pk})
        if membership is not None and can(request.user, Action.VIEW_RECORDS, membership)
        else center.get_absolute_url()
    )
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        record(
            AuditAction.PROJECT_SAVED,
            request=request,
            center=center,
            target=project,
            student=str(project.student_id),
            fields=sorted(form.changed_data),
        )
        messages.success(request, _("Project saved."))
        return redirect(back)
    context = {
        "center": center,
        "form": form,
        "title": _("Edit a writing project"),
        "intro": _("%(name)s's project. The student sees its title, course and due date.")
        % {"name": project.student.display_name},
        "crumbs": _crumbs(center),
        "submit": _("Save project"),
        "back": back,
    }
    return render(request, "scheduling/form_page.html", context)


@login_required
def resource_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_RECORDS, slug)
    own = Resource.objects.filter(center=center).prefetch_related("topics")
    shared = Resource.objects.filter(center__isnull=True, is_active=True)
    context = {
        "center": center,
        "resources": own,
        "shared_count": shared.count(),
        "publisher_count": shared.values("publisher").distinct().count(),
        "crumbs": _crumbs(center),
    }
    return render(request, "records/resource_list.html", context)


def _resource_page(
    request: HttpRequest, center: Center, resource: Resource, *, title: str
) -> HttpResponse:
    form = ResourceForm(request.POST or None, instance=resource, center=center)
    if request.method == "POST" and form.is_valid():
        adding = resource._state.adding
        saved = form.save()
        record(
            AuditAction.RESOURCE_SAVED,
            request=request,
            center=center,
            target=saved,
            created=adding,
        )
        messages.success(request, _("Resource saved. Tutors can attach it now."))
        return redirect("records:resources", slug=center.slug)
    context = {
        "center": center,
        "form": form,
        "title": title,
        "axes": topic_axes(topic_choices(), form.topic_slugs()),
        "crumbs": _crumbs(
            center, (_("Resources"), reverse("records:resources", kwargs={"slug": center.slug}))
        ),
    }
    return render(request, "records/resource_form.html", context)


@login_required
def resource_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_RECORDS, slug)
    resource = Resource(center=center, created_by=signed_in(request), link_check="manual")
    return _resource_page(request, center, resource, title=_("Add a link"))


@login_required
def resource_edit(request: HttpRequest, slug: str, resource_id: UUID) -> HttpResponse:
    resource = get_for_action(
        request.user,
        Action.MANAGE_RECORDS,
        Resource.objects.select_related("center").filter(center__slug=slug),
        pk=resource_id,
    )
    return _resource_page(request, cast(Center, resource.center), resource, title=_("Edit a link"))


@login_required
def resource_links(request: HttpRequest, slug: str) -> HttpResponse:
    """The links that moved or stopped working, for the center's own and the shared catalog."""
    center = center_for(request, Action.MANAGE_RECORDS, slug)
    problem = [LinkState.MOVED, LinkState.UNREACHABLE, LinkState.BROKEN]
    own = Resource.objects.filter(center=center, link_state__in=problem).order_by("title")
    shared = Resource.objects.filter(
        center__isnull=True, is_active=True, link_state__in=problem
    ).order_by("title")
    context = {
        "center": center,
        "enabled": settings.SYMPRAXI_LINK_CHECK,
        "last_run": LinkCheckRun.objects.filter(finished_at__isnull=False).first(),
        "own": own,
        "shared": shared,
        "unchecked": Resource.objects.available_to(center)
        .filter(link_state=LinkState.UNCHECKED, link_check="auto")
        .count(),
        "crumbs": _crumbs(
            center, (_("Resources"), reverse("records:resources", kwargs={"slug": center.slug}))
        ),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/resource_links.html", context)


@login_required
def resource_check(request: HttpRequest, slug: str, resource_id: UUID) -> HttpResponse:
    """Check one of the center's own links now, for a director who just fixed it."""
    resource = get_for_action(
        request.user,
        Action.MANAGE_RECORDS,
        Resource.objects.select_related("center").filter(center__slug=slug),
        pk=resource_id,
    )
    center = cast(Center, resource.center)
    back = reverse("records:resource_links", kwargs={"slug": slug})
    if request.method == "POST":
        if not settings.SYMPRAXI_LINK_CHECK:
            messages.error(request, _("The link checker is turned off for this installation."))
            return redirect(back)
        result = linkcheck.check_one(resource)
        record(
            AuditAction.LINK_CHECKED,
            request=request,
            center=center,
            target=resource,
            state=result.state,
        )
        if result.state == LinkState.OK:
            messages.success(request, _("The link works."))
        elif result.state == LinkState.MOVED:
            messages.warning(
                request,
                _("The link works but the page moved to %(url)s.") % {"url": result.moved_to},
            )
        else:
            messages.error(
                request,
                _("The link wasn't confirmed: %(why)s")
                % {"why": result.detail or resource.get_link_state_display()},
            )
        return redirect(back)
    context = {
        "center": center,
        "resource": resource,
        "enabled": settings.SYMPRAXI_LINK_CHECK,
        "back": back,
        "crumbs": _crumbs(
            center,
            (_("Resources"), reverse("records:resources", kwargs={"slug": slug})),
            (_("Links to fix"), back),
        ),
    }
    return render(request, "records/resource_check.html", context)


@login_required
def record_settings(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_RECORDS, slug)
    form = RecordSettingsForm(request.POST or None, instance=center)
    if request.method == "POST" and form.is_valid():
        form.save()
        record(
            AuditAction.RECORD_SETTINGS_CHANGED,
            request=request,
            center=center,
            target=center,
            **{name: form.cleaned_data[name] for name in form.changed_data},
        )
        messages.success(request, _("Settings saved."))
        return redirect(center.get_absolute_url())
    context = {
        "center": center,
        "form": form,
        "title": _("Session record settings"),
        "intro": _(
            "Students always see the summary, next steps and resources from each session. "
            "These settings decide whether they see more, and when tutors are reminded "
            "about records they haven't written."
        ),
        "crumbs": _crumbs(center),
        "submit": _("Save settings"),
        "back": center.get_absolute_url(),
    }
    return render(request, "scheduling/form_page.html", context)
