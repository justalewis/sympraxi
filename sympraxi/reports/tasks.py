"""The full-center export, built by the task worker (`manage.py db_worker`)."""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.reports import center_export
from sympraxi.reports.models import CenterExport, ExportStatus

logger = logging.getLogger("sympraxi.reports")


@task()
def build_center_export(export_id: str, base_url: str) -> None:
    # Only one worker builds each export: the first to move it out of the queue.
    started = CenterExport.objects.filter(pk=export_id, status=ExportStatus.QUEUED).update(
        status=ExportStatus.BUILDING
    )
    if not started:
        return
    export = CenterExport.objects.select_related("center", "requested_by").get(pk=export_id)
    try:
        center_export.build(export)
    except Exception:
        CenterExport.objects.filter(pk=export_id).update(status=ExportStatus.FAILED)
        logger.exception("The export %s of center %s failed", export_id, export.center_id)
        raise
    export.refresh_from_db()
    director = export.requested_by
    if director is not None and director.is_active:
        ready_message(export, director, base_url=base_url).send()


def ready_message(export: CenterExport, director: User, *, base_url: str) -> EmailMessage:
    """Says the export is ready and links to the page that has it. Nothing else."""
    center = export.center
    url = base_url.rstrip("/") + reverse("reports:center_export", kwargs={"slug": center.slug})
    with timezone.override(center.time_zone):
        body = render_to_string(
            "reports/email/export_ready.txt",
            {
                "center": center,
                "export": export,
                "url": url,
                "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
            },
        )
    subject = _("Your export of %(center)s is ready") % {"center": center.name}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[director.email],
    )
