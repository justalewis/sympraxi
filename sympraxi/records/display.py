"""Shapes the record pages need: topic checkboxes grouped by axis, and records by project."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from sympraxi.records.models import Axis, SessionRecord, Topic, WritingProject

AXIS_TEXT = {
    Axis.CONCERN: (
        _("What the writing needed"),
        _(
            "Tag the most specific term that fits; finalizing needs at least one. Open a "
            "group to see its terms."
        ),
    ),
    Axis.STAGE: (_("Where the writer was in the work"), _("Choose every stage that applies.")),
    Axis.ACTION: (
        _("What happened in the session"),
        _("Choose every action that took up a real part of the session."),
    ),
}


@dataclass
class Choice:
    topic: Topic
    checked: bool

    @property
    def html_id(self) -> str:
        return "topic-" + self.topic.slug.replace(":", "--")


@dataclass
class Group:
    label: str
    choices: list[Choice]

    @property
    def chosen(self) -> int:
        return sum(c.checked for c in self.choices)

    @property
    def html_id(self) -> str:
        return "topic-group-" + self.choices[0].topic.slug.replace(":", "--")


@dataclass
class AxisSection:
    axis: str
    legend: str
    hint: str
    groups: list[Group] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)


def topic_axes(topics: QuerySet[Topic], checked: set[str]) -> list[AxisSection]:
    """Every taggable term, checked or not. Concerns come grouped under their parent."""
    sections = {
        axis: AxisSection(axis=axis, legend=str(text[0]), hint=str(text[1]))
        for axis, text in AXIS_TEXT.items()
    }
    groups: dict[str, Group] = {}
    for topic in topics:
        choice = Choice(topic=topic, checked=topic.slug in checked)
        section = sections[Axis(topic.axis)]
        if topic.axis == Axis.CONCERN:
            family = topic.slug.split(":", 1)[0]
            if family not in groups:
                label = topic.parent.label if topic.parent else topic.label
                groups[family] = Group(label=label, choices=[])
                section.groups.append(groups[family])
            groups[family].choices.append(choice)
        else:
            section.choices.append(choice)
    return list(sections.values())


def topics_by_axis(topics: Iterable[Topic]) -> list[tuple[str, list[Topic]]]:
    """A record's tags for reading: [(axis label, topics)], skipping empty axes."""
    by_axis: dict[str, list[Topic]] = {}
    for topic in sorted(topics, key=lambda t: t.position):
        by_axis.setdefault(topic.axis, []).append(topic)
    return [(str(Axis(axis).label), by_axis[axis]) for axis in Axis.values if axis in by_axis]


@dataclass
class ProjectGroup:
    project: WritingProject | None
    records: list[SessionRecord]


def by_project(
    records: Iterable[SessionRecord], projects: Iterable[WritingProject]
) -> list[ProjectGroup]:
    """Records grouped under their projects (open first), then sessions with no project.

    Projects with no records still show, so a student sees everything they're
    working on.
    """
    groups = {p.pk: ProjectGroup(project=p, records=[]) for p in projects}
    loose = ProjectGroup(project=None, records=[])
    for rec in records:
        if rec.project_id is not None:
            group = groups.get(rec.project_id)
            if group is None and rec.project is not None:
                group = groups[rec.project_id] = ProjectGroup(project=rec.project, records=[])
            if group is not None:
                group.records.append(rec)
                continue
        loose.records.append(rec)
    ordered = sorted(
        groups.values(),
        key=lambda g: (
            g.project.is_finished if g.project else True,
            -(g.records[0].held_at.timestamp() if g.records else 0),
        ),
    )
    return [*ordered, loose] if loose.records else ordered
