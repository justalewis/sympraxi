"""Surveys after sessions: who gets one, the one-time link, anonymous answers, results.

When a survey goes out is a center setting (`Center.survey_timing`): when the
tutor finalizes the student's session record, or a set time after the
appointment ends (`Center.survey_delay_minutes`). Each minute the scheduler
invites every student whose survey fell due in the last two days
(`LOOK_BACK`), so a scheduler that was down catches up, and one started on an
installation with a term of history doesn't survey all of it.

Who gets one: a student marked as here (finalizing a record marks them) for an
appointment that is still scheduled and not a placeholder, who didn't leave the
group, whose account is active and who hasn't turned survey emails off.
Never for a canceled appointment or a no-show; a student no one has marked gets
none until they are marked as here. Meetings that weren't booked get none. The
schedule's own survey is sent if it has one in use, otherwise the center's
default.

Each invitation is sent once. The scheduler writes a `SurveyInvitation` (unique
per appointment and student) and queues the email in the same transaction, so
a second scheduler, or a sweep run twice, inserts nothing and sends nothing.
The mail worker makes the link's token and sends the email
(`sympraxi/forms/tasks.py`); only a hash of the token is stored, so the token
isn't in the database, not even in the task queue.

The answers are anonymous. The stored response has no student, appointment,
record or person who answered, and only the date it was given, in the center's
time zone. The invitation isn't changed or even locked when its link is used;
the used link goes in `UsedSurveyToken` under a second hash that can't be
matched to the invitation without the link. Answering writes nothing else: no
audit event, no session, no message. A link that is unknown, used or expired,
or whose survey was stopped, gets the same page.

Results: a survey's answers in one term, counted by question key and option
key across every version, shown only once at least `MIN_RESPONSES` people
answered in that term. Terms don't overlap here: a response counts in the
latest term that started on or before the day it was given, so no two periods
can be compared to pick out one response.
"""

import logging
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.centers.models import SURVEY_DELAY_CHOICES, Center, SurveyTiming
from sympraxi.forms import services
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Answer,
    Form,
    FormKind,
    FormVersion,
    Question,
    QuestionKind,
    Response,
    SurveyInvitation,
    UsedSurveyToken,
    plain_number,
    token_hash,
    used_digest,
)
from sympraxi.forms.services import AnswerValue
from sympraxi.records.models import RecordStatus, SessionRecord
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Participant,
    Term,
)

logger = logging.getLogger("sympraxi.scheduler")

# How far back the scheduler looks for surveys that fell due.
LOOK_BACK = timedelta(days=2)
# How long a survey link works after it is sent.
LINK_DAYS = 14
# Results show only once this many people answered in the term.
MIN_RESPONSES = 5
# What `secrets.token_urlsafe(32)` makes. Anything else is refused before hashing.
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{40,50}")


# --- Sending ------------------------------------------------------------------------


def _centers_with_surveys() -> Any:
    return Form.objects.filter(
        kind=FormKind.SURVEY, is_active=True, versions__published_at__isnull=False
    ).values("center_id")


def send_due_invitations(*, base_url: str, now: datetime | None = None) -> int:
    """Queue every survey invitation due now. Returns how many were queued."""
    now = now or timezone.now()
    centers = _centers_with_surveys()
    candidates: set[tuple[UUID, UUID]] = set(
        SessionRecord.objects.filter(
            status=RecordStatus.FINAL,
            appointment__isnull=False,
            finalized_at__gt=now - LOOK_BACK,
            finalized_at__lte=now,
            center__survey_timing=SurveyTiming.AFTER_RECORD,
            center_id__in=centers,
        ).values_list("appointment_id", "student_id")
    )
    longest = timedelta(minutes=max(m for m, _label in SURVEY_DELAY_CHOICES))
    ended = Participant.objects.filter(
        canceled_at__isnull=True,
        attendance=Attendance.ATTENDED,
        appointment__status=AppointmentStatus.SCHEDULED,
        appointment__ends_at__gt=now - LOOK_BACK - longest,
        appointment__ends_at__lte=now,
        appointment__schedule__center__survey_timing=SurveyTiming.AFTER_END,
        appointment__schedule__center_id__in=centers,
    ).values_list(
        "appointment_id",
        "student_id",
        "appointment__ends_at",
        "appointment__schedule__center__survey_delay_minutes",
    )
    for appointment_id, student_id, ends_at, delay in ended:
        if now - LOOK_BACK < ends_at + timedelta(minutes=delay) <= now:
            candidates.add((appointment_id, student_id))
    if not candidates:
        return 0
    invited = set(
        SurveyInvitation.objects.filter(appointment_id__in={a for a, _s in candidates}).values_list(
            "appointment_id", "student_id"
        )
    )
    sent = 0
    for appointment_id, student_id in sorted(candidates - invited, key=str):
        try:
            sent += invite(appointment_id, student_id, base_url=base_url, now=now)
        except Exception:
            # One bad appointment mustn't stop the others; it's tried again next
            # minute, while it's still inside the look-back.
            logger.exception("Survey for appointment %s failed", appointment_id)
    return sent


def due_at(appointment: Appointment, student_id: Any, center: Center) -> datetime | None:
    """When this student's survey falls due, or None if it can't yet."""
    if center.survey_timing == SurveyTiming.AFTER_RECORD:
        rec = SessionRecord.objects.filter(
            appointment=appointment, student_id=student_id, status=RecordStatus.FINAL
        ).first()
        return rec.finalized_at if rec is not None else None
    return appointment.ends_at + timedelta(minutes=center.survey_delay_minutes)


def invite(appointment_id: Any, student_id: Any, *, base_url: str, now: datetime) -> int:
    """Invite one student to the survey about one appointment, if they should get one.

    The rules are checked again with the appointment locked, so a cancellation
    committing at the same moment is seen.
    """
    from sympraxi.forms import tasks

    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update(of=("self",))
            .select_related("schedule__center")
            .get(pk=appointment_id)
        )
        center = appointment.schedule.center
        participant = (
            appointment.participants.filter(student_id=student_id, canceled_at__isnull=True)
            .select_related("student")
            .first()
        )
        if (
            not center.is_active
            or not appointment.is_active
            or appointment.is_placeholder
            or participant is None
            or participant.attendance != Attendance.ATTENDED
            or not participant.student.is_active
            or not participant.student.survey_emails
        ):
            return 0
        due = due_at(appointment, student_id, center)
        if due is None or not (now - LOOK_BACK < due <= now):
            return 0
        form = services.survey_form(appointment.schedule)
        if form is None:
            return 0
        try:
            with transaction.atomic():
                invitation = SurveyInvitation.objects.create(
                    appointment=appointment,
                    student=participant.student,
                    form=form,
                    sent_at=now,
                    expires_at=now + timedelta(days=LINK_DAYS),
                )
        except IntegrityError:
            return 0  # Already sent.
        # Queued in this transaction, so the invitation is recorded exactly when
        # its email is queued.
        tasks.send_survey_invitation.enqueue(invitation.pk, base_url)
    return 1


def new_token() -> str:
    return secrets.token_urlsafe(32)


def survey_url(token: str, url_base: str) -> str:
    return url_base.rstrip("/") + reverse("survey:answer", kwargs={"token": token})


def invitation_message(invitation: SurveyInvitation, token: str, *, url_base: str) -> EmailMessage:
    """The survey email: which session, the link, that it's anonymous, and how to stop them."""
    appointment = invitation.appointment
    center = appointment.schedule.center
    student = invitation.student
    with timezone.override(center.time_zone):
        context = {
            "student": student,
            "center": center,
            "appointment": appointment,
            "url": survey_url(token, url_base),
            "expires_at": invitation.expires_at,
            "settings_url": url_base.rstrip("/") + reverse("accounts:reminders"),
            "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        }
        body = render_to_string("forms/email/survey_invitation.txt", context)
    subject = _("A short survey about your session")
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[student.email],
        reply_to=[center.contact_email] if center.contact_email else [],
    )


# --- Answering ----------------------------------------------------------------------


@dataclass
class OpenSurvey:
    """A survey link that works: the survey to answer and the version asked now."""

    form: Form
    version: FormVersion

    @property
    def center(self) -> Center:
        return self.form.center


def open_survey(token: str, *, now: datetime | None = None) -> OpenSurvey | None:
    """The survey a link opens, or None if it is unknown, used or expired, or the
    survey was stopped. Reads only; writes nothing."""
    now = now or timezone.now()
    if not TOKEN_SHAPE.fullmatch(token):
        return None
    invitation = (
        SurveyInvitation.objects.select_related("form__center")
        .filter(token_hash=token_hash(token))
        .exclude(token_hash="")
        .first()
    )
    if invitation is None or invitation.expires_at <= now:
        return None
    if UsedSurveyToken.objects.filter(digest=used_digest(token)).exists():
        return None
    form = invitation.form
    version = services.published_version(form)
    if version is None or not form.center.is_active:
        return None
    return OpenSurvey(form=form, version=version)


def submit(
    survey: OpenSurvey,
    token: str,
    values: dict[UUID, AnswerValue],
    *,
    now: datetime | None = None,
) -> bool:
    """Store anonymous answers and use up the link. False if the link was used meanwhile.

    The used link and the response are written together, so a link is used
    exactly when its answers are stored. Neither names the invitation.
    """
    now = now or timezone.now()
    with transaction.atomic():
        try:
            with transaction.atomic():
                UsedSurveyToken.objects.create(digest=used_digest(token))
        except IntegrityError:
            return False
        services.create_response(
            version=survey.version,
            values=values,
            answered_by=None,
            student=None,
            answered_on=timezone.localdate(now, timezone=ZoneInfo(survey.center.time_zone)),
        )
    return True


# --- Results ------------------------------------------------------------------------


@dataclass
class Period:
    """The days whose responses count in one term: from its first day until the next
    term's first day."""

    term: Term
    starts_on: date
    ends_before: date | None

    @property
    def key(self) -> str:
        return str(self.term.pk)

    def includes(self, day: date) -> bool:
        return self.starts_on <= day and (self.ends_before is None or day < self.ends_before)


def periods(center: Center) -> list[Period]:
    terms = list(Term.objects.filter(center=center).order_by("starts_on", "pk"))
    return [
        Period(
            term=term,
            starts_on=term.starts_on,
            ends_before=terms[i + 1].starts_on if i + 1 < len(terms) else None,
        )
        for i, term in enumerate(terms)
    ]


def current_period(choices: list[Period], today: date) -> Period | None:
    """The period today falls in, or the latest one before it, or the first."""
    started = [p for p in choices if p.starts_on <= today]
    if started:
        return started[-1]
    return choices[0] if choices else None


@dataclass
class OptionCount:
    label: str
    count: int
    percent: int


@dataclass
class QuestionResult:
    label: str
    kind: str
    answered: int
    options: list[OptionCount] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    @property
    def is_choice(self) -> bool:
        return self.kind in CHOICE_KINDS


@dataclass
class Results:
    responses: int
    shown: bool
    questions: list[QuestionResult] = field(default_factory=list)


def results(form: Form, period: Period) -> Results:
    """The survey's answers in one period, or only how many there were if too few."""
    responses = Response.objects.filter(form=form, answered_on__gte=period.starts_on)
    if period.ends_before is not None:
        responses = responses.filter(answered_on__lt=period.ends_before)
    count = responses.count()
    if count < MIN_RESPONSES:
        return Results(responses=count, shown=False)

    # Each question in its latest wording, latest version first; then questions
    # dropped from later versions.
    versions = list(
        form.versions.filter(published_at__isnull=False)
        .order_by("number")
        .prefetch_related("questions__options")
    )
    latest: dict[UUID, Question] = {}
    order: dict[UUID, tuple[int, int]] = {}
    option_labels: dict[UUID, dict[UUID, str]] = defaultdict(dict)
    for version in versions:
        for question in version.questions.all():
            latest[question.key] = question
            order[question.key] = (-version.number, question.position)
            for option in question.options.all():
                option_labels[question.key][option.key] = option.label

    by_key: dict[UUID, list[tuple[list[UUID], str, Any, Any]]] = defaultdict(list)
    for key, options, text, number, day in Answer.objects.filter(
        response__in=responses
    ).values_list("key", "options", "text", "number", "date"):
        by_key[key].append((options, text, number, day))

    newest = versions[-1].number if versions else 0
    shown_keys = [k for k in latest if k in by_key or order[k][0] == -newest]
    questions = []
    for key in sorted(shown_keys, key=lambda k: order[k]):
        question = latest[key]
        rows = by_key.get(key, [])
        result = QuestionResult(label=question.label, kind=question.kind, answered=len(rows))
        if question.kind in CHOICE_KINDS:
            counts = Counter(option for options, _t, _n, _d in rows for option in options)
            current = [o.key for o in question.options.all()]
            keys = current + [k for k in option_labels[key] if k not in current]
            result.options = [
                OptionCount(
                    label=option_labels[key][k],
                    count=counts[k],
                    percent=round(100 * counts[k] / len(rows)) if rows else 0,
                )
                for k in keys
            ]
        else:
            # Sorted, so the order says nothing about when each was given.
            given = [(text, number, day) for _o, text, number, day in rows]
            if question.kind == QuestionKind.NUMBER:
                given.sort(key=lambda g: (g[1] is None, g[1] or 0))
            elif question.kind == QuestionKind.DATE:
                given.sort(key=lambda g: (g[2] is None, g[2] or date.min))
            else:
                given.sort(key=lambda g: g[0].casefold())
            result.texts = [_text(question, *g) for g in given]
        questions.append(result)
    return Results(responses=count, shown=True, questions=questions)


def _text(question: Question, text: str, number: Any, day: Any) -> str:
    if question.kind == QuestionKind.NUMBER:
        return plain_number(number) if number is not None else ""
    if question.kind == QuestionKind.DATE:
        return date_format(day, "F j, Y") if day else ""
    return text
