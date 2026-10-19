"""The comment viewer: a student's draft drawn in the browser, with a tutor's comments.

The PDF is fetched by the page's script and drawn on canvases by PDF.js, so the
browser never opens the file itself: it is sent as a download that no browser
runs, like every file here. A Word or similar file is shown through the PDF the
worker made from it. A tutor comments while they have the submission; the
student sees the comments once the feedback is returned, and directors see them
at any time.
"""

import json
from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.core.policy import Action, can
from sympraxi.feedback.models import Comment, FileRole, SubmissionFile, SubmissionStatus
from sympraxi.feedback.views import _back, _crumbs, _file_for, _queue_crumb, signed_in
from sympraxi.files import services as file_services
from sympraxi.files.models import CONVERTIBLE, FileKind, PdfStatus
from sympraxi.privacy.audit import AuditAction, record

MAX_COMMENTS = 500
MAX_RECTS = 50


def may_comment(user: User, attached: SubmissionFile) -> bool:
    """The tutor who has the submission comments on the student's drafts, until it's returned."""
    submission = attached.submission
    return (
        attached.role == FileRole.DRAFT
        and submission.status == SubmissionStatus.CLAIMED
        and submission.tutor_id == user.pk
    )


def sees_comments(user: User, attached: SubmissionFile) -> bool:
    submission = attached.submission
    if submission.student_id == user.pk:
        return submission.status == SubmissionStatus.RETURNED
    return can(user, Action.VIEW_SUBMISSION, attached)


def comment_json(comment: Comment, user: User) -> dict[str, Any]:
    return {
        "id": str(comment.pk),
        "page": comment.page,
        "quote": comment.quote,
        "rects": comment.rects,
        "body": comment.body,
        "author": comment.author.display_name,
        "mine": comment.author_id == user.pk,
    }


def _unavailable_reason(attached: SubmissionFile) -> str:
    stored = attached.file
    if stored.removed_at is not None:
        return _("This file was removed.")
    if not stored.is_ready:
        return _("This file is still being checked for viruses. Try again in a minute.")
    if stored.kind == FileKind.PDF:
        return ""
    if stored.kind in CONVERTIBLE and stored.pdf_status == PdfStatus.PENDING:
        return _("A viewable copy of this file is being made. Try again in a minute.")
    if stored.kind in CONVERTIBLE and stored.pdf_status == PdfStatus.FAILED:
        return _("A viewable copy of this file couldn't be made.")
    return _("Only PDF files can be shown here.")


@login_required
def viewer(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponse:
    attached = _file_for(request, slug, file_id)
    user = signed_in(request)
    submission = attached.submission
    back = _back(submission)
    download = reverse("feedback:download", kwargs={"slug": slug, "file_id": attached.pk})
    context: dict[str, Any] = {
        "center": submission.center,
        "attached": attached,
        "stored": attached.file,
        "back": back,
        "download": download,
        "crumbs": _crumbs(submission.center, _queue_crumb(submission.center))
        if submission.student_id != user.pk
        else _crumbs(submission.center),
    }
    reason = _unavailable_reason(attached) or (
        "" if attached.file.viewable_name else _("Only PDF files can be shown here.")
    )
    if reason:
        context["reason"] = reason
        return render(request, "feedback/viewer_unavailable.html", context)
    record(
        AuditAction.FILE_VIEWED,
        request=request,
        center=submission.center,
        target=attached,
        student=str(submission.student_id),
        submission=str(submission.pk),
    )
    comments = (
        [
            comment_json(c, user)
            for c in attached.comments.select_related("author").order_by("page", "created_at")
        ]
        if sees_comments(user, attached)
        else []
    )
    context["config"] = {
        "library": static("vendor/pdfjs/pdf.min.mjs"),
        "worker": static("vendor/pdfjs/pdf.worker.min.mjs"),
        "pdf": reverse("feedback:pdf", kwargs={"slug": slug, "file_id": attached.pk}),
        "commentsUrl": reverse("feedback:comments", kwargs={"slug": slug, "file_id": attached.pk}),
        "csrf": get_token(request),
        "canComment": may_comment(user, attached),
        "comments": comments,
        "labels": {
            "page": _("Page {n} of {total}"),
            "meta": _("Page {page}, {author}"),
            "show": _("Go to page"),
            "remove": _("Remove"),
            "saved": _("Your comment is saved."),
            "removed": _("The comment is removed."),
            "error": _("The comment couldn't be saved."),
            "badPage": _("Choose a page from 1 to {total}."),
            "failed": _("This file can't be shown here. You can download it instead."),
        },
    }
    context["can_comment"] = context["config"]["canComment"]
    context["shows_comments"] = sees_comments(user, attached)
    context["script"] = static("sympraxi/pdf-comments.js")
    return render(request, "feedback/viewer.html", context)


@login_required
def pdf(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponseBase:
    """The PDF to draw: the file itself, or the copy the worker made."""
    attached = _file_for(request, slug, file_id)
    name = attached.file.viewable_name
    if not name:
        raise Http404
    response = FileResponse(
        file_services.storage().open(name, "rb"),
        as_attachment=True,
        filename=attached.file.name.rsplit(".", 1)[0] + ".pdf",
        content_type="application/pdf",
    )
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    return response


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _parse(request: HttpRequest) -> dict[str, Any] | None:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _rects(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) > MAX_RECTS:
        return None
    clean = []
    for rect in value:
        if (
            not isinstance(rect, list)
            or len(rect) != 4
            or not all(isinstance(n, int | float) and not isinstance(n, bool) for n in rect)
        ):
            return None
        clean.append([round(min(1.0, max(0.0, float(n))), 5) for n in rect])
    return clean


@login_required
def comments(request: HttpRequest, slug: str, file_id: UUID) -> HttpResponse:
    """GET lists the comments the person may see; POST adds one; DELETE removes their own."""
    attached = _file_for(request, slug, file_id)
    user = signed_in(request)
    center = attached.submission.center
    if request.method == "GET":
        found = attached.comments.select_related("author") if sees_comments(user, attached) else []
        return JsonResponse({"comments": [comment_json(c, user) for c in found]})
    if not may_comment(user, attached):
        return _error(_("You can comment while the submission is yours."), status=403)
    if request.method == "POST":
        data = _parse(request)
        if data is None:
            return _error(_("That wasn't understood."))
        body = data.get("body")
        quote = data.get("quote", "")
        page = data.get("page")
        rects = _rects(data.get("rects", []))
        if not isinstance(body, str) or not body.strip():
            return _error(_("Write a comment first."))
        if len(body) > 2000:
            return _error(_("A comment can be 2,000 characters."))
        if not isinstance(quote, str) or rects is None:
            return _error(_("That wasn't understood."))
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 10000:
            return _error(_("Choose a page."))
        if attached.comments.count() >= MAX_COMMENTS:
            return _error(_("This file has as many comments as it can hold."))
        made = Comment.objects.create(
            submission_file=attached,
            author=user,
            page=page,
            quote=quote.strip()[:500],
            rects=rects,
            body=body.strip(),
        )
        record(
            AuditAction.COMMENT_ADDED,
            request=request,
            center=center,
            target=made,
            student=str(attached.student_id),
            submission=str(attached.submission_id),
        )
        return JsonResponse(comment_json(made, user), status=201)
    if request.method == "DELETE":
        target = attached.comments.filter(pk=request.GET.get("comment", ""), author=user).first()
        if target is None:
            return _error(_("There is no such comment of yours."), status=404)
        record(
            AuditAction.COMMENT_REMOVED,
            request=request,
            center=center,
            target=target,
            student=str(attached.student_id),
            submission=str(attached.submission_id),
        )
        target.delete()
        return JsonResponse({}, status=200)
    return HttpResponse(status=405)
