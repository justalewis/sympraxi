"""Writing goals: adding them, and marking them met, dropped or open again.

A goal is a sentence and, if the student wants, a day. It stays open until someone
marks it met or dropped, which keeps who did it and when; marking it open again clears
that, for a mistake. Every change is audited. Goals are never deleted.
"""

from datetime import date, timedelta

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records.models import Goal, GoalStatus, WritingProject

MAX_OPEN = 20
# How far ahead a goal's day may be.
FURTHEST = timedelta(days=730)


class GoalError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def open_goals(center: Center, student: User) -> list[Goal]:
    return list(Goal.objects.filter(center=center, student=student, status=GoalStatus.OPEN))


def add(
    *,
    actor: User,
    center: Center,
    student: User,
    text: str,
    target_on: date | None = None,
    project: WritingProject | None = None,
    request: HttpRequest | None = None,
    today: date | None = None,
) -> Goal:
    text = " ".join(text.split())
    if not text:
        raise GoalError(_("Write the goal first."))
    if len(text) > 300:
        raise GoalError(_("A goal can be 300 characters."))
    today = today or timezone.now().date()
    if target_on is not None and not today <= target_on <= today + FURTHEST:
        raise GoalError(_("Choose a day from today to two years ahead, or leave it empty."))
    if project is not None and (project.student_id != student.pk or project.center_id != center.pk):
        raise GoalError(_("That writing project isn't this student's."))
    with transaction.atomic():
        if (
            Goal.objects.filter(center=center, student=student, status=GoalStatus.OPEN).count()
            >= MAX_OPEN
        ):
            raise GoalError(
                _("There are %(n)s open goals. Mark some met or dropped before adding more.")
                % {"n": MAX_OPEN}
            )
        goal = Goal.objects.create(
            center=center,
            student=student,
            text=text,
            target_on=target_on,
            project=project,
            created_by=actor,
        )
        record(
            AuditAction.GOAL_ADDED,
            request=request,
            actor=actor,
            center=center,
            target=goal,
            student=str(student.pk),
        )
    return goal


def set_status(goal: Goal, status: str, *, actor: User, request: HttpRequest | None = None) -> Goal:
    """Mark a goal met or dropped, or open again."""
    if status not in GoalStatus.values:
        raise GoalError(_("That isn't a way to close a goal."))
    with transaction.atomic():
        locked = Goal.objects.select_for_update().get(pk=goal.pk)
        if locked.status == status:
            return locked
        locked.status = status
        if status == GoalStatus.OPEN:
            locked.closed_at = None
            locked.closed_by = None
        else:
            locked.closed_at = timezone.now()
            locked.closed_by = actor
        locked.save(update_fields=["status", "closed_at", "closed_by"])
        record(
            AuditAction.GOAL_CHANGED,
            request=request,
            actor=actor,
            center=locked.center,
            target=locked,
            student=str(locked.student_id),
            status=status,
        )
    return locked
