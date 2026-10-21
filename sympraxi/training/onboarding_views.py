"""Cohorts, mentors, sign-offs, observation checklists and the observations themselves."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.forms import DateInput
from sympraxi.training import onboarding
from sympraxi.training.models import (
    SCORES,
    Cohort,
    CohortMember,
    Criterion,
    Module,
    Observation,
    Template,
)
from sympraxi.training.views import _crumbs, _me, _swap, _uuid


class CohortForm(forms.ModelForm):
    class Meta:
        model = Cohort
        fields = ["name", "starts_on", "required_observations"]
        widgets = {"starts_on": DateInput()}


class TemplateForm(forms.ModelForm):
    class Meta:
        model = Template
        fields = ["title", "is_active"]


class CriterionForm(forms.ModelForm):
    class Meta:
        model = Criterion
        fields = ["text"]


class StartObservationForm(forms.Form):
    template = forms.ChoiceField(label=gettext_lazy("Checklist"))
    observed_on = forms.DateField(label=gettext_lazy("Day you observed"), widget=DateInput())

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.templates = {
            str(t.pk): t for t in Template.objects.filter(center=center, is_active=True)
        }
        cast(forms.ChoiceField, self.fields["template"]).choices = [
            (pk, t.title) for pk, t in self.templates.items()
        ]


def _user_by_id(candidates: list[User], raw: str | None) -> User | None:
    return next((u for u in candidates if str(u.pk) == (raw or "")), None)


# --- Cohorts ----------


@login_required
def cohorts(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TRAINING, Center.objects.all(), slug=slug)
    form = CohortForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        cohort = form.save(commit=False)
        cohort.center = center
        cohort.save()
        record(
            AuditAction.ONBOARDING_CHANGED,
            request=request,
            center=center,
            target=cohort,
            what="cohort created",
        )
        return redirect(reverse("training:cohort", kwargs={"slug": slug, "cohort_id": cohort.pk}))
    context = {
        "center": center,
        "form": form,
        "cohorts": list(Cohort.objects.filter(center=center).prefetch_related("members")),
        "crumbs": _crumbs(center),
    }
    return render(request, "training/cohorts.html", context)


@login_required
def cohort_detail(request: HttpRequest, slug: str, cohort_id: UUID) -> HttpResponse:
    cohort = get_for_action(
        request.user,
        Action.MANAGE_TRAINING,
        Cohort.objects.select_related("center").filter(center__slug=slug),
        pk=cohort_id,
    )
    center = cohort.center
    here = reverse("training:cohort", kwargs={"slug": slug, "cohort_id": cohort.pk})
    people = onboarding.addable(cohort)
    mentors = onboarding.mentors_for(center)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add":
                person = _user_by_id(people, request.POST.get("person"))
                if person is None:
                    raise onboarding.OnboardingError(_("Choose someone from the list."))
                onboarding.add_member(
                    cohort,
                    person,
                    _user_by_id(mentors, request.POST.get("mentor")),
                    request=request,
                )
            elif action in ("mentor", "remove", "complete"):
                member = CohortMember.objects.get(
                    pk=_uuid(request.POST.get("member")), cohort=cohort
                )
                if action == "mentor":
                    onboarding.set_mentor(
                        member, _user_by_id(mentors, request.POST.get("mentor")), request=request
                    )
                elif action == "remove":
                    onboarding.remove_member(member, request=request)
                else:
                    onboarding.complete(member, by=_me(request), request=request)
                    messages.success(request, _("Onboarding is marked complete."))
            elif action == "settings":
                cohort.is_active = request.POST.get("is_active") == "on"
                cohort.save(update_fields=["is_active"])
        except CohortMember.DoesNotExist:
            messages.error(request, _("That person isn't in this cohort."))
        except onboarding.OnboardingError as problem:
            messages.error(request, str(problem))
        return redirect(here)
    statuses = [onboarding.status_of(m) for m in cohort.members.select_related("person", "mentor")]
    context = {
        "center": center,
        "cohort": cohort,
        "statuses": statuses,
        "people": people,
        "mentors": mentors,
        "crumbs": _crumbs(
            center, (_("Cohorts"), reverse("training:cohorts", kwargs={"slug": slug}))
        ),
    }
    return render(request, "training/cohort.html", context)


# --- Checklists ----------


@login_required
def templates(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TRAINING, Center.objects.all(), slug=slug)
    form = TemplateForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.center = center
        template.save()
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=template,
            what="checklist created",
        )
        return redirect(
            reverse("training:template", kwargs={"slug": slug, "template_id": template.pk})
        )
    context = {
        "center": center,
        "form": form,
        "templates": list(Template.objects.filter(center=center).prefetch_related("criteria")),
        "crumbs": _crumbs(center),
    }
    return render(request, "training/templates.html", context)


@login_required
def template_edit(request: HttpRequest, slug: str, template_id: UUID) -> HttpResponse:
    template = get_for_action(
        request.user,
        Action.MANAGE_TRAINING,
        Template.objects.select_related("center").filter(center__slug=slug),
        pk=template_id,
    )
    center = template.center
    here = reverse("training:template", kwargs={"slug": slug, "template_id": template.pk})
    action = request.POST.get("action") if request.method == "POST" else None
    form = TemplateForm(request.POST if action == "save" else None, instance=template)
    criterion_form = CriterionForm(request.POST if action == "add" else None)
    if action == "save" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect(here)
    if action == "add" and criterion_form.is_valid():
        criterion = criterion_form.save(commit=False)
        criterion.template = template
        criterion.position = (template.criteria.aggregate(m=Max("position"))["m"] or 0) + 1
        criterion.save()
        return redirect(here)
    if action in ("up", "down", "delete"):
        rows = list(template.criteria.all())
        index = next(
            (n for n, c in enumerate(rows) if str(c.pk) == request.POST.get("criterion")), None
        )
        if index is not None:
            if action == "delete":
                if rows[index].ratings.exists():
                    messages.error(
                        request,
                        _("That item has been used in an observation, so it can't be deleted."),
                    )
                else:
                    rows[index].delete()
            else:
                _swap(rows, index, -1 if action == "up" else 1)
        return redirect(here)
    context = {
        "center": center,
        "template": template,
        "form": form,
        "criterion_form": criterion_form,
        "criteria": list(template.criteria.all()),
        "crumbs": _crumbs(center),
    }
    return render(request, "training/template_edit.html", context)


# --- Mentors ----------


@login_required
def mentees(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MENTOR_TUTORS, Center.objects.all(), slug=slug)
    me = _me(request)
    members = CohortMember.objects.filter(
        cohort__center=center, cohort__is_active=True
    ).select_related("person", "cohort", "mentor")
    if not can(me, Action.MANAGE_TRAINING, center):
        members = members.filter(mentor=me)
    context = {
        "center": center,
        "statuses": [onboarding.status_of(m) for m in members],
        "crumbs": _crumbs(center),
    }
    return render(request, "training/mentees.html", context)


@login_required
def mentee(request: HttpRequest, slug: str, member_id: UUID) -> HttpResponse:
    member = get_for_action(
        request.user,
        Action.MENTOR_TUTORS,
        CohortMember.objects.select_related("cohort__center", "person", "mentor").filter(
            cohort__center__slug=slug
        ),
        pk=member_id,
    )
    center = member.center
    me = _me(request)
    here = reverse("training:mentee", kwargs={"slug": slug, "member_id": member.pk})
    start_form = StartObservationForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") == "observe"
        else None,
        center=center,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "sign_off":
                module = Module.objects.get(
                    pk=_uuid(request.POST.get("module")), center=center, is_published=True
                )
                onboarding.sign_off(
                    member, module, by=me, note=request.POST.get("note", ""), request=request
                )
                messages.success(request, _("Signed off."))
                return redirect(here)
            if action == "observe" and start_form.is_valid():
                observation = onboarding.start_observation(
                    start_form.templates[start_form.cleaned_data["template"]],
                    member.person,
                    me,
                    start_form.cleaned_data["observed_on"],
                    request=request,
                )
                return redirect(
                    reverse(
                        "training:observation",
                        kwargs={"slug": slug, "observation_id": observation.pk},
                    )
                )
        except Module.DoesNotExist:
            messages.error(request, _("That module isn't published."))
            return redirect(here)
        except onboarding.OnboardingError as problem:
            messages.error(request, str(problem))
            return redirect(here)
    context = {
        "center": center,
        "member": member,
        "status": onboarding.status_of(member),
        "start_form": start_form,
        "observations": list(
            Observation.objects.filter(tutor=member.person, center=center).select_related(
                "template", "observer"
            )
        ),
        "crumbs": _crumbs(center),
        "today": timezone.localdate(),
    }
    return render(request, "training/mentee.html", context)


# --- Observations ----------


@login_required
def observation(request: HttpRequest, slug: str, observation_id: UUID) -> HttpResponse:
    found = (
        Observation.objects.select_related("center", "template", "tutor", "observer")
        .filter(center__slug=slug, pk=observation_id)
        .first()
    )
    if found is None:
        raise Http404
    me = _me(request)
    observer = can(me, Action.OBSERVE, found)
    editing = observer and not found.is_submitted
    reading = (
        observer
        or can(me, Action.MANAGE_TRAINING, found)
        or (found.is_submitted and found.tutor_id == me.pk)
    )
    if not reading:
        # Raises the 403 or 404 the policy calls for.
        get_for_action(
            request.user, Action.OBSERVE, Observation.objects.filter(pk=found.pk), pk=found.pk
        )
    here = reverse("training:observation", kwargs={"slug": slug, "observation_id": found.pk})
    criteria = list(found.template.criteria.all())
    ratings = {r.criterion_id: r for r in found.ratings.all()}
    if request.method == "POST":
        if not editing:
            raise PermissionDenied
        scores = {k[2:]: str(v) for k, v in request.POST.items() if k.startswith("s_")}
        comments = {k[2:]: str(v) for k, v in request.POST.items() if k.startswith("c_")}
        try:
            onboarding.save_observation(
                found,
                scores,
                comments,
                request.POST.get("notes", ""),
                submit=request.POST.get("action") == "submit",
                request=request,
            )
        except onboarding.OnboardingError as problem:
            messages.error(request, str(problem))
        else:
            messages.success(
                request,
                _("Submitted.") if request.POST.get("action") == "submit" else _("Saved."),
            )
        return redirect(here)
    rows = [{"criterion": c, "rating": ratings.get(c.pk)} for c in criteria]
    context = {
        "center": found.center,
        "observation": found,
        "rows": rows,
        "scores": SCORES,
        "editing": editing,
        "crumbs": _crumbs(found.center),
    }
    return render(request, "training/observation.html", context)
