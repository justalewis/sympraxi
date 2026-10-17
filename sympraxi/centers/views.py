from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django_htmx.middleware import HtmxDetails

from sympraxi.centers.models import Center, Membership
from sympraxi.core.policy import Action, can, get_for_action, roles_in
from sympraxi.forms.models import Form, FormKind


@login_required
def center_detail(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_CENTER, Center.objects.all(), slug=slug)
    context = {
        "center": center,
        "my_roles": sorted(roles_in(request.user, center)),
        "can_view_members": can(request.user, Action.VIEW_MEMBERS, center),
        "can_view_audit_log": can(request.user, Action.VIEW_AUDIT_LOG, center),
        "can_view_schedules": can(request.user, Action.VIEW_SCHEDULES, center),
        "can_book_own": can(request.user, Action.BOOK_OWN, center),
        "can_tutor": can(request.user, Action.VIEW_OWN_TUTORING, center),
        "can_manage_schedules": can(request.user, Action.MANAGE_SCHEDULES, center),
        "can_see_own_writing": can(request.user, Action.VIEW_OWN_WRITING, center),
        "can_see_records_to_write": can(request.user, Action.VIEW_RECORD_STATUS, center),
        "can_write_records": can(request.user, Action.WRITE_RECORDS, center),
        "can_manage_records": can(request.user, Action.MANAGE_RECORDS, center),
        "can_mark_attendance": can(request.user, Action.MARK_ATTENDANCE, center),
        "can_manage_blocks": can(request.user, Action.MANAGE_BLOCKS, center),
        "can_manage_forms": can(request.user, Action.MANAGE_FORMS, center),
        "asks_registration": can(request.user, Action.ANSWER_OWN_FORMS, center)
        and Form.objects.filter(
            center=center,
            kind=FormKind.REGISTRATION,
            is_active=True,
            versions__published_at__isnull=False,
        ).exists(),
    }
    return render(request, "centers/center_detail.html", context)


class HtmxHttpRequest(HttpRequest):
    htmx: HtmxDetails


@login_required
def member_list(request: HtmxHttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_MEMBERS, Center.objects.all(), slug=slug)
    query = request.GET.get("q", "").strip()
    members = Membership.objects.filter(center=center).select_related("user")
    if query:
        members = members.filter(
            Q(user__full_name__icontains=query)
            | Q(user__preferred_name__icontains=query)
            | Q(user__email__icontains=query)
        )
    page = Paginator(members.order_by("user__full_name", "role"), 50).get_page(
        request.GET.get("page")
    )
    context = {
        "center": center,
        "page": page,
        "query": query,
        "can_view_records": can(request.user, Action.VIEW_RECORDS, center),
    }
    # A search typed into the box replaces only the results, not the whole page.
    template = "centers/_member_results.html" if request.htmx else "centers/member_list.html"
    return render(request, template, context)
