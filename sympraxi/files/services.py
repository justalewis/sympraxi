"""Storing, scanning, attaching and removing files.

`store()` checks a file (size, type from content, see checks.py), saves it under
a random name in the private `uploads` storage with its SHA-256, and, when the
installation has ClamAV, marks it pending and hands it to the task worker to
scan. Nobody can open a pending file. A clean scan makes it ready; a virus
removes its content at once. If clamd can't be reached the file waits, and the
scheduler asks the worker to try again (`retry_scans`), at most `MAX_ATTEMPTS`
times.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import IO, Any

from django.conf import settings
from django.core.files import File
from django.core.files.storage import Storage, storages
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.files import checks, clamav
from sympraxi.files.models import AppointmentFile, ScanStatus, StoredFile
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Appointment

logger = logging.getLogger("sympraxi.files")

MAX_ATTEMPTS = 5
# How long a scan may wait before the scheduler asks again.
RETRY_AFTER = timedelta(minutes=5)


def storage() -> Storage:
    return storages["uploads"]


def max_bytes() -> int:
    return int(settings.SYMPRAXI_UPLOAD_MAX_MB) * 1024 * 1024


def scanning() -> bool:
    return bool(settings.SYMPRAXI_CLAMAV_ADDRESS)


def store(
    handle: IO[bytes], name: str, size: int, *, center: Center, uploaded_by: User
) -> StoredFile:
    """Check and keep one upload. Raises checks.Refused with the reason."""
    if size > max_bytes():
        raise checks.Refused(
            _("The file is larger than %(n)s MB.") % {"n": settings.SYMPRAXI_UPLOAD_MAX_MB}
        )
    display = checks.clean_name(name)
    kind = checks.identify(display, handle)
    digest = hashlib.sha256()
    handle.seek(0)
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    handle.seek(0)
    stored_name = storage().save(f"{center.pk}/{uuid.uuid4().hex}", File(handle))
    status = ScanStatus.PENDING if scanning() else ScanStatus.NOT_SCANNED
    stored = StoredFile.objects.create(
        center=center,
        uploaded_by=uploaded_by,
        name=display,
        kind=kind,
        size=size,
        sha256=digest.hexdigest(),
        storage_name=stored_name,
        scan_status=status,
    )
    if status == ScanStatus.PENDING:
        file_id = str(stored.pk)
        transaction.on_commit(lambda: _enqueue_scan(file_id))
    return stored


def _enqueue_scan(file_id: str) -> None:
    from sympraxi.files.tasks import scan_file

    scan_file.enqueue(file_id)


def scan(file_id: Any) -> str:
    """Scan one pending file. Returns its status afterwards."""
    with transaction.atomic():
        stored = (
            StoredFile.objects.select_for_update(skip_locked=True)
            .filter(pk=file_id, scan_status__in=[ScanStatus.PENDING, ScanStatus.ERROR])
            .first()
        )
        if stored is None:
            return ""
        stored.scan_attempts += 1
        try:
            with storage().open(stored.storage_name, "rb") as handle:
                verdict = clamav.scan(handle, settings.SYMPRAXI_CLAMAV_ADDRESS)
        except clamav.ScannerUnavailable as error:
            stored.scan_status = ScanStatus.ERROR
            stored.scan_detail = str(error)[:200]
            stored.save(update_fields=["scan_status", "scan_detail", "scan_attempts"])
            logger.warning("Couldn't scan file %s: %s", stored.pk, error)
            return stored.scan_status
        stored.scanned_at = timezone.now()
        if verdict.clean:
            stored.scan_status = ScanStatus.CLEAN
            stored.scan_detail = ""
        else:
            stored.scan_status = ScanStatus.INFECTED
            stored.scan_detail = verdict.signature
            storage().delete(stored.storage_name)
            stored.storage_name = ""
            record(
                AuditAction.FILE_INFECTED,
                actor_label="virus scanner",
                center=stored.center,
                target=stored,
                signature=verdict.signature,
            )
        stored.save()
        return stored.scan_status


def retry_scans(now: datetime | None = None) -> int:
    """Ask the worker again about files whose scan stalled or couldn't reach clamd."""
    if not scanning():
        return 0
    now = now or timezone.now()
    stalled = StoredFile.objects.filter(
        scan_status__in=[ScanStatus.PENDING, ScanStatus.ERROR],
        scan_attempts__lt=MAX_ATTEMPTS,
        uploaded_at__lt=now - RETRY_AFTER,
        removed_at__isnull=True,
    ).values_list("pk", flat=True)
    count = 0
    for pk in stalled:
        _enqueue_scan(str(pk))
        count += 1
    return count


def attach(
    appointment: Appointment,
    student: User,
    stored: StoredFile,
    *,
    note: str,
    request: HttpRequest,
) -> AppointmentFile:
    attached = AppointmentFile.objects.create(
        appointment=appointment, student=student, file=stored, note=note.strip()
    )
    record(
        AuditAction.FILE_UPLOADED,
        request=request,
        center=appointment.schedule.center,
        target=attached,
        appointment=str(appointment.pk),
        student=str(student.pk),
        kind=stored.kind,
        size=stored.size,
    )
    return attached


def remove(attached: AppointmentFile, *, request: HttpRequest) -> None:
    """Delete the content; keep the row and its name for the record."""
    with transaction.atomic():
        stored = StoredFile.objects.select_for_update().get(pk=attached.file_id)
        if stored.removed_at is not None:
            return
        if stored.storage_name:
            storage().delete(stored.storage_name)
        stored.storage_name = ""
        stored.removed_at = timezone.now()
        stored.removed_by = request.user  # type: ignore[assignment]
        stored.save(update_fields=["storage_name", "removed_at", "removed_by"])
        record(
            AuditAction.FILE_REMOVED,
            request=request,
            center=attached.center,
            target=attached,
            student=str(attached.student_id),
        )
