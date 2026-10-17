"""Pages for forms: the builder for directors, and the pages where answers are given
and read.

The builder works without JavaScript. Moving a question is a button in its own
small form; the page reloads with the question in its new place. With htmx, the
list is swapped in place, focus stays on the button, and a status line says
what happened. Follow-up questions work the same way on every answering page
(see answering.py).
"""

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.centers.views import HtmxHttpRequest
from sympraxi.core.policy import Action, can, get_for_action
from sympraxi.forms import builder, display, services
from sympraxi.forms.answering import CONTINUE, Questions, follow_up_message
from sympraxi.forms.forms import IntroForm, NewFormForm, QuestionForm, places_free
from sympraxi.forms.models import (
    CHOICE_KINDS,
    Form,
    FormKind,
    FormVersion,
    Question,
    QuestionKind,
)
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records.views import student_for
from sympraxi.scheduling.views import appointment_for, center_for, signed_in


def _crumbs(center: Center, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url()), *extra]


def _list_crumb(center: Center) -> tuple[str, str]:
    return (_("Forms"), reverse("forms:list", kwargs={"slug": center.slug}))


def _form_crumbs(form: Form) -> list[tuple[str, str]]:
    url = reverse("forms:detail", kwargs={"slug": form.center.slug, "form_id": form.pk})
    return _crumbs(form.center, _list_crumb(form.center), (form.title, url))


def _detail_url(form: Form, anchor: str = "") -> str:
    url = reverse("forms:detail", kwargs={"slug": form.center.slug, "form_id": form.pk})
    return f"{url}#{anchor}" if anchor else url


def form_for(request: HttpRequest, slug: str, form_id: UUID) -> Form:
    return get_for_action(
        request.user,
        Action.MANAGE_FORMS,
        Form.objects.select_related("center", "schedule").filter(center__slug=slug),
        pk=form_id,
    )


def question_for(request: HttpRequest, slug: str, question_id: UUID) -> Question:
    return get_for_action(
        request.user,
        Action.MANAGE_FORMS,
        Question.objects.select_related("version__form__center", "version__form__schedule").filter(
            version__form__center__slug=slug
        ),
        pk=question_id,
    )


def describe(version: FormVersion) -> list[dict[str, Any]]:
    """Each question of a version with what the builder says about it."""
    questions = version.ordered_questions()
    by_key = {q.key: q for q in questions}
    rows = []
    for index, question in enumerate(questions):
        condition = ""
        if question.show_if_question is not None:
            source = by_key.get(question.show_if_question)
            labels = source.option_labels() if source else {}
            option = labels.get(question.show_if_option) if question.show_if_option else None
            condition = _("Shows only when “%(question)s” is answered “%(option)s”.") % {
                "question": source.label if source else "?",
                "option": option or "?",
            }
        rows.append(
            {
                "question": question,
                "number": index + 1,
                "first": index == 0,
                "last": index == len(questions) - 1,
                "options": [o.label for o in question.options.all()]
                if question.kind in CHOICE_KINDS
                else [],
                "condition": condition,
            }
        )
    return rows


# --- The builder ------------------------------------------------------------------


@login_required
def form_list(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_FORMS, slug)
    forms = Form.objects.filter(center=center).select_related("schedule")
    # Registration first, then the default intake form, then each schedule's.
    ordered = sorted(
        forms.annotate(answered=Count("responses")),
        key=lambda f: (f.kind != FormKind.REGISTRATION, f.schedule is not None, str(f.schedule)),
    )
    rows = [{"form": f, "published": f.published(), "draft": f.draft()} for f in ordered]
    context = {
        "center": center,
        "rows": rows,
        "can_add": bool(places_free(center)),
        "crumbs": _crumbs(center),
    }
    return render(request, "forms/form_list.html", context)


@login_required
def form_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_FORMS, slug)
    if not places_free(center):
        messages.info(request, _("This center has every form it can have. Edit one below."))
        return redirect("forms:list", slug=slug)
    form = NewFormForm(request.POST or None, center=center)
    if request.method == "POST" and form.is_valid():
        kind, schedule = form.kind_and_schedule()
        try:
            created = builder.create_form(
                actor=signed_in(request),
                center=center,
                kind=kind,
                schedule=schedule,
                request=request,
            )
        except builder.BuilderError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, _("Form added. Add its questions, then publish it."))
            return redirect(_detail_url(created))
    context = {
        "center": center,
        "form": form,
        "title": _("Add a form"),
        "intro": _(
            "A registration form is asked once, the first time a student books. An intake "
            "form is asked each time a student books. A schedule with its own intake form "
            "uses it; every other schedule uses the default one."
        ),
        "crumbs": _crumbs(center, _list_crumb(center)),
        "submit": _("Add the form"),
        "back": reverse("forms:list", kwargs={"slug": slug}),
    }
    return render(request, "scheduling/form_page.html", context)


def _detail_action(request: HttpRequest, form: Form) -> tuple[str, bool, str]:
    """Carry out one of the detail page's buttons. Returns (message, ok, anchor)."""
    actor = signed_in(request)
    action = request.POST.get("action", "")
    draft = form.draft()
    try:
        if action == "start_draft":
            draft = builder.start_draft(actor=actor, form=form)
            return _("Draft started. Change it, then publish it."), True, "draft"
        if action in ("stop", "start"):
            builder.set_active(actor=actor, form=form, active=action == "start", request=request)
            if action == "start":
                return _("These questions are asked again."), True, ""
            return (
                _("These questions aren't asked anymore. Answers already given are kept."),
                True,
                "",
            )
        if draft is None:
            return _("There's no draft to change. Press “Make changes” first."), False, ""
        if action in ("up", "down"):
            question_id = _uuid(request.POST.get("question"))
            question = draft.questions.filter(pk=question_id).first() if question_id else None
            if question is None:
                return _("That question isn't in the draft anymore."), False, ""
            builder.move_question(draft=draft, question=question, up=action == "up")
            message = (
                _("Moved “%(label)s” up.") if action == "up" else _("Moved “%(label)s” down.")
            ) % {"label": question.label}
            return message, True, f"question-{question.pk}"
        if action == "intro":
            intro = IntroForm(request.POST, instance=draft)
            if intro.is_valid():
                builder.save_intro(draft=draft, intro=intro.cleaned_data["intro"])
                return _("Introduction saved."), True, "draft"
            return " ".join(str(e) for e in intro.errors.get("intro", [])), False, "draft"
        if action == "discard":
            builder.discard_draft(form=form)
            return _("Draft thrown away."), True, ""
    except builder.BuilderError as exc:
        return exc.message, False, ""
    return "", False, ""


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value or "")
    except ValueError:
        return None


@login_required
def form_detail(request: HtmxHttpRequest, slug: str, form_id: UUID) -> HttpResponse:
    """A form's versions and its draft: the questions, in order, with buttons to change them."""
    form = form_for(request, slug, form_id)
    status = ""
    if request.method == "POST":
        message, ok, anchor = _detail_action(request, form)
        if not request.htmx:
            if message:
                (messages.success if ok else messages.warning)(request, message)
            return redirect(_detail_url(form, anchor))
        status = message
    draft = form.draft()
    published = form.published()
    versions = (
        form.versions.filter(published_at__isnull=False)
        .select_related("published_by")
        .annotate(answered=Count("responses"))
        .order_by("-number")
    )
    context = {
        "center": form.center,
        "form": form,
        "draft": draft,
        "published": published,
        "versions": versions,
        "draft_rows": describe(draft) if draft else [],
        "published_rows": describe(published) if published and not draft else [],
        "problems": builder.publish_problems(draft) if draft else [],
        "intro_form": IntroForm(instance=draft) if draft else None,
        "is_registration": form.kind == FormKind.REGISTRATION,
        "status": status,
        "crumbs": _crumbs(form.center, _list_crumb(form.center)),
    }
    return render(request, "forms/form_detail.html", context)


def _question_initial(question: Question | None) -> dict[str, Any]:
    if question is None:
        return {"kind": "short_text"}
    condition = ""
    if question.show_if_question is not None and question.show_if_option is not None:
        condition = f"{question.show_if_question.hex}:{question.show_if_option.hex}"
    return {
        "label": question.label,
        "help_text": question.help_text,
        "kind": question.kind,
        "required": question.required,
        "is_demographic": question.is_demographic,
        "shown_to_staff": question.shown_to_staff,
        "condition": condition,
    }


def _question_page(
    request: HttpRequest, form: Form, draft: FormVersion, question: Question | None
) -> HttpResponse:
    locked = question is not None and builder.kind_locked(question)
    qform = QuestionForm(
        request.POST if request.method == "POST" else None,
        initial=_question_initial(question),
        draft=draft,
        question=question,
        kind_locked=locked,
    )
    if request.method == "POST" and qform.is_valid():
        try:
            saved = builder.save_question(
                draft=draft, question=question, data=qform.question_data()
            )
        except builder.BuilderError as exc:
            qform.add_error(None, exc.message)
        else:
            messages.success(request, _("Question saved to the draft."))
            return redirect(_detail_url(form, f"question-{saved.pk}"))
    context = {
        "center": form.center,
        "form": form,
        "draft": draft,
        "question": question,
        "qform": qform,
        "option_rows": qform.option_rows(),
        "kind_locked": locked,
        "is_yes_no": question is not None and question.kind == QuestionKind.YES_NO,
        "title": _("Add a question") if question is None else _("Change a question"),
        "crumbs": _form_crumbs(form),
        "back": _detail_url(form, f"question-{question.pk}" if question else "draft"),
    }
    return render(request, "forms/question_form.html", context)


@login_required
def question_create(request: HttpRequest, slug: str, form_id: UUID) -> HttpResponse:
    form = form_for(request, slug, form_id)
    draft = form.draft()
    if draft is None:
        messages.info(request, _("Press “Make changes” to start a draft first."))
        return redirect(_detail_url(form))
    return _question_page(request, form, draft, None)


@login_required
def question_edit(request: HttpRequest, slug: str, question_id: UUID) -> HttpResponse:
    question = question_for(request, slug, question_id)
    form = question.version.form
    if question.version.is_published:
        messages.info(
            request, _("That version is published and can't change. Press “Make changes” first.")
        )
        return redirect(_detail_url(form))
    return _question_page(request, form, question.version, question)


@login_required
def question_remove(request: HttpRequest, slug: str, question_id: UUID) -> HttpResponse:
    question = question_for(request, slug, question_id)
    form = question.version.form
    draft = question.version
    if draft.is_published:
        return redirect(_detail_url(form))
    error = ""
    if request.method == "POST":
        try:
            builder.remove_question(draft=draft, question=question)
        except builder.BuilderError as exc:
            error = exc.message
        else:
            messages.success(request, _("Question removed from the draft."))
            return redirect(_detail_url(form, "draft"))
    context = {
        "center": form.center,
        "form": form,
        "question": question,
        "followers": builder.dependents(draft, question),
        "was_published": builder.kind_locked(question),
        "error": error,
        "crumbs": _form_crumbs(form),
        "back": _detail_url(form, f"question-{question.pk}"),
    }
    return render(request, "forms/question_remove.html", context)


def changes(before: FormVersion | None, after: FormVersion) -> dict[str, list[str]]:
    """What a draft changes from the published version, by question key."""
    old = {q.key: q for q in before.ordered_questions()} if before else {}
    new = after.ordered_questions()
    keys = {q.key for q in new}
    changed = []
    for question in new:
        earlier = old.get(question.key)
        if earlier is None:
            continue
        same = (
            earlier.label == question.label
            and earlier.help_text == question.help_text
            and earlier.required == question.required
            and earlier.shown_to_staff == question.shown_to_staff
            and earlier.show_if_option == question.show_if_option
            and [(o.key, o.label) for o in earlier.options.all()]
            == [(o.key, o.label) for o in question.options.all()]
        )
        if not same:
            changed.append(question.label)
    old_order = [k for k in old if k in keys]
    new_order = [q.key for q in new if q.key in old]
    return {
        "added": [q.label for q in new if q.key not in old],
        "removed": [q.label for k, q in old.items() if k not in keys],
        "changed": changed,
        "moved": [_("Questions are in a new order.")] if old_order != new_order else [],
    }


@login_required
def publish(request: HttpRequest, slug: str, form_id: UUID) -> HttpResponse:
    form = form_for(request, slug, form_id)
    draft = form.draft()
    if draft is None:
        messages.info(request, _("There's no draft to publish."))
        return redirect(_detail_url(form))
    problems = builder.publish_problems(draft)
    error = ""
    if request.method == "POST" and not problems:
        try:
            builder.publish(actor=signed_in(request), draft=draft, request=request)
        except builder.BuilderError as exc:
            error = exc.message
        else:
            messages.success(
                request,
                _("Published version %(n)s. Everyone answering from now on sees it.")
                % {"n": draft.number},
            )
            return redirect(_detail_url(form))
    context = {
        "center": form.center,
        "form": form,
        "draft": draft,
        "published": form.published(),
        "problems": problems,
        "changes": changes(form.published(), draft),
        "error": error,
        "crumbs": _form_crumbs(form),
        "back": _detail_url(form, "draft"),
    }
    return render(request, "forms/publish.html", context)


@login_required
def preview(request: HttpRequest, slug: str, form_id: UUID) -> HttpResponse:
    """The form as people see it, to try out. Nothing entered here is saved."""
    form = form_for(request, slug, form_id)
    number = request.GET.get("version", "")
    if number.isdigit():
        version = form.versions.filter(number=int(number)).first()
    else:
        version = form.draft() or form.published()
    if version is None:
        messages.info(request, _("That version doesn't exist."))
        return redirect(_detail_url(form))
    posted = request.POST if request.method == "POST" else None
    questions = Questions(version, prefix="preview", data=posted)
    result = ""
    status = ""
    if posted is not None:
        if CONTINUE in posted or questions.needs_another_look:
            if CONTINUE not in posted:
                status = follow_up_message(_("Try it"))
            questions = questions.again()
        elif questions.is_valid():
            result = _("Those answers would be accepted. Nothing was saved.")
    context = {
        "center": form.center,
        "form": form,
        "version": version,
        "questions": questions,
        "result": result,
        "form_status": status or questions.status,
        "crumbs": _form_crumbs(form),
        "back": _detail_url(form),
    }
    return render(request, "forms/preview.html", context)


# --- Answering ----------------------------------------------------------------------


@login_required
def my_registration(request: HttpRequest, slug: str) -> HttpResponse:
    """A student's own registration answers, to read and change."""
    center = center_for(request, Action.ANSWER_OWN_FORMS, slug)
    student = signed_in(request)
    version = services.registration_version(center)
    latest = services.latest_registration(center, student)
    questions = None
    status = ""
    if version is not None:
        posted = request.POST if request.method == "POST" else None
        questions = Questions(
            version, prefix="registration", data=posted, initial=services.values_of(latest)
        )
        if posted is not None:
            if CONTINUE in posted or questions.needs_another_look:
                if CONTINUE not in posted:
                    status = follow_up_message(_("Save your answers"))
                questions = questions.again()
            elif questions.is_valid():
                services.save_response(
                    version=version,
                    values=questions.values(),
                    answered_by=student,
                    student=student,
                    request=request,
                )
                messages.success(request, _("Your answers are saved."))
                return redirect("forms:my_registration", slug=slug)
    context = {
        "center": center,
        "version": version,
        "questions": questions,
        "latest": display.show(latest) if latest and version is None else None,
        "saved_at": latest.created_at if latest else None,
        "form_status": status or (questions.status if questions else ""),
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "forms/my_registration.html", context)


@login_required
def intake(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """A student's intake answers for one of their appointments, until it starts."""
    appointment = appointment_for(request, Action.ANSWER_OWN_FORMS, slug, appointment_id)
    student = signed_in(request)
    center = appointment.schedule.center
    version = services.intake_version(appointment.schedule)
    latest = services.latest_intake(appointment, student)
    still_open = appointment.is_active and appointment.starts_at > timezone.now()
    questions = None
    status = ""
    appointment_url = reverse(
        "scheduling:appointment", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    if version is not None and still_open:
        posted = request.POST if request.method == "POST" else None
        questions = Questions(
            version, prefix="intake", data=posted, initial=services.values_of(latest)
        )
        if posted is not None:
            if CONTINUE in posted or questions.needs_another_look:
                if CONTINUE not in posted:
                    status = follow_up_message(_("Save your answers"))
                questions = questions.again()
            elif questions.is_valid():
                services.save_response(
                    version=version,
                    values=questions.values(),
                    answered_by=student,
                    student=student,
                    appointment=appointment,
                    request=request,
                )
                messages.success(request, _("Your answers are saved. Your tutor will see them."))
                return redirect(appointment_url)
    context = {
        "center": center,
        "appointment": appointment,
        "version": version,
        "questions": questions,
        "still_open": still_open,
        "latest": display.show(latest) if latest else None,
        "form_status": status or (questions.status if questions else ""),
        "appointment_url": appointment_url,
        "crumbs": _crumbs(
            center,
            (
                _("Your appointments"),
                reverse("scheduling:my_appointments", kwargs={"slug": slug}),
            ),
            (_("Appointment"), appointment_url),
        ),
    }
    with timezone.override(center.time_zone):
        return render(request, "forms/intake.html", context)


@login_required
def student_registration(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    """A student's registration answers, for staff: as much as each role may see."""
    membership = student_for(request, Action.VIEW_REGISTRATION, slug, membership_id)
    center = membership.center
    user = request.user
    shown = display.registration_for(user, membership)
    sees_all = can(user, Action.VIEW_ALL_REGISTRATION, membership)
    earlier = []
    if sees_all:
        earlier = [display.show(r) for r in display.registration_history(membership)[1:]]
    responses = ([shown] if shown else []) + earlier
    if responses:
        record(
            AuditAction.ANSWERS_VIEWED,
            request=request,
            center=center,
            target=membership,
            students=[str(membership.user_id)],
            responses=[str(r.response.pk) for r in responses],
            where="registration page",
        )
    context = {
        "center": center,
        "membership": membership,
        "student": membership.user,
        "shown": shown,
        "earlier": earlier,
        "sees_all": sees_all,
        "crumbs": _crumbs(center),
    }
    with timezone.override(center.time_zone):
        return render(request, "forms/student_registration.html", context)
