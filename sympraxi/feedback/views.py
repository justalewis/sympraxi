"""Pages for the written-feedback queue.

Students send a draft and follow it. Tutors and directors see the waiting
submissions without the student's name, claim one, and then see the student
and the draft. The feedback itself is written on the usual session-record page.
"""

from datetime import timedelta
from typing import IO
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.feedback import services
from sympraxi.feedback.forms import (
    AssignForm,
    FileForm,
    QueueSettingsForm,
    RangeForm,
    SubmitForm,
)
from sympraxi.feedback.models import FileRole, Submission, SubmissionFile, SubmissionStatus
from sympraxi.files import checks
from sympraxi.files import services as file_services
from sympraxi.files import views as file_views
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import notifications as record_notifications
from sympraxi.scheduling.views import center_for, signed_in


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [(center.name, reverse("centers:detail", kwargs={"slug": center.slug})), *extra]


def _queue_crumb(center: Center) -> tuple[str, str]:
    return (_("Written feedback"), reverse("feedback:queue", kwargs={"slug": center.slug}))


def _mine_crumb(center: Center) -> tuple[str, str]:
    return (_("Your submissions"), reverse("feedback:mine", kwargs={"slug": center.slug}))


def submission_for(
    request: HttpRequest, action: Action, slug: str, submission_id: UUID
) -> Submission:
    return get_for_action(
        request.user,
        action,
        Submission.objects.select_related("center", "student", "tutor", "record").filter(
            center__slug=slug
        ),
        pk=submission_id,
    )


def _back(submission: Submission) -> str:
    return reverse(
        "feedback:detail", kwargs={"slug": submission.center.slug, "submission_id": submission.pk}
    )


def _notice(request: HttpRequest, submission: Submission, message: str) -> HttpResponse:
    """A page that says why not, for someone who may see the submission but not do this."""
    context = {
        "center": submission.center,
        "submission": submission,
        "message": message,
        "crumbs": _crumbs(submission.center, (submission.title, _back(submission))),
    }
    return render(request, "feedback/notice.html", context)


def _upload_of(form: FileForm | SubmitForm) -> tuple[IO[bytes], str, int]:
    upload = form.cleaned_data["file"]
    return upload.file, upload.name, upload.size


# --- Students -----------------------------------------------------------------------


@login_required
def submit(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.SUBMIT_WORK, slug)
    user = signed_in(request)
    crumbs = _crumbs(center, _mine_crumb(center))
    if not services.is_open(center):
        return render(
            request,
            "feedback/closed.html",
            {"center": center, "crumbs": _crumbs(center)},
        )
    with timezone.override(center.time_zone):
        form = SubmitForm(request.POST or None, request.FILES or None, center=center)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                submission = services.send(
                    student=user,
                    center=center,
                    title=data["title"],
                    course=data["course"],
                    wanted=data["wanted"],
                    day=data["day"],
                    upload=_upload_of(form),
                    request=request,
                )
            except checks.Refused as refused:
                form.add_error("file", str(refused))
            except services.QueueError as error:
                form.add_error(None, error.message)
            else:
                messages.success(request, _("Your draft is in the queue."))
                return redirect(_back(submission))
        context = {
            "center": center,
            "form": form,
            "title": _("Send a draft for written feedback"),
            "intro": _(
                "A tutor reads your draft and writes back, so you don't need to book a time. "
                "Say what you want help with and the day you need it by."
            ),
            "crumbs": crumbs,
            "submit": _("Send it"),
            "back": reverse("feedback:mine", kwargs={"slug": slug}),
            "multipart": True,
        }
        return render(request, "scheduling/form_page.html", context)


@login_required
def mine(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.SUBMIT_WORK, slug)
    submissions = Submission.objects.filter(
        center=center, student=signed_in(request)
    ).select_related("tutor")
    context = {
        "center": center,
        "submissions": submissions.order_by("-created_at"),
        "is_open": services.is_open(center),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "feedback/mine.html", context)


# --- One submission ------------------------------------------------------------------


@login_required
def detail(request: HttpRequest, slug: str, submission_id: UUID) -> HttpResponse:
    submission = submission_for(request, Action.VIEW_SUBMISSION, slug, submission_id)
    center = submission.center
    user = signed_in(request)
    is_student = submission.student_id == user.pk
    is_tutor = submission.tutor_id == user.pk
    returned = submission.status == SubmissionStatus.RETURNED
    drafts = services.files_of(submission, role=FileRole.DRAFT)
    sent_back = (
        services.files_of(submission, role=FileRole.RETURNED)
        if (returned or not is_student)
        else []
    )
    rec = submission.record
    feedback_url = ""
    if rec is not None:
        if is_student and returned:
            feedback_url = reverse("records:my_record", kwargs={"slug": slug, "record_id": rec.pk})
        elif is_tutor and not rec.is_final:
            feedback_url = reverse("records:edit", kwargs={"slug": slug, "record_id": rec.pk})
        elif can(user, Action.VIEW_RECORDS, rec):
            feedback_url = reverse("records:detail", kwargs={"slug": slug, "record_id": rec.pk})
    limit = services.settings_for(center).max_files
    context = {
        "center": center,
        "submission": submission,
        "is_student": is_student,
        "is_tutor": is_tutor,
        "drafts": drafts,
        "sent_back": sent_back,
        "feedback_url": feedback_url,
        "may_add_draft": is_student and submission.is_open and len(drafts) < limit,
        "may_send_back": is_tutor and submission.status == SubmissionStatus.CLAIMED,
        "may_cancel": submission.is_open
        and (
            (is_student and submission.status == SubmissionStatus.WAITING)
            or can(user, Action.MANAGE_QUEUE, center)
        ),
        "may_assign": submission.is_open and can(user, Action.MANAGE_QUEUE, center),
        "may_remove": submission.is_open,
        "overdue": submission.is_open and submission.due_at < timezone.now(),
        "crumbs": _crumbs(center, _mine_crumb(center) if is_student else _queue_crumb(center)),
    }
    with timezone.override(center.time_zone):
        return render(request, "feedback/detail.html", context)


@login_required
def cancel(request: HttpRequest, slug: str, submission_id: UUID) -> HttpResponse:
    submission = submission_for(request, Action.VIEW_SUBMISSION, slug, submission_id)
    user = signed_in(request)
    is_student = submission.student_id == user.pk
    if not is_student and not can(user, Action.MANAGE_QUEUE, submission.center):
        return _notice(
            request,
            submission,
            _("Only the student, or a director, can cancel a submission."),
        )
    if request.method == "POST":
        try:
            services.cancel(submission.pk, by=user, request=request)
        except services.QueueError as error:
            messages.error(request, error.message)
            return redirect(_back(submission))
        messages.success(request, _("The submission is canceled."))
        target = "feedback:mine" if is_student else "feedback:queue"
        return redirect(reverse(target, kwargs={"slug": slug}))
    context = {
        "center": submission.center,
        "submission": submission,
        "crumbs": _crumbs(submission.center, _mine_crumb(submission.center))
        if is_student
        else _crumbs(submission.center, _queue_crumb(submission.center)),
    }
    return render(request, "feedback/cancel.html", context)


# --- Files -----------------------------------------------------------------------------


@login_required
def add_file(request: HttpRequest, slug: str, submission_id: UUID) -> HttpResponse:
    submission = submission_for(request, Action.VIEW_SUBMISSION, slug, submission_id)
    user = signed_in(request)
    center = submission.center
    if submission.student_id == user.pk:
        role = FileRole.DRAFT
    elif submission.tutor_id == user.pk and submission.status == SubmissionStatus.CLAIMED:
        role = FileRole.RETURNED
    else:
        return _notice(
            request,
            submission,
            _("Files are added by the student, and by the tutor who has the submission."),
        )
    form = FileForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.add_file(
                submission,
                by=user,
                role=role,
                upload=_upload_of(form),
                request=request,
            )
        except checks.Refused as refused:
            form.add_error("file", str(refused))
        except services.QueueError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, _("The file is attached."))
            return redirect(_back(submission))
    title = _("Add another draft") if role == FileRole.DRAFT else _("Send a commented file back")
    context = {
        "center": center,
        "form": form,
        "title": title,
        "intro": _("The student sees files you send back once you return your feedback.")
        if role == FileRole.RETURNED
        else "",
        "crumbs": _crumbs(
            center, _mine_crumb(center) if role == FileRole.DRAFT else _queue_crumb(center)
        ),
        "submit": _("Attach it"),
        "back": _back(submission),
        "multipart": True,
    }
    return render(request, "scheduling/form_page.html", context)


def _file_for(request: HttpRequest, slug: str, file_id: UUID) -> SubmissionFile:
    attached = get_for_action(
        request.user,
        Action.VIEW_SUBMISSION,
        SubmissionFile.objects.filter(submission__center__slug=slug).select_related(
            "file", "submission__center", "submission__student", "submission__tutor"
        ),
        pk=file_id,
    )
    # What a tutor sends back stays hidden from the student until it is returned.
    if (
        attached.role == FileRole.RETURNED
        and attached.submission.student_id == request.user.pk
        and attached.submission.status != SubmissionStatus.RETURNED
    ):
        raise Http404
    return attached


@login_required
def download(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponseBase:
    attached = _file_for(request, slug, file_id)
    return file_views.serve(
        request,
        attached.file,
        target=attached,
        back=_back(attached.submission),
        submission=str(attached.submission_id),
    )


@login_required
def remove_file(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponse:
    attached = _file_for(request, slug, file_id)
    user = signed_in(request)
    submission = attached.submission
    may_remove = (
        submission.is_open
        and attached.file.removed_at is None
        and (
            attached.file.uploaded_by_id == user.pk
            or can(user, Action.MANAGE_QUEUE, submission.center)
        )
    )
    back = _back(submission)
    if request.method == "POST" and may_remove:
        file_services.remove(attached, request=request)  # type: ignore[arg-type]
        messages.success(request, _("%(name)s is removed.") % {"name": attached.file.name})
        return redirect(back)
    context = {
        "center": submission.center,
        "attached": attached,
        "may_remove": may_remove,
        "back": back,
        "crumbs": _crumbs(submission.center, (submission.title, back)),
    }
    return render(request, "files/remove.html", context)


# --- Tutors and directors ---------------------------------------------------------------


@login_required
def queue(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.WORK_QUEUE, slug)
    user = signed_in(request)
    is_director = can(user, Action.MANAGE_QUEUE, center)
    open_ones = Submission.objects.filter(center=center).exclude(
        status__in=[SubmissionStatus.RETURNED, SubmissionStatus.CANCELED]
    )
    now = timezone.now()
    waiting_now = open_ones.filter(status=SubmissionStatus.WAITING).order_by("due_at")
    # Tutors see waiting work without the student; only directors get the name.
    waiting = list(waiting_now.select_related("student") if is_director else waiting_now)
    mine_now = list(
        open_ones.filter(status=SubmissionStatus.CLAIMED, tutor=user)
        .select_related("student")
        .order_by("due_at")
    )
    others = (
        list(
            open_ones.filter(status=SubmissionStatus.CLAIMED)
            .exclude(tutor=user)
            .select_related("student", "tutor")
            .order_by("due_at")
        )
        if is_director
        else []
    )
    context = {
        "center": center,
        "settings": services.settings_for(center),
        "waiting": waiting,
        "mine": mine_now,
        "others": others,
        "is_director": is_director,
        "now": now,
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "feedback/queue.html", context)


@login_required
def claim(request: HttpRequest, slug: str, submission_id: UUID) -> HttpResponse:
    center = center_for(request, Action.WORK_QUEUE, slug)
    submission = get_for_action(
        request.user,
        Action.WORK_QUEUE,
        Submission.objects.select_related("center").filter(center=center),
        pk=submission_id,
    )
    queue_url = reverse("feedback:queue", kwargs={"slug": slug})
    if request.method == "POST":
        try:
            services.claim(submission.pk, signed_in(request), request=request)
        except services.QueueError as error:
            messages.error(request, error.message)
            return redirect(queue_url)
        messages.success(request, _("It's yours. Read the draft, then write your feedback."))
        return redirect(_back(submission))
    context = {
        "center": center,
        "submission": submission,
        "queue_url": queue_url,
        "crumbs": _crumbs(center, _queue_crumb(center)),
    }
    with timezone.override(center.time_zone):
        return render(request, "feedback/claim.html", context)


@login_required
def assign(request: HttpRequest, slug: str, submission_id: UUID) -> HttpResponse:
    center = center_for(request, Action.MANAGE_QUEUE, slug)
    submission = submission_for(request, Action.VIEW_SUBMISSION, slug, submission_id)
    people = list(
        User.objects.filter(
            memberships__center=center,
            memberships__role__in=[Role.TUTOR, Role.DIRECTOR],
            memberships__is_active=True,
            is_active=True,
        )
        .distinct()
        .order_by("full_name", "email")
    )
    form = AssignForm(request.POST or None, people=people)
    if request.method == "POST" and form.is_valid():
        try:
            services.assign(
                submission.pk,
                form.chosen(),
                by=signed_in(request),
                request=request,
                base_url=record_notifications.base_url(request),
            )
        except services.QueueError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, _("The submission is assigned."))
            return redirect(_back(submission))
    context = {
        "center": center,
        "form": form,
        "title": _("Give this submission to a tutor"),
        "intro": _(
            "The tutor is emailed. If someone already has it, their unfinished "
            "feedback goes with it."
        ),
        "crumbs": _crumbs(center, _queue_crumb(center), (submission.title, _back(submission))),
        "submit": _("Assign it"),
        "back": _back(submission),
    }
    return render(request, "scheduling/form_page.html", context)


@login_required
def queue_settings(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_QUEUE, slug)
    current = services.settings_for(center)
    form = QueueSettingsForm(request.POST or None, instance=current)
    if request.method == "POST" and form.is_valid():
        changed = sorted(form.changed_data)
        form.save()
        if changed:
            record(
                AuditAction.QUEUE_SETTINGS_CHANGED,
                request=request,
                center=center,
                target=current,
                changed=changed,
            )
        messages.success(request, _("The queue's settings are saved."))
        return redirect(reverse("feedback:queue", kwargs={"slug": slug}))
    context = {
        "center": center,
        "form": form,
        "title": _("Written feedback settings"),
        "intro": "",
        "crumbs": _crumbs(center, _queue_crumb(center)),
        "submit": _("Save"),
        "back": reverse("feedback:queue", kwargs={"slug": slug}),
    }
    return render(request, "scheduling/form_page.html", context)


@login_required
def turnaround(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_QUEUE, slug)
    today = services.today(center)
    initial = {"start": today - timedelta(days=90), "end": today}
    form = RangeForm(request.GET or None, initial=initial)
    if request.GET and form.is_valid():
        start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    else:
        start, end = initial["start"], initial["end"]
    stats = services.turnaround(center, start, end)
    context = {
        "center": center,
        "form": form,
        "stats": stats,
        "start": start,
        "end": end,
        "crumbs": _crumbs(center, _queue_crumb(center)),
    }
    with timezone.override(center.time_zone):
        return render(request, "feedback/turnaround.html", context)
