"""Writing goals and the timeline across terms: pages for the student and for staff.

The two goal pages are the same page with a different person in front of it: a student
writes and closes their own goals; a tutor who works with them, or a director, does it
with them. Adding a goal and marking one met, dropped or open all post to the page
itself, so it works without JavaScript.
"""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership
from sympraxi.core.policy import Action, can
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records import goals, timeline
from sympraxi.records.models import Goal, GoalStatus, WritingProject
from sympraxi.records.views import _crumbs, center_for, signed_in, student_for


class GoalForm(forms.Form):
    text = forms.CharField(
        label=gettext_lazy("Goal"),
        max_length=300,
        help_text=gettext_lazy("For example: use evidence in every body paragraph."),
    )
    target_on = forms.DateField(
        label=gettext_lazy("By when"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=gettext_lazy("Optional."),
    )
    project = forms.ModelChoiceField(
        label=gettext_lazy("Writing project"),
        queryset=WritingProject.objects.none(),
        required=False,
        help_text=gettext_lazy("Optional: the project this goal is for."),
    )

    def __init__(self, *args: Any, center: Center, student: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = cast(forms.ModelChoiceField, self.fields["project"])
        field.queryset = WritingProject.objects.filter(
            center=center, student=student, is_finished=False
        )


def _goal_page(
    request: HttpRequest,
    *,
    center: Center,
    student: User,
    title: str,
    intro: str,
    here: str,
    back: tuple[str, str],
) -> HttpResponse:
    user = signed_in(request)
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "add":
            form = GoalForm(request.POST, center=center, student=student)
            if form.is_valid():
                try:
                    goals.add(
                        actor=user,
                        center=center,
                        student=student,
                        text=form.cleaned_data["text"],
                        target_on=form.cleaned_data["target_on"],
                        project=form.cleaned_data["project"],
                        request=request,
                    )
                except goals.GoalError as error:
                    form.add_error(None, error.message)
                else:
                    messages.success(request, _("The goal is added."))
                    return redirect(here)
        else:
            form = GoalForm(center=center, student=student)
            goal = (
                Goal.objects.filter(
                    center=center, student=student, pk=request.POST.get("goal", "")
                ).first()
                if _is_uuid(request.POST.get("goal", ""))
                else None
            )
            if goal is None or not can(user, Action.SET_GOALS, goal):
                messages.error(request, _("That goal wasn't found."))
            else:
                try:
                    goals.set_status(goal, action, actor=user, request=request)
                except goals.GoalError as error:
                    messages.error(request, error.message)
                else:
                    messages.success(request, _("The goal is updated."))
                    return redirect(here)
    else:
        form = GoalForm(center=center, student=student)
    everything = list(Goal.objects.filter(center=center, student=student).select_related("project"))
    context = {
        "center": center,
        "student": student,
        "form": form,
        "title": title,
        "intro": intro,
        "open_goals": [g for g in everything if g.status == GoalStatus.OPEN],
        "closed_goals": sorted(
            (g for g in everything if g.status != GoalStatus.OPEN),
            key=lambda g: g.closed_at or g.created_at,
            reverse=True,
        ),
        "back": back,
        "crumbs": _crumbs(center),
        "today": timezone.now().date(),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/goals.html", context)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


@login_required
def my_goals(request: HttpRequest, slug: str) -> HttpResponse:
    """A student's own goals."""
    center = center_for(request, Action.VIEW_OWN_WRITING, slug)
    return _goal_page(
        request,
        center=center,
        student=signed_in(request),
        title=_("Your writing goals"),
        intro=_(
            "Write what you want to get better at, and tutors see it before they meet you. "
            "Mark it met when you've done it, or dropped if it no longer fits."
        ),
        here=reverse("records:my_goals", kwargs={"slug": slug}),
        back=(_("My writing"), reverse("records:my_writing", kwargs={"slug": slug})),
    )


@login_required
def student_goals(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    """A student's goals, for the tutors and directors who work with them."""
    membership = student_for(request, Action.SET_GOALS, slug, membership_id)
    center = membership.center
    return _goal_page(
        request,
        center=center,
        student=membership.user,
        title=_("Writing goals: %(name)s") % {"name": membership.user.display_name},
        intro=_(
            "The student can see these and change them. Open goals show in the brief before "
            "each session."
        ),
        here=reverse(
            "records:student_goals", kwargs={"slug": slug, "membership_id": membership.pk}
        ),
        back=(
            _("Their session records"),
            reverse(
                "records:student_history", kwargs={"slug": slug, "membership_id": membership.pk}
            ),
        ),
    )


def _timeline_page(
    request: HttpRequest,
    *,
    center: Center,
    student: User,
    own: bool,
    title: str,
    back: tuple[str, str],
) -> HttpResponse:
    groups = timeline.build(center, student, request.user, own=own)
    context = {
        "center": center,
        "student": student,
        "groups": groups,
        "own": own,
        "title": title,
        "back": back,
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "records/timeline.html", context)


@login_required
def my_timeline(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.VIEW_OWN_WRITING, slug)
    return _timeline_page(
        request,
        center=center,
        student=signed_in(request),
        own=True,
        title=_("Your writing across terms"),
        back=(_("My writing"), reverse("records:my_writing", kwargs={"slug": slug})),
    )


@login_required
def student_timeline(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    membership: Membership = student_for(request, Action.VIEW_RECORDS, slug, membership_id)
    center = membership.center
    record(
        AuditAction.TIMELINE_VIEWED,
        request=request,
        center=center,
        target=membership,
        student=str(membership.user_id),
    )
    return _timeline_page(
        request,
        center=center,
        student=membership.user,
        own=False,
        title=_("Across terms: %(name)s") % {"name": membership.user.display_name},
        back=(
            _("Their session records"),
            reverse(
                "records:student_history", kwargs={"slug": slug, "membership_id": membership.pk}
            ),
        ),
    )
