"""A student's record of disclosures: directors add to it, the student reads it."""

from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.privacy.forms import DisclosureForm
from sympraxi.privacy.models import Disclosure
from sympraxi.scheduling.timeutils import local_date


def entries(center: Center, student: User) -> QuerySet[Disclosure]:
    return (
        Disclosure.objects.filter(center=center, student=student)
        .select_related("recorded_by", "corrects")
        .order_by("-recorded_at")
    )


@login_required
def disclosures(request: HttpRequest, slug: str, membership_id: UUID) -> HttpResponse:
    membership = get_for_action(
        request.user,
        Action.RECORD_DISCLOSURES,
        Membership.objects.filter(center__slug=slug, role=Role.STUDENT).select_related(
            "center", "user"
        ),
        pk=membership_id,
    )
    center, student = membership.center, membership.user
    found = entries(center, student)
    today = local_date(timezone.now(), ZoneInfo(center.time_zone))
    form = DisclosureForm(request.POST or None, earlier=found, today=today)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.center = center
        entry.student = student
        entry.recorded_by = cast(User, request.user)
        entry.save()
        record(
            AuditAction.DISCLOSURE_RECORDED,
            request=request,
            center=center,
            target=entry,
            student=str(student.pk),
            basis=entry.basis,
            disclosed=entry.disclosed,
            corrects=str(entry.corrects_id) if entry.corrects_id else None,
        )
        messages.success(request, _("Added to the record of disclosures."))
        return redirect(request.path)
    if request.method == "GET":
        record(
            AuditAction.DISCLOSURES_VIEWED,
            request=request,
            center=center,
            target=membership,
            student=str(student.pk),
        )
    history = reverse(
        "records:student_history", kwargs={"slug": slug, "membership_id": membership_id}
    )
    context = {
        "center": center,
        "student": student,
        "entries": found,
        "form": form,
        "crumbs": [
            (center.name, center.get_absolute_url()),
            (student.display_name, history),
        ],
    }
    return render(request, "privacy/disclosures.html", context)


@login_required
def my_disclosures(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_OWN_WRITING, Center.objects.all(), slug=slug)
    context = {
        "center": center,
        "entries": entries(center, cast(User, request.user)),
        "crumbs": [
            (center.name, center.get_absolute_url()),
            (_("My writing"), reverse("records:my_writing", kwargs={"slug": slug})),
        ],
    }
    return render(request, "privacy/my_disclosures.html", context)
