from django.conf import settings
from django.db import connection
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import render
from django.template import loader
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import requires_csrf_token
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


def too_many_requests(request: HttpRequest) -> HttpResponse:
    """allauth's answer when a rate limit is reached (`handler429` in sympraxi/urls.py).

    Every limit that answers this way counts over one minute, so a minute is
    always long enough to wait.
    """
    response = render(request, "429.html", status=429)
    response["Retry-After"] = "60"
    return response


@require_GET
def source_code(request: HttpRequest) -> HttpResponseRedirect:
    """AGPL section 13: every user can get the source of the version they are using."""
    return HttpResponseRedirect(settings.SYMPRAXI_SOURCE_CODE_URL)


@requires_csrf_token
def server_error(request: HttpRequest) -> HttpResponse:
    """The 500 page in the site's layout (settings: handler500 in urls.py).

    Rendered without the request, so no context processor runs: one that reads
    the session or the database could fail the same way the request did.
    """
    page = loader.get_template("500.html").render({"SITE_NAME": settings.SYMPRAXI_SITE_NAME})
    return HttpResponseServerError(page)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """A failed CSRF check, in the site's layout (settings.CSRF_FAILURE_VIEW).

    Django has already logged the reason; the page doesn't repeat it.
    """
    return render(request, "403_csrf.html", status=403)


@require_GET
def set_language(request: HttpRequest, code: str) -> HttpResponse:
    """Remember the chosen language in a cookie and go back to the page the link was on.

    A plain link rather than Django's form post, so pages carry no per-response CSRF
    token (which would make otherwise identical pages differ). Changing the language is
    harmless to do for someone else.
    """
    from django.http import HttpResponseRedirect
    from django.utils.http import url_has_allowed_host_and_scheme

    back = request.headers.get("referer", "")
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}, request.is_secure()):
        back = "/"
    response = HttpResponseRedirect(back)
    if code in dict(settings.LANGUAGES):
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            code,
            max_age=settings.LANGUAGE_COOKIE_AGE,
            samesite="Lax",
            secure=request.is_secure(),
        )
    return response
