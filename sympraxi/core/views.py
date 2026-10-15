from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from sympraxi.centers.models import Membership


def home(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return render(request, "core/landing.html")
    memberships = (
        Membership.objects.filter(user=request.user, is_active=True, center__is_active=True)
        .select_related("center")
        .order_by("center__name", "role")
    )
    centers: dict[object, dict[str, object]] = {}
    for m in memberships:
        entry = centers.setdefault(m.center_id, {"center": m.center, "roles": []})
        entry["roles"].append(m.get_role_display())  # type: ignore[attr-defined]
    return render(request, "core/dashboard.html", {"centers": list(centers.values())})


@require_GET
@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness and database check for Docker and monitoring. Reveals nothing else."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)


@require_GET
def source_code(request: HttpRequest) -> HttpResponseRedirect:
    """AGPL section 13: every user can get the source of the version they are using."""
    return HttpResponseRedirect(settings.SYMPRAXI_SOURCE_CODE_URL)
