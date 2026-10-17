from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from django import forms
from django.db.models import Q, QuerySet
from django.http import QueryDict
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.records.models import Axis, Resource, SessionRecord, Topic, WritingProject
from sympraxi.records.services import Content
from sympraxi.scheduling.forms import (
    DATETIME_LOCAL,
    DateInput,
    DateTimeLocalInput,
    PersonChoiceField,
    choice_field,
    students_of,
)

NEW_PROJECT = "new"


def topic_choices(selected: QuerySet[Topic] | None = None) -> QuerySet[Topic]:
    """Terms a tutor can tag: every current term, plus retired ones already on the record."""
    current = Q(is_deprecated=False)
    if selected is not None:
        current |= Q(pk__in=selected.values("pk"))
    return Topic.objects.filter(current).select_related("parent").order_by("position")


class TopicsField(forms.ModelMultipleChoiceField):
    """Topic tags, posted as slugs. Rendered by records/_topic_fields.html."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("queryset", topic_choices())
        kwargs.setdefault("required", False)
        kwargs.setdefault("to_field_name", "slug")
        kwargs.setdefault("widget", forms.CheckboxSelectMultiple)
        super().__init__(**kwargs)


class RecordForm(forms.Form):
    """The tutor's record: what the student sees, the tags, staff notes and details.

    Attached resources aren't Django fields: each attached resource posts its ID
    as `resources` and its note as `note-<id>`, and `remove` lists any to take
    off. `attached()` reads them.
    """

    summary = forms.CharField(
        label=_("Summary for the student"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_(
            "Write it to the student: what you worked on and what they did well. They read "
            "it on their My writing page."
        ),
    )
    next_steps = forms.CharField(
        label=_("Next steps"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("What the student plans to do next. The student sees this."),
    )
    topics = TopicsField(label=_("Topics"))
    staff_notes = forms.CharField(
        label=_("Notes for staff"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_(
            "For tutors and directors who work with this student later. The student can ask "
            "to read these too."
        ),
    )
    minutes = forms.IntegerField(
        label=_("Length in minutes"),
        min_value=1,
        max_value=600,
        help_text=_("How long the session actually lasted."),
    )
    project = forms.ChoiceField(label=_("Writing project"), required=False)
    project_title = forms.CharField(label=_("New project title"), max_length=200, required=False)
    project_course = forms.CharField(label=_("Course"), max_length=100, required=False)
    project_instructor = forms.CharField(label=_("Instructor"), max_length=200, required=False)
    project_due_on = forms.DateField(label=_("Due date"), required=False, widget=DateInput())
    copy_to_group = forms.BooleanField(required=False)
    reason = forms.CharField(
        label=_("Why are you changing this record?"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Kept with the change, along with your name and the time."),
    )

    def __init__(
        self,
        data: QueryDict | None = None,
        *,
        rec: SessionRecord | None,
        center: Center,
        student: User,
        amend: bool = False,
        group_others: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.rec = rec
        self.center = center
        self.student = student
        self.amend = amend
        selected = rec.topics.all() if rec is not None else None
        cast(TopicsField, self.fields["topics"]).queryset = topic_choices(selected)
        # A draft can be saved without a summary; finalizing can't (services.finalize).
        self.fields["summary"].needed_to_finalize = True  # type: ignore[attr-defined]
        self.projects = list(
            WritingProject.objects.filter(center=center, student=student).filter(
                Q(is_finished=False) | Q(pk=rec.project_id if rec else None)
            )
        )
        choices = [("", str(_("Not part of a project")))]
        choices += [(str(p.pk), _project_label(p)) for p in self.projects]
        choices.append((NEW_PROJECT, str(_("A new project (fill in below)"))))
        cast(forms.ChoiceField, self.fields["project"]).choices = choices
        copy = self.fields["copy_to_group"]
        copy.label = _(
            "Also start drafts for the other %(n)s students in this group, with the same "
            "summary, next steps, topics, length, resources and answers to the center's "
            "questions. Staff notes and the project aren't copied. You'll still read and "
            "finalize each one."
        ) % {"n": group_others}
        if not group_others or amend:
            del self.fields["copy_to_group"]
        if amend:
            self.fields["reason"].required = True
        else:
            del self.fields["reason"]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("project") == NEW_PROJECT and not data.get("project_title", "").strip():
            self.add_error("project_title", _("Give the new project a title."))
        return data

    def topic_slugs(self) -> set[str]:
        """The topics as posted, or as saved, for drawing the checkboxes."""
        if self.is_bound:
            return set(cast(QueryDict, self.data).getlist("topics"))
        return set(self.initial.get("topics") or [])

    def chosen_project(self) -> WritingProject | None:
        value = self.cleaned_data.get("project")
        return next((p for p in self.projects if str(p.pk) == value), None)

    def wants_new_project(self) -> bool:
        return self.cleaned_data.get("project") == NEW_PROJECT

    def content(
        self, resources: list[tuple[Resource, str]], project: WritingProject | None
    ) -> Content:
        data = self.cleaned_data
        return Content(
            minutes=data["minutes"],
            staff_notes=data["staff_notes"].strip(),
            summary=data["summary"].strip(),
            next_steps=data["next_steps"].strip(),
            topics=list(data["topics"]),
            project=project,
            resources=resources,
        )


def _project_label(project: WritingProject) -> str:
    parts = [project.title]
    if project.course:
        parts.append(project.course)
    if project.due_on:
        parts.append(str(_("due %(date)s") % {"date": date_format(project.due_on, "M j")}))
    return ", ".join(parts)


@dataclass
class Attached:
    resource: Resource
    note: str


def attached_from_post(
    data: QueryDict, center: Center, rec: SessionRecord | None
) -> list[Attached]:
    """The attached resources as posted, in order, leaving out any marked for removal.

    A resource counts only if this center may use it, or it is already on the
    record (an entry that has since left the catalog stays where it was attached).
    """
    removed = set(data.getlist("remove"))
    ids: list[str] = []
    for value in data.getlist("resources"):
        if value not in ids and value not in removed and _is_uuid(value):
            ids.append(value)
    allowed = Resource.objects.available_to(center)
    if rec is not None:
        allowed = Resource.objects.filter(
            Q(pk__in=allowed.values("pk")) | Q(attachments__record=rec)
        ).distinct()
    found = {str(r.pk): r for r in allowed.filter(pk__in=ids)}
    return [
        Attached(resource=found[i], note=data.get(f"note-{i}", "").strip())
        for i in ids
        if i in found
    ]


def attached_from_record(rec: SessionRecord | None) -> list[Attached]:
    if rec is None:
        return []
    return [
        Attached(resource=a.resource, note=a.note)
        for a in rec.attachments.select_related("resource")
    ]


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


class StandaloneForm(forms.Form):
    """Who and when, for a meeting that wasn't booked. The rest is the usual record form."""

    student = PersonChoiceField(queryset=User.objects.none(), label=_("Student"))
    held_at = forms.DateTimeField(
        label=_("When the meeting started"),
        input_formats=[DATETIME_LOCAL],
        widget=DateTimeLocalInput(),
    )
    minutes = forms.IntegerField(label=_("Length in minutes"), min_value=1, max_value=600)

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choice_field(self, "student").queryset = students_of(center)


class ProjectForm(forms.ModelForm):
    class Meta:
        model = WritingProject
        fields = ["title", "course", "instructor", "due_on", "is_finished"]
        widgets = {"due_on": DateInput()}


class ResourceForm(forms.ModelForm):
    """A center's own link. Tag at least one concern so it can be suggested."""

    topics = TopicsField(label=_("Topics"))

    class Meta:
        model = Resource
        fields = [
            "title",
            "url",
            "publisher",
            "license",
            "summary",
            "language",
            "topics",
            "is_active",
        ]
        widgets = {"summary": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "publisher": _("Who wrote or hosts the page. Leave empty to use your center's name."),
            "language": _("A two-letter code, such as en or es."),
        }

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        self.fields["publisher"].required = False
        if self.instance.pk:
            self.initial["topics"] = list(self.instance.topics.values_list("slug", flat=True))

    def clean_url(self) -> str:
        url: str = self.cleaned_data["url"]
        if not url.startswith("https://"):
            raise forms.ValidationError(_("Use an https:// address."))
        return url

    def clean_publisher(self) -> str:
        return self.cleaned_data.get("publisher") or self.center.name

    def clean_topics(self) -> Any:
        topics = self.cleaned_data["topics"]
        if not any(t.axis == Axis.CONCERN for t in topics):
            raise forms.ValidationError(_("Tag at least one concern, so tutors see it suggested."))
        return topics

    def topic_slugs(self) -> set[str]:
        if self.is_bound:
            return set(cast(QueryDict, self.data).getlist("topics"))
        return set(self.initial.get("topics", []))


class RecordSettingsForm(forms.ModelForm):
    class Meta:
        model = Center
        fields = ["students_see_staff_notes", "summary_email_includes_text"]
