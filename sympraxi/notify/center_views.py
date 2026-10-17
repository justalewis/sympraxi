"""Directors' pages for announcements and mass email."""

from typing import Any, cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.notify import news
from sympraxi.notify.forms import AnnouncementForm, MassEmailForm
from sympraxi.notify.models import Announcement, MassEmail
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.notifications import base_url

# How many recipients a sent email's page lists by name.
LISTED = 500


def _center(request: HttpRequest, action: Action, slug: str) -> Center:
    return get_for_action(request.user, action, Center.objects.all(), slug=slug)


def _crumbs(center: Center, *more: tuple[str, str]) -> list[tuple[str, str]]:
    return [(center.name, center.get_absolute_url()), *more]


# --- Announcements ------------------------------------------------------------------


@login_required
def announcements(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.MANAGE_ANNOUNCEMENTS, slug)
    context = {
        "center": center,
        "announcements": Announcement.objects.filter(center=center),
        "crumbs": _crumbs(center),
    }
    return render(request, "notify/announcement_list.html", context)


def _announcements_crumbs(center: Center) -> list[tuple[str, str]]:
    url = reverse("news:announcements", kwargs={"slug": center.slug})
    return _crumbs(center, (_("Announcements"), url))


@login_required
def announcement_create(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.MANAGE_ANNOUNCEMENTS, slug)
    form = AnnouncementForm(
        request.POST or None,
        instance=Announcement(center=center, created_by=cast(User, request.user)),
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Announcement saved."))
        return redirect("news:announcements", slug=slug)
    context = {
        "center": center,
        "form": form,
        "title": _("New announcement"),
        "crumbs": _announcements_crumbs(center),
    }
    return render(request, "notify/announcement_form.html", context)


@login_required
def announcement_edit(request: HttpRequest, slug: str, announcement_id: UUID) -> HttpResponse:
    announcement = get_for_action(
        request.user,
        Action.MANAGE_ANNOUNCEMENTS,
        Announcement.objects.filter(center__slug=slug).select_related("center"),
        pk=announcement_id,
    )
    center = announcement.center
    if request.method == "POST" and "delete" in request.POST:
        announcement.delete()
        messages.success(request, _("Announcement deleted."))
        return redirect("news:announcements", slug=slug)
    form = AnnouncementForm(request.POST or None, instance=announcement)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Announcement saved."))
        return redirect("news:announcements", slug=slug)
    context = {
        "center": center,
        "form": form,
        "announcement": announcement,
        "title": _("Change the announcement"),
        "crumbs": _announcements_crumbs(center),
    }
    return render(request, "notify/announcement_form.html", context)


# --- Mass email ---------------------------------------------------------------------


def _log_crumbs(center: Center) -> list[tuple[str, str]]:
    url = reverse("news:mass_email_log", kwargs={"slug": center.slug})
    return _crumbs(center, (_("Emails sent"), url))


@login_required
def mass_email(request: HttpRequest, slug: str) -> HttpResponse:
    """Write, then review (how many it reaches, and a test copy), then send."""
    center = _center(request, Action.SEND_MASS_EMAIL, slug)
    me = cast(User, request.user)
    form = MassEmailForm(request.POST or None, center=center)
    context: dict[str, Any] = {
        "center": center,
        "form": form,
        "crumbs": _crumbs(center),
        "title": _("Email people in the center"),
    }
    if request.method != "POST" or not form.is_valid() or "edit" in request.POST:
        return render(request, "notify/mass_email.html", context)
    reach = form.reach()
    data = form.cleaned_data
    if "send" in request.POST:
        mass = news.send(
            center,
            reach,
            subject=data["subject"],
            body=data["body"],
            sent_by=me,
            url_base=base_url(request),
        )
        count = mass.recipients.count()
        record(
            AuditAction.MASS_EMAIL_SENT,
            request=request,
            center=center,
            target=mass,
            audience=mass.audience,
            recipients=count,
            skipped=mass.skipped,
        )
        messages.success(
            request,
            ngettext(
                "Your email is on its way to %(n)s person.",
                "Your email is on its way to %(n)s people.",
                count,
            )
            % {"n": count},
        )
        return redirect("news:mass_email_detail", slug=slug, mass_email_id=mass.pk)
    if "test" in request.POST:
        test = MassEmail(
            center=center,
            subject=data["subject"],
            body=data["body"],
            audience=reach.label(),
            sent_by=me,
        )
        news.message(test, me, url_base=base_url(request), student_only=False).send()
        messages.info(request, _("A test copy is on its way to %(email)s.") % {"email": me.email})
    recipients, skipped = reach.split(center)
    context.update(
        {
            "title": _("Check the email before it goes"),
            "reach": reach,
            "count": recipients.count(),
            "skipped": skipped,
            "subject": data["subject"],
            "body": data["body"],
        }
    )
    return render(request, "notify/mass_email_review.html", context)


@login_required
def mass_email_log(request: HttpRequest, slug: str) -> HttpResponse:
    center = _center(request, Action.SEND_MASS_EMAIL, slug)
    sent = MassEmail.objects.filter(center=center).select_related("sent_by").order_by("-created_at")
    context = {"center": center, "sent": sent, "crumbs": _crumbs(center)}
    return render(request, "notify/mass_email_log.html", context)


@login_required
def mass_email_detail(request: HttpRequest, slug: str, mass_email_id: UUID) -> HttpResponse:
    mass = get_for_action(
        request.user,
        Action.SEND_MASS_EMAIL,
        MassEmail.objects.filter(center__slug=slug).select_related("center", "sent_by"),
        pk=mass_email_id,
    )
    recipients = mass.recipients.select_related("user").order_by("user__full_name", "user__email")
    context = {
        "center": mass.center,
        "mass": mass,
        "count": recipients.count(),
        "queued": recipients.filter(queued_at__isnull=False).count(),
        "listed": recipients[:LISTED],
        "more": max(0, recipients.count() - LISTED),
        "crumbs": _log_crumbs(mass.center),
    }
    return render(request, "notify/mass_email_detail.html", context)
