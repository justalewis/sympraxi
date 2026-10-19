"""Attaching, downloading and removing the files on an appointment."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.files import checks, services
from sympraxi.files.display import students_for
from sympraxi.files.models import AppointmentFile, StoredFile
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.views import appointment_crumbs, appointment_for


class UploadForm(forms.Form):
    file = forms.FileField(label=gettext_lazy("File"))
    note = forms.CharField(
        label=gettext_lazy("Note"),
        max_length=300,
        required=False,
        help_text=gettext_lazy("For example: the assignment sheet, or my second draft."),
    )
    student = forms.ChoiceField(label=gettext_lazy("For"))

    def __init__(self, *args: Any, students: list[User], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.students = {str(s.pk): s for s in students}
        field = cast(forms.ChoiceField, self.fields["student"])
        field.choices = [(pk, s.display_name) for pk, s in self.students.items()]
        if len(students) == 1:
            # Only one student it can be for: nothing to choose.
            field.widget = forms.HiddenInput()
            field.initial = next(iter(self.students))
            field.required = False
        self.fields["file"].help_text = _(
            "PDF, Word, OpenDocument, rich text or plain text, up to %(n)s MB."
        ) % {"n": services.max_bytes() // (1024 * 1024)}

    def chosen_student(self) -> User:
        chosen = self.cleaned_data.get("student") or ""
        if len(self.students) == 1:
            return next(iter(self.students.values()))
        return self.students[chosen]


@login_required
def upload(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = appointment_for(request, Action.ATTACH_FILES, slug, appointment_id)
    user = cast(User, request.user)
    students = students_for(user, appointment)
    back = reverse(
        "scheduling:appointment", kwargs={"slug": slug, "appointment_id": appointment_id}
    )
    form = UploadForm(request.POST or None, request.FILES or None, students=students)
    if request.method == "POST" and form.is_valid() and appointment.is_active:
        upload_file = form.cleaned_data["file"]
        student = form.chosen_student()
        try:
            with transaction.atomic():
                stored = services.store(
                    upload_file,
                    upload_file.name,
                    upload_file.size,
                    center=appointment.schedule.center,
                    uploaded_by=user,
                )
                services.attach(
                    appointment, student, stored, note=form.cleaned_data["note"], request=request
                )
        except checks.Refused as refusal:
            form.add_error("file", str(refusal))
        else:
            if services.scanning():
                messages.success(
                    request,
                    _("%(name)s is attached. It can be opened once the virus check finishes.")
                    % {"name": stored.name},
                )
            else:
                messages.success(request, _("%(name)s is attached.") % {"name": stored.name})
            return redirect(back + "#files")
    context = {
        "center": appointment.schedule.center,
        "appointment": appointment,
        "form": form,
        "back": back,
        "closed": not appointment.is_active,
        "crumbs": [*appointment_crumbs(user, appointment), (_("Appointment"), back)],
    }
    return render(request, "files/upload.html", context)


def _file_for(request: HttpRequest, slug: str, file_id: UUID) -> AppointmentFile:
    return get_for_action(
        request.user,
        Action.VIEW_FILES,
        AppointmentFile.objects.filter(appointment__schedule__center__slug=slug).select_related(
            "file", "student", "appointment__schedule__center", "appointment__tutor"
        ),
        pk=file_id,
    )


def serve(
    request: HttpRequest, stored: StoredFile, *, target: Any, back: str, **audit: Any
) -> HttpResponseBase:
    """A file as a download, or the page saying why it can't be opened yet."""
    center = target.center
    if not stored.is_ready:
        waiting = render(
            request,
            "files/not_ready.html",
            {"center": center, "stored": stored, "back": back},
            status=409,
        )
        add_never_cache_headers(waiting)
        return waiting
    record(
        AuditAction.FILE_DOWNLOADED,
        request=request,
        center=center,
        target=target,
        student=str(target.student_id),
        **audit,
    )
    handle = services.storage().open(stored.storage_name, "rb")
    response = FileResponse(
        handle,
        as_attachment=True,
        filename=stored.name,
        content_type=checks.CONTENT_TYPES[checks.extension_kind(stored.name)],
    )
    # Never shown in the browser, and never run if it is: a file is someone's work,
    # not a page of this site.
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    add_never_cache_headers(response)
    return response


@login_required
def download(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponseBase:
    attached = _file_for(request, slug, file_id)
    back = reverse(
        "scheduling:appointment",
        kwargs={"slug": slug, "appointment_id": attached.appointment_id},
    )
    return serve(request, attached.file, target=attached, back=back)


@login_required
def remove(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponse:
    attached = _file_for(request, slug, file_id)
    user = request.user
    may_remove = attached.file.uploaded_by_id == user.pk or can(
        user, Action.VIEW_ALL_APPOINTMENTS, attached.center
    )
    back = reverse(
        "scheduling:appointment",
        kwargs={"slug": slug, "appointment_id": attached.appointment_id},
    )
    if request.method == "POST" and may_remove and attached.file.removed_at is None:
        services.remove(attached, request=request)
        messages.success(request, _("%(name)s is removed.") % {"name": attached.file.name})
        return redirect(back + "#files")
    context = {
        "center": attached.center,
        "attached": attached,
        "may_remove": may_remove,
        "back": back,
        "crumbs": [
            *appointment_crumbs(user, attached.appointment),
            (_("Appointment"), back),
        ],
    }
    return render(request, "files/remove.html", context)
