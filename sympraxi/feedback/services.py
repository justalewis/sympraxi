"""Sending, claiming, assigning and returning submissions; the queue's jobs.

Every change to a submission locks its row first, so two tutors claiming the
same one, or a claim and a cancel, can't both win. A submission has no record
until a tutor has it: claiming (or being assigned) starts the session record
the feedback is written in, and finalizing that record returns the submission
(`returned_by_record`, called by the `record_finalized` signal).

A submission is never released back to the queue once claimed: its record is
the tutor's working draft. A director reassigns it, and the draft goes with it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import IO, Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.feedback import notifications
from sympraxi.feedback.models import (
    FileRole,
    QueueSettings,
    Submission,
    SubmissionFile,
    SubmissionStatus,
)
from sympraxi.files import services as file_services
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import services as record_services
from sympraxi.records.models import RecordStatus, SessionRecord

# Minutes a new feedback record starts with; the tutor sets the real figure.
STARTING_MINUTES = 30


class QueueError(Exception):
    """A rule refused the request. `message` is written for the person who asked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def settings_for(center: Center) -> QueueSettings:
    found, _created = QueueSettings.objects.get_or_create(center=center)
    return found


def is_open(center: Center) -> bool:
    return QueueSettings.objects.filter(center=center, is_open=True).exists()


def _zone(center: Center) -> ZoneInfo:
    return ZoneInfo(center.time_zone)


def today(center: Center, *, now: datetime | None = None) -> date:
    """The date at the center right now."""
    return timezone.localtime(now or timezone.now(), _zone(center)).date()


def earliest_day(center: Center, *, now: datetime | None = None) -> date:
    """The soonest day a student may ask for feedback by."""
    return today(center, now=now) + timedelta(days=settings_for(center).lead_days)


def latest_day(center: Center, *, now: datetime | None = None) -> date:
    return today(center, now=now) + timedelta(days=366)


def end_of_day(center: Center, day: date) -> datetime:
    return datetime.combine(day, time(23, 59), tzinfo=_zone(center))


# --- Sending ----------------------------------------------------------------------


def send(
    *,
    student: User,
    center: Center,
    title: str,
    course: str,
    wanted: str,
    day: date,
    upload: tuple[IO[bytes], str, int],
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> Submission:
    """A student's request for feedback, with its first file.

    Raises QueueError, or files.checks.Refused if the file isn't acceptable.
    """
    now = now or timezone.now()
    if not is_open(center):
        raise QueueError("closed", _("This center isn't taking submissions right now."))
    if not earliest_day(center, now=now) <= day <= latest_day(center, now=now):
        raise QueueError(
            "date",
            _("Choose a day at least %(n)s days from now, and within a year.")
            % {"n": settings_for(center).lead_days},
        )
    handle, name, size = upload
    with transaction.atomic():
        stored = file_services.store(handle, name, size, center=center, uploaded_by=student)
        submission = Submission.objects.create(
            center=center,
            student=student,
            title=title.strip(),
            course=course.strip(),
            wanted=wanted.strip(),
            due_at=end_of_day(center, day),
        )
        SubmissionFile.objects.create(submission=submission, file=stored, role=FileRole.DRAFT)
        record(
            AuditAction.SUBMISSION_SENT,
            request=request,
            actor=student,
            center=center,
            target=submission,
            student=str(student.pk),
        )
        record(
            AuditAction.FILE_UPLOADED,
            request=request,
            actor=student,
            center=center,
            target=stored,
            student=str(student.pk),
            submission=str(submission.pk),
        )
    return submission


def draft_count(submission: Submission) -> int:
    return submission.files.filter(role=FileRole.DRAFT, file__removed_at__isnull=True).count()


def add_file(
    submission: Submission,
    *,
    by: User,
    role: str,
    upload: tuple[IO[bytes], str, int],
    request: HttpRequest | None = None,
) -> SubmissionFile:
    """Another draft from the student, or a commented copy from the tutor."""
    handle, name, size = upload
    with transaction.atomic():
        locked = (
            Submission.objects.select_for_update().select_related("center").get(pk=submission.pk)
        )
        if role == FileRole.DRAFT:
            limit = settings_for(locked.center).max_files
            if not locked.is_open:
                raise QueueError(
                    "closed", _("This submission is closed, so it takes no more drafts.")
                )
            if draft_count(locked) >= limit:
                raise QueueError(
                    "too_many",
                    _("A submission can have %(n)s files. Remove one to add another.")
                    % {"n": limit},
                )
        elif locked.status != SubmissionStatus.CLAIMED:
            raise QueueError(
                "not_claimed", _("Files go back to the student while you're working on it.")
            )
        stored = file_services.store(handle, name, size, center=locked.center, uploaded_by=by)
        attached = SubmissionFile.objects.create(submission=locked, file=stored, role=role)
        record(
            AuditAction.FILE_UPLOADED,
            request=request,
            actor=by,
            center=locked.center,
            target=stored,
            student=str(locked.student_id),
            submission=str(locked.pk),
        )
    return attached


# --- Claiming and assigning -------------------------------------------------------


def _start_record(submission: Submission, tutor: User, now: datetime) -> SessionRecord:
    """The record the feedback is written in; reused if there is one already."""
    rec = submission.record
    if rec is None:
        return record_services.start_standalone(
            actor=tutor,
            center=submission.center,
            student=submission.student,
            held_at=now,
            minutes=STARTING_MINUTES,
        )
    if rec.tutor_id != tutor.pk:
        # A draft changes hands with the submission.
        SessionRecord.objects.filter(pk=rec.pk, status=RecordStatus.DRAFT).update(tutor=tutor)
        rec.tutor = tutor
    return rec


def _take(
    submission: Submission,
    tutor: User,
    *,
    action: AuditAction,
    request: HttpRequest | None,
    actor: User | None,
    now: datetime,
) -> None:
    submission.record = _start_record(submission, tutor, now)
    submission.tutor = tutor
    submission.status = SubmissionStatus.CLAIMED
    submission.claimed_at = submission.claimed_at or now
    submission.save(update_fields=["record", "tutor", "status", "claimed_at"])
    record(
        action,
        request=request,
        actor=actor,
        actor_label="" if actor is not None else "the queue",
        center=submission.center,
        target=submission,
        student=str(submission.student_id),
        tutor=str(tutor.pk),
    )


def claim(
    submission_id: Any,
    tutor: User,
    *,
    request: HttpRequest | None = None,
    now: datetime | None = None,
) -> Submission:
    now = now or timezone.now()
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update(of=("self",))
            .select_related("center", "student", "record")
            .get(pk=submission_id)
        )
        if submission.status != SubmissionStatus.WAITING:
            raise QueueError("taken", _("Someone else has this submission, or it was canceled."))
        _take(
            submission,
            tutor,
            action=AuditAction.SUBMISSION_CLAIMED,
            request=request,
            actor=tutor,
            now=now,
        )
    return submission


def assign(
    submission_id: Any,
    tutor: User,
    *,
    by: User | None,
    request: HttpRequest | None = None,
    base_url: str = "",
    now: datetime | None = None,
) -> Submission:
    """Give a waiting or claimed submission to a tutor. `by` is None for the queue itself."""
    now = now or timezone.now()
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update(of=("self",))
            .select_related("center", "student", "record")
            .get(pk=submission_id)
        )
        if not submission.is_open:
            raise QueueError("closed", _("This submission is already returned or canceled."))
        if not Membership.objects.filter(
            center=submission.center,
            user=tutor,
            is_active=True,
            role__in=[Role.TUTOR, Role.DIRECTOR],
        ).exists():
            raise QueueError("not_staff", _("That person can't take submissions in this center."))
        if submission.tutor_id == tutor.pk:
            return submission
        _take(
            submission,
            tutor,
            action=AuditAction.SUBMISSION_ASSIGNED,
            request=request,
            actor=by,
            now=now,
        )
        if by is None or by.pk != tutor.pk:
            submission_pk = submission.pk
            transaction.on_commit(
                lambda: notifications.send_assigned(submission_pk, base_url=base_url)
            )
    return submission


def cancel(submission_id: Any, *, by: User, request: HttpRequest | None = None) -> Submission:
    """The student takes a submission back while nobody has it.

    A director can cancel any open one.
    """
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update(of=("self",))
            .select_related("center")
            .get(pk=submission_id)
        )
        if not submission.is_open:
            raise QueueError("closed", _("This submission is already returned or canceled."))
        if submission.status == SubmissionStatus.CLAIMED and by.pk == submission.student_id:
            raise QueueError(
                "claimed", _("A tutor is working on this one. Ask the center to cancel it.")
            )
        submission.status = SubmissionStatus.CANCELED
        submission.canceled_at = timezone.now()
        submission.save(update_fields=["status", "canceled_at"])
        record(
            AuditAction.SUBMISSION_CANCELED,
            request=request,
            actor=by,
            center=submission.center,
            target=submission,
            student=str(submission.student_id),
        )
    return submission


def returned_by_record(rec: SessionRecord, *, now: datetime | None = None) -> bool:
    """Called when a record becomes final. Returns the submission it was written for."""
    submission = (
        Submission.objects.select_for_update(of=("self",))
        .select_related("center")
        .filter(record=rec, status=SubmissionStatus.CLAIMED)
        .first()
    )
    if submission is None:
        return False
    submission.status = SubmissionStatus.RETURNED
    submission.returned_at = now or rec.finalized_at or timezone.now()
    submission.save(update_fields=["status", "returned_at"])
    record(
        AuditAction.SUBMISSION_RETURNED,
        actor=rec.finalized_by,
        center=submission.center,
        target=submission,
        student=str(submission.student_id),
    )
    return True


# --- The queue's jobs -------------------------------------------------------------


def open_counts(center: Center) -> dict[Any, int]:
    """How many claimed submissions each tutor of the center has."""
    rows = (
        Submission.objects.filter(center=center, status=SubmissionStatus.CLAIMED)
        .values("tutor")
        .annotate(n=Count("pk"))
    )
    return {row["tutor"]: row["n"] for row in rows}


def auto_assign(*, base_url: str = "", now: datetime | None = None) -> int:
    """Give each waiting submission to the tutor with the fewest open ones, while any has room."""
    now = now or timezone.now()
    assigned = 0
    queues = QueueSettings.objects.filter(
        is_open=True, auto_assign=True, center__is_active=True
    ).select_related("center")
    for queue in queues:
        center = queue.center
        waiting = list(
            Submission.objects.filter(center=center, status=SubmissionStatus.WAITING).order_by(
                "due_at", "created_at"
            )
        )
        if not waiting:
            continue
        tutors = list(
            User.objects.filter(
                memberships__center=center,
                memberships__role=Role.TUTOR,
                memberships__is_active=True,
                is_active=True,
            ).distinct()
        )
        load = open_counts(center)
        for submission in waiting:
            candidates = [t for t in tutors if load.get(t.pk, 0) < queue.max_open_per_tutor]
            if not candidates:
                break
            tutor = min(candidates, key=lambda t: (load.get(t.pk, 0), str(t.pk)))
            try:
                assign(submission.pk, tutor, by=None, base_url=base_url, now=now)
            except QueueError:
                continue  # taken or canceled while we looked
            load[tutor.pk] = load.get(tutor.pk, 0) + 1
            assigned += 1
    return assigned


def overdue_alerts(*, base_url: str, now: datetime | None = None) -> int:
    """Tell each tutor, and the directors, about submissions that just ran past their date.

    Each submission is counted once, ever: it is marked in the same step that
    picks it, so a second scheduler sends nothing. Returns how many emails were queued.
    """
    now = now or timezone.now()
    sent = 0
    due = Submission.objects.filter(
        status__in=[SubmissionStatus.WAITING, SubmissionStatus.CLAIMED],
        due_at__lt=now,
        overdue_notified_at__isnull=True,
        center__is_active=True,
    )
    for center in Center.objects.filter(pk__in=due.values("center")):
        with transaction.atomic():
            picked = list(
                Submission.objects.select_for_update(skip_locked=True, of=("self",))
                .filter(pk__in=due.filter(center=center).values("pk"))
                .select_related("tutor")
            )
            if not picked:
                continue
            Submission.objects.filter(pk__in=[s.pk for s in picked]).update(overdue_notified_at=now)
            sent += notifications.send_overdue(center, picked, base_url=base_url)
    return sent


# --- Turnaround ------------------------------------------------------------------


@dataclass
class TutorTurnaround:
    tutor: User
    returned: int
    median_hours: float | None


@dataclass
class Turnaround:
    sent: int
    returned: int
    canceled: int
    open: int
    overdue: int
    on_time: int
    median_hours: float | None
    by_tutor: list[TutorTurnaround]


def _hours(submission: Submission) -> float:
    if submission.returned_at is None:
        return 0.0
    return (submission.returned_at - submission.created_at).total_seconds() / 3600


def turnaround(
    center: Center, start: date, end: date, *, now: datetime | None = None
) -> Turnaround:
    """Submissions sent between two days: how many came back, how fast, and how many on time."""
    now = now or timezone.now()
    zone = _zone(center)
    first = datetime.combine(start, time.min, tzinfo=zone)
    last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone)
    found = list(
        Submission.objects.filter(center=center, created_at__gte=first, created_at__lt=last)
        .select_related("tutor")
        .order_by("created_at")
    )
    returned = [s for s in found if s.status == SubmissionStatus.RETURNED and s.returned_at]
    by_tutor: dict[Any, list[Submission]] = {}
    for s in returned:
        by_tutor.setdefault(s.tutor_id, []).append(s)
    rows = []
    for subs in by_tutor.values():
        tutor = subs[0].tutor
        if tutor is None:
            continue
        rows.append(TutorTurnaround(tutor, len(subs), median(_hours(s) for s in subs)))
    rows.sort(key=lambda r: (-r.returned, r.tutor.display_name.lower()))
    return Turnaround(
        sent=len(found),
        returned=len(returned),
        canceled=sum(1 for s in found if s.status == SubmissionStatus.CANCELED),
        open=sum(1 for s in found if s.is_open),
        overdue=sum(1 for s in found if s.is_open and s.due_at < now),
        on_time=sum(1 for s in returned if s.returned_at and s.returned_at <= s.due_at),
        median_hours=median(_hours(s) for s in returned) if returned else None,
        by_tutor=rows,
    )


def counts_for(center: Center) -> dict[str, int]:
    """Numbers for the queue page."""
    stats = Submission.objects.filter(center=center).aggregate(
        waiting=Count("pk", filter=Q(status=SubmissionStatus.WAITING)),
        claimed=Count("pk", filter=Q(status=SubmissionStatus.CLAIMED)),
    )
    return {"waiting": stats["waiting"], "claimed": stats["claimed"]}


def files_of(submission: Submission, *, role: str) -> Sequence[SubmissionFile]:
    return list(submission.files.filter(role=role).select_related("file", "file__uploaded_by"))
