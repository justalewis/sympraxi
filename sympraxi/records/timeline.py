"""One timeline of a student's work at a center, across terms.

It gathers what the center keeps in separate places into a single list in time order,
newest first, grouped by the center's terms: each session record, each writing goal
when it was set and when it was met or dropped, each writing project when it began,
and each draft sent for written feedback and when it came back.

What a person sees follows what they may read elsewhere. A student sees their own final
records, their goals, their projects and their submissions. Staff see what they may open:
a tutor the records of students they work with, drafts included for the records they may
read. Staff notes are never part of an entry: a record shows its date, its tutor, its
project and the start of the summary written for the student.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can
from sympraxi.feedback.models import Submission, SubmissionStatus
from sympraxi.records.models import (
    Goal,
    GoalStatus,
    RecordStatus,
    SessionRecord,
    WritingProject,
)
from sympraxi.scheduling.models import Term

SUMMARY_CHARS = 200


@dataclass
class Entry:
    when: datetime
    kind: str
    title: str
    detail: str = ""
    url: str = ""


@dataclass
class Group:
    label: str
    entries: list[Entry] = field(default_factory=list)


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1].rstrip() + "…"


def entries(center: Center, student: User, viewer: Any, *, own: bool) -> list[Entry]:
    """Everything the viewer may see on the student's timeline, unsorted."""
    slug = center.slug
    found: list[Entry] = []

    records = SessionRecord.objects.filter(center=center, student=student).select_related(
        "tutor", "project"
    )
    if own:
        records = records.filter(status=RecordStatus.FINAL)
    for rec in records:
        if own:
            if not can(viewer, Action.VIEW_OWN_WRITING, rec):
                continue
            url = reverse("records:my_record", kwargs={"slug": slug, "record_id": rec.pk})
        else:
            if not can(viewer, Action.VIEW_RECORDS, rec):
                continue
            url = reverse("records:detail", kwargs={"slug": slug, "record_id": rec.pk})
        title = _("Session with %(tutor)s") % {"tutor": rec.tutor.display_name}
        if rec.project is not None:
            title += f", {rec.project.title}"
        if not rec.is_final:
            title += " (" + _("draft") + ")"
        found.append(Entry(rec.held_at, "session", title, _snippet(rec.summary), url))

    for goal in Goal.objects.filter(center=center, student=student):
        found.append(Entry(goal.created_at, "goal", _("Goal set: %(text)s") % {"text": goal.text}))
        if goal.status != GoalStatus.OPEN and goal.closed_at is not None:
            label = (
                _("Goal met: %(text)s")
                if goal.status == GoalStatus.MET
                else _("Goal dropped: %(text)s")
            )
            found.append(Entry(goal.closed_at, "goal_closed", label % {"text": goal.text}))

    for project in WritingProject.objects.filter(center=center, student=student):
        title = _("Writing project begun: %(title)s") % {"title": project.title}
        found.append(Entry(project.created_at, "project", title, project.course))

    for submission in Submission.objects.filter(center=center, student=student).select_related(
        "record"
    ):
        url = ""
        if can(viewer, Action.VIEW_SUBMISSION, submission):
            url = reverse("feedback:detail", kwargs={"slug": slug, "submission_id": submission.pk})
        found.append(
            Entry(
                submission.created_at,
                "submission",
                _("Draft sent for written feedback: %(title)s") % {"title": submission.title},
                url=url,
            )
        )
        if submission.status == SubmissionStatus.RETURNED and submission.returned_at:
            found.append(
                Entry(
                    submission.returned_at,
                    "returned",
                    _("Written feedback returned: %(title)s") % {"title": submission.title},
                    url=url,
                )
            )
    return found


def group_by_term(center: Center, found: list[Entry]) -> list[Group]:
    """Newest first, in the center's terms, with what falls outside a term last."""
    zone = ZoneInfo(center.time_zone)
    terms = list(Term.objects.filter(center=center).order_by("-starts_on"))
    groups: dict[str, Group] = {}
    order: list[str] = []
    outside = Group(_("Outside any term"))

    def term_for(day: date) -> Term | None:
        return next((t for t in terms if t.starts_on <= day <= t.ends_on), None)

    for entry in sorted(found, key=lambda e: e.when, reverse=True):
        term = term_for(entry.when.astimezone(zone).date())
        if term is None:
            outside.entries.append(entry)
            continue
        if term.name not in groups:
            groups[term.name] = Group(term.name)
            order.append(term.name)
        groups[term.name].entries.append(entry)
    ordered = [groups[name] for name in order]
    return ordered + ([outside] if outside.entries else [])


def build(center: Center, student: User, viewer: Any, *, own: bool) -> list[Group]:
    return group_by_term(center, entries(center, student, viewer, own=own))
