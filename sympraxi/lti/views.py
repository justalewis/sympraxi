"""The three LTI addresses: start a login, take the launch, and describe the tool."""

from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sympraxi.accounts import cas
from sympraxi.lti import launch as flow
from sympraxi.privacy.audit import AuditAction, record


def _base(request: HttpRequest) -> str:
    return str(request.build_absolute_uri("/")).rstrip("/")


def _fail(request: HttpRequest, problem: flow.LtiError) -> HttpResponse:
    record(AuditAction.LOGIN_FAILED, request=request, reason=f"lti_{problem.reason}")
    return HttpResponse(
        f"<!doctype html><title>Sign-in problem</title><p>{escape(str(problem.message))}</p>",
        status=400,
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def login_initiation(request: HttpRequest) -> HttpResponse:
    data = request.POST if request.method == "POST" else request.GET
    issuer, hint = data.get("iss", ""), data.get("login_hint", "")
    if not issuer or not hint:
        return HttpResponseBadRequest("Missing iss or login_hint.")
    try:
        platform = flow.find_platform(issuer, data.get("client_id", ""))
    except flow.LtiError as problem:
        return _fail(request, problem)
    state, nonce = flow.begin(platform)
    query: dict[str, Any] = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": platform.client_id,
        "redirect_uri": _base(request) + reverse("lti:launch"),
        "login_hint": hint,
        "state": state,
        "nonce": nonce,
    }
    if data.get("lti_message_hint"):
        query["lti_message_hint"] = data["lti_message_hint"]
    return HttpResponseRedirect(f"{platform.login_url}?{urlencode(query)}")


@csrf_exempt
@require_http_methods(["POST"])
def launch(request: HttpRequest) -> HttpResponse:
    try:
        platform, claims = flow.verify(
            request.POST.get("state", ""), request.POST.get("id_token", "")
        )
        user = flow.user_for(platform, claims, request)
    except flow.LtiError as problem:
        return _fail(request, problem)
    flow.join_center(user, platform)
    return cas.sign_in(request, user, platform.center.get_absolute_url())


def config(request: HttpRequest) -> JsonResponse:
    """The tool's description, which Canvas reads when a developer key is made from JSON."""
    base = _base(request)
    launch_url = base + reverse("lti:launch")
    return JsonResponse(
        {
            "title": settings.SYMPRAXI_SITE_NAME,
            "description": "Writing center scheduling and records",
            "oidc_initiation_url": base + reverse("lti:login"),
            "target_link_uri": launch_url,
            "scopes": [],
            "extensions": [
                {
                    "platform": "canvas.instructure.com",
                    "privacy_level": "public",
                    "settings": {
                        "placements": [
                            {
                                "placement": "course_navigation",
                                "message_type": "LtiResourceLinkRequest",
                                "target_link_uri": launch_url,
                                "text": settings.SYMPRAXI_SITE_NAME,
                            }
                        ]
                    },
                }
            ],
        }
    )
