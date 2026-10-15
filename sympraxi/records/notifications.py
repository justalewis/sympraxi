"""The email a student gets when their session summary is ready.

It says a summary is ready and links to it (build plan §6.1 item 6). The
summary and next steps go in the email only when the center turns on
`summary_email_includes_text`. Staff notes never go in an email.

`services.finalize` calls `send_summary_ready()` after the transaction commits,
so a record that is rolled back sends nothing.
"""

from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.records.models import SessionRecord
from sympraxi.scheduling.notifications import base_url

__all__ = ["base_url", "record_url", "send_summary_ready", "summary_message"]


def record_url(rec: SessionRecord, url_base: str) -> str:
    path = reverse("records:my_record", kwargs={"slug": rec.center.slug, "record_id": rec.pk})
    return url_base.rstrip("/") + path


def summary_message(rec: SessionRecord, *, url_base: str) -> EmailMessage:
    center = rec.center
    context = {
        "record": rec,
        "center": center,
        "student": rec.student,
        "url": record_url(rec, url_base),
        "include_text": center.summary_email_includes_text,
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
    }
    with timezone.override(center.time_zone):
        body = render_to_string("records/email/summary_ready.txt", context)
    subject = _("Your session summary is ready")
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[rec.student.email],
        reply_to=[center.contact_email] if center.contact_email else [],
    )


def send_summary_ready(record_id: UUID, *, base_url: str = "") -> int:
    rec = SessionRecord.objects.select_related("center", "student", "tutor").get(pk=record_id)
    if not rec.is_final or not rec.student.is_active:
        return 0
    return mailers.default.send_messages([summary_message(rec, url_base=base_url)]) or 0
