"""Tutor training: the tutor's pages, the director's builder, and the onboarding table."""

from typing import Any, cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.training import progress
from sympraxi.training.forms import HoursForm, ItemForm, ModuleForm, QuestionForm
from sympraxi.training.models import (
    Attempt,
    Choice,
    Item,
    ItemKind,
    Module,
    Observation,
    ObservationStatus,
    Question,
)


def _me(request: HttpRequest) -> User:
    return cast(User, request.user)


def _uuid(raw: str | None) -> UUID:
    try:
        return UUID(raw or "")
    except ValueError:
        return UUID(int=0)


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [
        (center.name, center.get_absolute_url()),
        (_("Training"), reverse("training:home", kwargs={"slug": center.slug})),
        *extra,
    ]


# --- The tutor's side ----------


@login_required
def home(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.TAKE_TRAINING, Center.objects.all(), slug=slug)
    me = _me(request)
    rows = progress.statuses(me, center)
    hours = progress.training_hours(center, [me.pk]).get(me.pk)
    required = center.training_hours_required
    context: dict[str, Any] = {
        "center": center,
        "modules": rows,
        "hours": hours or 0,
        "required_hours": required,
        "required_done": all(r.complete for r in rows if r.module.is_required),
        "can_manage": can(me, Action.MANAGE_TRAINING, center),
        "observations": list(
            Observation.objects.filter(tutor=me, center=center, status=ObservationStatus.SUBMITTED)
            .select_related("template")
            .order_by("-observed_on")
        ),
        "crumbs": [(center.name, center.get_absolute_url())],
    }
    return render(request, "training/home.html", context)


@login_required
def module_page(request: HttpRequest, slug: str, module_id: UUID) -> HttpResponse:
    module = get_for_action(
        request.user,
        Action.TAKE_TRAINING,
        Module.objects.select_related("center").filter(center__slug=slug),
        pk=module_id,
    )
    me = _me(request)
    done = progress.done_item_ids(me, [module.pk])
    items = list(module.items.all())
    context = {
        "center": module.center,
        "module": module,
        "items": [(i, i.pk in done) for i in items],
        "crumbs": _crumbs(module.center),
    }
    return render(request, "training/module.html", context)


@login_required
def item_page(request: HttpRequest, slug: str, item_id: UUID) -> HttpResponse:
    item = get_for_action(
        request.user,
        Action.TAKE_TRAINING,
        Item.objects.select_related("module__center").filter(module__center__slug=slug),
        pk=item_id,
    )
    center = item.module.center
    me = _me(request)
    attempt: Attempt | None = None
    if request.method == "POST":
        if item.kind == ItemKind.QUIZ:
            answers = {k[2:]: str(v) for k, v in request.POST.items() if k.startswith("q_")}
            attempt = progress.submit_quiz(me, item, answers, request=request)
        elif request.POST.get("action") == "done":
            progress.mark_done(me, item, request=request)
            messages.success(request, _("Marked as done."))
            return redirect(
                reverse("training:module", kwargs={"slug": slug, "module_id": item.module_id})
            )
    siblings = list(item.module.items.all())
    index = next((n for n, s in enumerate(siblings) if s.pk == item.pk), 0)
    questions = list(item.questions.prefetch_related("choices")) if item.is_quiz else []
    wrong: set[str] = set()
    if attempt is not None:
        wrong = {
            str(q.pk)
            for q in questions
            if not any(
                str(c.pk) == attempt.answers.get(str(q.pk)) and c.is_correct
                for c in q.choices.all()
            )
        }
    context: dict[str, Any] = {
        "center": center,
        "item": item,
        "module": item.module,
        "questions": questions,
        "attempt": attempt,
        "wrong": wrong,
        "done": item.pk in progress.done_item_ids(me, [item.module_id]),
        "tries": Attempt.objects.filter(item=item, person=me).count() if item.is_quiz else 0,
        "next_item": siblings[index + 1] if index + 1 < len(siblings) else None,
        "crumbs": _crumbs(
            center,
            (
                item.module.title,
                reverse("training:module", kwargs={"slug": slug, "module_id": item.module_id}),
            ),
        ),
    }
    return render(request, "training/item.html", context)


# --- The director's side ----------


@login_required
def manage(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TRAINING, Center.objects.all(), slug=slug)
    here = reverse("training:manage", kwargs={"slug": slug})
    hours_form = HoursForm(request.POST if request.method == "POST" else None, center=center)
    if request.method == "POST" and hours_form.is_valid():
        center.training_hours_required = hours_form.cleaned_data["training_hours_required"]
        center.save(update_fields=["training_hours_required"])
        record(
            AuditAction.TRAINING_SETTINGS_CHANGED,
            request=request,
            center=center,
            target=center,
            hours=str(center.training_hours_required or ""),
        )
        messages.success(request, _("Saved."))
        return redirect(here)
    modules = list(Module.objects.filter(center=center).prefetch_related("items"))
    context = {
        "center": center,
        "modules": modules,
        "hours_form": hours_form,
        "crumbs": _crumbs(center),
    }
    return render(request, "training/manage.html", context)


@login_required
def module_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.MANAGE_TRAINING, Center.objects.all(), slug=slug)
    form = ModuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        module = form.save(commit=False)
        module.center = center
        module.position = (
            Module.objects.filter(center=center).aggregate(m=Max("position"))["m"] or 0
        ) + 1
        module.save()
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=module,
            what="module created",
        )
        messages.success(request, _("Module saved. Add its pages and quizzes below."))
        return redirect(
            reverse("training:module_edit", kwargs={"slug": slug, "module_id": module.pk})
        )
    context = {
        "center": center,
        "form": form,
        "title": _("New module"),
        "crumbs": _crumbs(center),
    }
    return render(request, "training/form.html", context)


def _module_for_edit(request: HttpRequest, slug: str, module_id: UUID) -> Module:
    return get_for_action(
        request.user,
        Action.MANAGE_TRAINING,
        Module.objects.select_related("center").filter(center__slug=slug),
        pk=module_id,
    )


def _swap(items: list[Any], index: int, step: int) -> None:
    other = index + step
    if 0 <= other < len(items):
        a, b = items[index], items[other]
        a.position, b.position = b.position, a.position
        if a.position == b.position:  # equal positions can't be told apart; spread them
            for n, obj in enumerate(items):
                obj.position = n
            a, b = items[index], items[other]
            a.position, b.position = b.position, a.position
        a.save(update_fields=["position"])
        b.save(update_fields=["position"])


@login_required
def module_edit(request: HttpRequest, slug: str, module_id: UUID) -> HttpResponse:
    module = _module_for_edit(request, slug, module_id)
    center = module.center
    here = reverse("training:module_edit", kwargs={"slug": slug, "module_id": module.pk})
    action = request.POST.get("action") if request.method == "POST" else None
    form = ModuleForm(request.POST if action == "save" else None, instance=module)
    item_form = ItemForm(request.POST if action == "add_item" else None)
    if action == "save" and form.is_valid():
        form.save()
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=module,
            what="module saved",
            published=module.is_published,
        )
        messages.success(request, _("Module saved."))
        return redirect(here)
    if action == "add_item" and item_form.is_valid():
        item = item_form.save(commit=False)
        item.module = module
        item.position = (module.items.aggregate(m=Max("position"))["m"] or 0) + 1
        item.save()
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=item,
            what="item added",
        )
        messages.success(request, _("Added."))
        if item.is_quiz:
            return redirect(
                reverse("training:item_edit", kwargs={"slug": slug, "item_id": item.pk})
            )
        return redirect(here)
    if action in ("up", "down", "delete_item"):
        items = list(module.items.all())
        index = next(
            (n for n, i in enumerate(items) if str(i.pk) == request.POST.get("item")), None
        )
        if index is not None:
            if action == "delete_item":
                gone = items[index]
                record(
                    AuditAction.TRAINING_CONTENT_CHANGED,
                    request=request,
                    center=center,
                    target=module,
                    what="item deleted",
                    title=gone.title,
                )
                gone.delete()
            else:
                _swap(items, index, -1 if action == "up" else 1)
        return redirect(here)
    context = {
        "center": center,
        "module": module,
        "form": form,
        "item_form": item_form,
        "items": list(module.items.prefetch_related("questions")),
        "crumbs": _crumbs(center),
    }
    return render(request, "training/module_edit.html", context)


@login_required
def item_edit(request: HttpRequest, slug: str, item_id: UUID) -> HttpResponse:
    item = get_for_action(
        request.user,
        Action.MANAGE_TRAINING,
        Item.objects.select_related("module__center").filter(module__center__slug=slug),
        pk=item_id,
    )
    center = item.module.center
    here = reverse("training:item_edit", kwargs={"slug": slug, "item_id": item.pk})
    back = reverse("training:module_edit", kwargs={"slug": slug, "module_id": item.module_id})
    action = request.POST.get("action") if request.method == "POST" else None
    form = ItemForm(request.POST if action == "save" else None, instance=item)
    question_form = QuestionForm(request.POST if action == "add_question" else None)
    if action == "save" and form.is_valid():
        form.save()
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=item,
            what="item saved",
        )
        messages.success(request, _("Saved."))
        return redirect(back if not item.is_quiz else here)
    if action == "add_question" and item.is_quiz and question_form.is_valid():
        position = (item.questions.aggregate(m=Max("position"))["m"] or 0) + 1
        question = Question.objects.create(
            item=item, prompt=question_form.cleaned_data["prompt"].strip(), position=position
        )
        for n, (text, right) in enumerate(question_form.choices()):
            Choice.objects.create(question=question, text=text, is_correct=right, position=n)
        record(
            AuditAction.TRAINING_CONTENT_CHANGED,
            request=request,
            center=center,
            target=item,
            what="question added",
        )
        messages.success(request, _("Question added."))
        return redirect(here)
    if action == "delete_question":
        deleted = Question.objects.filter(
            item=item, pk=_uuid(request.POST.get("question"))
        ).delete()
        if deleted[0]:
            record(
                AuditAction.TRAINING_CONTENT_CHANGED,
                request=request,
                center=center,
                target=item,
                what="question deleted",
            )
        return redirect(here)
    context = {
        "center": center,
        "item": item,
        "module": item.module,
        "form": form,
        "question_form": question_form if item.is_quiz else None,
        "questions": list(item.questions.prefetch_related("choices")) if item.is_quiz else [],
        "back": back,
        "crumbs": _crumbs(center),
    }
    return render(request, "training/item_edit.html", context)


@login_required
def onboarding(request: HttpRequest, slug: str) -> HttpResponse:
    """Every tutor and front desk person, module by module, with their training hours."""
    center = get_for_action(request.user, Action.MANAGE_TRAINING, Center.objects.all(), slug=slug)
    rows = progress.onboarding_table(center)
    modules = list(Module.objects.filter(center=center, is_published=True))
    context = {
        "center": center,
        "rows": [
            {
                "person": r.person,
                "modules": r.modules,
                "hours": r.hours,
                "hours_ok": progress.hours_met(center, r.hours),
                "modules_ok": r.required_done(),
            }
            for r in rows
        ],
        "modules": modules,
        "required_hours": center.training_hours_required,
        "crumbs": _crumbs(center),
    }
    return render(request, "training/onboarding.html", context)
