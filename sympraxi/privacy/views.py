from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.privacy.models import AuditEvent


@login_required
def audit_log(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_AUDIT_LOG, Center.objects.all(), slug=slug)
    # Record first, so this visit appears in the list the viewer is about to read.
    record(AuditAction.AUDIT_LOG_VIEWED, request=request, center=center, target=center)
    events = AuditEvent.objects.filter(center=center)
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    with timezone.override(center.time_zone):
        return render(request, "privacy/audit_log.html", {"center": center, "page": page})
