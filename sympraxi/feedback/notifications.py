"""Emails from the written-feedback queue.

Like every email to a tutor, these name no students and quote no titles: they
say that something needs attention and link to the page, which needs a sign-in.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.feedback.models import Submission, SubmissionStatus


def _link(center: Center, name: str, base_url: str, **kwargs: Any) -> str:
    path = reverse(name, kwargs={"slug": center.slug, **kwargs})
    return base_url.rstrip("/") + path


def _message(
    center: Center, to: User, subject: str, template: str, context: dict[str, Any]
) -> EmailMessage:
    with timezone.override(center.time_zone):
        body = render_to_string(
            template,
            {"center": center, "person": to, "SITE_NAME": settings.SYMPRAXI_SITE_NAME, **context},
        )
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[to.email],
        reply_to=[center.contact_email] if center.contact_email else None,
    )


def send_assigned(submission_id: UUID, *, base_url: str = "") -> int:
    """Tell a tutor a submission is theirs. Sent after the assignment commits."""
    submission = (
        Submission.objects.select_related("center", "tutor")
        .filter(pk=submission_id, status=SubmissionStatus.CLAIMED)
        .first()
    )
    if submission is None or submission.tutor is None or not submission.tutor.is_active:
        return 0
    center = submission.center
    message = _message(
        center,
        submission.tutor,
        _("A written-feedback submission is yours"),
        "feedback/email/assigned.txt",
        {
            "due_at": submission.due_at,
            "url": _link(center, "feedback:detail", base_url, submission_id=submission.pk),
        },
    )
    return message.send()


def send_overdue(center: Center, submissions: list[Submission], *, base_url: str = "") -> int:
    """One email to each tutor who has overdue submissions, one to each director."""
    sent = 0
    mine: dict[Any, list[Submission]] = defaultdict(list)
    for s in submissions:
        if s.tutor_id is not None and s.status == SubmissionStatus.CLAIMED:
            mine[s.tutor_id].append(s)
    for items in mine.values():
        tutor = items[0].tutor
        if tutor is None or not tutor.is_active:
            continue
        subject = ngettext(
            "A written-feedback submission is overdue",
            "%(n)s written-feedback submissions are overdue",
            len(items),
        ) % {"n": len(items)}
        sent += _message(
            center,
            tutor,
            subject,
            "feedback/email/overdue_tutor.txt",
            {"count": len(items), "url": _link(center, "feedback:queue", base_url)},
        ).send()
    directors = User.objects.filter(
        memberships__center=center,
        memberships__role=Role.DIRECTOR,
        memberships__is_active=True,
        is_active=True,
    ).distinct()
    waiting = sum(1 for s in submissions if s.status == SubmissionStatus.WAITING)
    for director in directors:
        subject = ngettext(
            "A written-feedback submission is overdue",
            "%(n)s written-feedback submissions are overdue",
            len(submissions),
        ) % {"n": len(submissions)}
        sent += _message(
            center,
            director,
            subject,
            "feedback/email/overdue_directors.txt",
            {
                "count": len(submissions),
                "waiting": waiting,
                "url": _link(center, "feedback:queue", base_url),
            },
        ).send()
    return sent
