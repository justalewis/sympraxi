from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django_htmx.middleware import HtmxDetails

from sympraxi.centers.models import Center, Membership
from sympraxi.core.policy import Action, can, get_for_action, roles_in


@login_required
def center_detail(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(request.user, Action.VIEW_CENTER, Center.objects.all(), slug=slug)
    context = {
        "center": center,
        "my_roles": sorted(roles_in(request.user, center)),
        "can_view_members": can(request.user, Action.VIEW_MEMBERS, center),
        "can_view_audit_log": can(request.user, Action.VIEW_AUDIT_LOG, center),
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
    context = {"center": center, "page": page, "query": query}
    # A search typed into the box replaces only the results, not the whole page.
    template = "centers/_member_results.html" if request.htmx else "centers/member_list.html"
    return render(request, template, context)
