"""Pages where directors add people to their center and turn roles on and off.

Every page here needs `center.manage_members`. The rules live in
`sympraxi/centers/people.py`; these views only collect what to do and say what
happened.
"""

import secrets
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from sympraxi.accounts import sso
from sympraxi.accounts.models import User
from sympraxi.centers import people
from sympraxi.centers.forms import ImportForm, PersonForm, RolesOffForm
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.notifications import base_url

MANAGE = Action.MANAGE_MEMBERS
# Where the import's preview waits for the director to confirm it.
SESSION_KEY = "sympraxi_people_import"
# How many rows the preview lists; the rest are counted.
PREVIEW_ROWS = 500


def _center(request: HttpRequest, slug: str) -> Center:
    return get_for_action(request.user, MANAGE, Center.objects.all(), slug=slug)


def _crumbs(center: Center) -> list[tuple[str, str]]:
    return [
        (center.name, center.get_absolute_url()),
        (_("People"), reverse("centers:members", kwargs={"slug": center.slug})),
    ]


def _members_url(center: Center) -> str:
    return reverse("centers:members", kwargs={"slug": center.slug})


def welcome_message(
    center: Center, user: User, roles: list[Role], *, url_base: str
) -> EmailMessage:
    """Says the person was added and how to sign in. No link that signs anyone in."""
    context = {
        "center": center,
        "person": user,
        "roles": ", ".join(str(Role(r).label) for r in roles),
        "url": url_base.rstrip("/") + reverse("account_login"),
        "has_sso": bool(sso.provider_apps()),
        "sso_only": sso.sso_only(),
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
    }
    body = render_to_string("centers/email/welcome.txt", context)
    subject = _("You've been added to %(center)s") % {"center": center.name}
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {subject}",
        body=body,
        to=[user.email],
        reply_to=[center.contact_email] if center.contact_email else None,
    )


def _send_welcomes(request: HttpRequest, center: Center, outcome: people.Outcome) -> int:
    url_base = base_url(request)
    users = User.objects.filter(pk__in=list(outcome.welcomed), is_active=True)
    found = [(u, outcome.welcomed[u.pk]) for u in users]

    def send() -> None:
        for user, roles in found:
            welcome_message(center, user, roles, url_base=url_base).send()

    transaction.on_commit(send)
    return len(found)


@login_required
def person_add(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, slug)
    form = PersonForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and form.person is not None:
        try:
            outcome = people.add_people(center, [form.person], request=request, via="person_form")
        except IntegrityError:
            form.add_error(None, _("Someone else changed this account just now. Try again."))
        else:
            if form.cleaned_data["welcome"]:
                _send_welcomes(request, center, outcome)
            if outcome.already:
                messages.info(
                    request,
                    _("%(email)s already has that role here.") % {"email": form.person.email},
                )
            else:
                messages.success(
                    request,
                    _("%(email)s is in the center as %(role)s.")
                    % {"email": form.person.email, "role": form.person.role.label.lower()},
                )
            return redirect(_members_url(center))
    context = {
        "center": center,
        "form": form,
        "crumbs": _crumbs(center),
        "title": _("Add a person"),
    }
    return render(request, "centers/person_add.html", context)


def _preview_context(center: Center, reading: people.Reading) -> dict[str, Any]:
    """What the preview shows: each row's role and whether it changes anything here."""
    # Email addresses are unique without regard to case, so compare them lowercased.
    emails = {row.email.lower() for row in reading.rows}
    existing = {
        (email, role): active
        for email, role, active in Membership.objects.filter(center=center)
        .annotate(email=Lower("user__email"))
        .filter(email__in=emails)
        .values_list("email", "role", "is_active")
    }
    shown: list[tuple[people.PersonRow, str]] = []
    counts = {"add": 0, "turn_on": 0, "already": 0}
    for row in reading.rows:
        state = existing.get((row.email.lower(), row.role))
        status = "add" if state is None else ("already" if state else "turn_on")
        counts[status] += 1
        if len(shown) < PREVIEW_ROWS:
            shown.append((row, status))
    return {
        "rows": shown,
        "more": max(0, len(reading.rows) - PREVIEW_ROWS),
        "counts": counts,
        "problems": reading.problems,
        "ready": len(reading.rows),
    }


@login_required
def people_import(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, slug)
    crumbs = _crumbs(center)
    title = _("Add people from a CSV file")
    if request.method == "POST" and "confirm" in request.POST:
        return _confirm_import(request, center)

    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        reading = people.read_csv(
            upload.read(people.MAX_BYTES + 1), default_role=form.default_role()
        )
        if reading.file_problem:
            form.add_error("file", reading.file_problem)
        else:
            key = secrets.token_urlsafe(16)
            request.session[SESSION_KEY] = {
                "key": key,
                "center": str(center.pk),
                "rows": [row.as_data() for row in reading.rows],
                "welcome": bool(form.cleaned_data["welcome"]),
                "problems": len(reading.problems),
            }
            context = {
                "center": center,
                "crumbs": crumbs,
                "title": _("Check the people before adding them"),
                "key": key,
                "welcome": form.cleaned_data["welcome"],
                **_preview_context(center, reading),
            }
            return render(request, "centers/import_preview.html", context)
    context = {"center": center, "form": form, "crumbs": crumbs, "title": title}
    return render(request, "centers/import.html", context)


def _confirm_import(request: HttpRequest, center: Center) -> HttpResponse:
    waiting = request.session.get(SESSION_KEY) or {}
    again = reverse("centers:import", kwargs={"slug": center.slug})
    if (
        not waiting
        or waiting.get("center") != str(center.pk)
        or not secrets.compare_digest(str(waiting.get("key", "")), request.POST.get("key", ""))
    ):
        messages.error(request, _("That preview is no longer waiting. Choose the file again."))
        return redirect(again)
    rows = [people.PersonRow.from_data(data) for data in waiting["rows"]]
    try:
        outcome = people.add_people(center, rows, request=request, via="import")
        record(
            AuditAction.PEOPLE_IMPORTED,
            request=request,
            center=center,
            rows=len(rows),
            skipped=waiting.get("problems", 0),
            added=outcome.added,
            turned_on=outcome.turned_on,
            already=outcome.already,
        )
    except IntegrityError:
        messages.error(
            request,
            _(
                "Someone else changed one of these accounts while you were checking the file, "
                "so nobody was added. Choose the file again."
            ),
        )
        return redirect(again)
    finally:
        request.session.pop(SESSION_KEY, None)
    text = outcome.summary()
    if waiting["welcome"] and outcome.welcomed:
        sent = _send_welcomes(request, center, outcome)
        text += " " + ngettext(
            "%(n)s person will get an email.", "%(n)s people will get an email.", sent
        ) % {"n": sent}
    messages.success(request, text)
    return redirect(_members_url(center))


@login_required
def person(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    """One person's roles in the center, to turn on and off or add to."""
    found = get_for_action(
        request.user,
        MANAGE,
        Membership.objects.filter(center__slug=slug).select_related("center", "user"),
        pk=membership_id,
    )
    center, user = found.center, found.user
    here = reverse("centers:person", kwargs={"slug": slug, "membership_id": membership_id})
    if request.method == "POST":
        if "add_role" in request.POST:
            role_value = request.POST.get("add_role", "")
            if role_value in Role.values:
                row = people.PersonRow(
                    line=1, email=user.email, full_name=user.full_name, role=Role(role_value)
                )
                outcome = people.add_people(center, [row], request=request, via="person_page")
                if outcome.added or outcome.turned_on:
                    messages.success(
                        request,
                        _("%(name)s is now %(role)s here too.")
                        % {"name": user.display_name, "role": Role(role_value).label.lower()},
                    )
            return redirect(here)
        try:
            chosen = UUID(request.POST.get("membership", ""))
        except ValueError:
            return redirect(here)
        membership = Membership.objects.filter(center=center, user=user, pk=chosen).first()
        if membership is not None:
            turning_on = request.POST.get("active") == "1"
            try:
                people.set_active(membership, turning_on, request=request)
            except people.CantTurnOff as refusal:
                messages.error(request, str(refusal))
            else:
                messages.success(
                    request,
                    (
                        _("%(role)s role turned on.")
                        if turning_on
                        else _("%(role)s role turned off.")
                    )
                    % {"role": membership.get_role_display()},
                )
        return redirect(here)
    roles = list(Membership.objects.filter(center=center, user=user).order_by("role"))
    held = {m.role for m in roles}
    context = {
        "center": center,
        "person": user,
        "roles": roles,
        "addable": [(value, label) for value, label in Role.choices if value not in held],
        "is_me": user.pk == request.user.pk,
        "crumbs": _crumbs(center),
        "title": user.display_name,
    }
    return render(request, "centers/person.html", context)


@login_required
def roles_off(request: HttpRequest, slug: str) -> HttpResponse:
    """Turn off one role for everyone, as at the end of a term."""
    center = _center(request, slug)
    form = RolesOffForm(request.POST or None)
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "crumbs": _crumbs(center),
        "title": _("Turn off a role for everyone"),
    }
    if request.method == "POST" and form.is_valid():
        role = Role(form.cleaned_data["role"])
        if "confirm" in request.POST:
            count = people.turn_off_role(center, role, request=request)
            messages.success(
                request,
                ngettext(
                    "Turned off %(n)s %(role)s role.", "Turned off %(n)s %(role)s roles.", count
                )
                % {"n": count, "role": role.label.lower()},
            )
            return redirect(_members_url(center))
        affected = people.active_in_role(center, role, keep=request.user)
        context.update(
            {
                "role": role,
                "count": affected.count(),
                "upcoming": people.with_upcoming(center, role, affected),
                "keeps_self": Membership.objects.filter(
                    center=center, role=role, user=cast(User, request.user), is_active=True
                ).exists(),
            }
        )
        return render(request, "centers/roles_off_confirm.html", context)
    return render(request, "centers/roles_off.html", context)
