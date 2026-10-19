"""The room page, the document's history, and the editor service's internal address."""

import base64
import json
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sympraxi.core.policy import Action, can
from sympraxi.online import room, services
from sympraxi.online.models import RoomVersion
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.views import appointment_crumbs, appointment_for, signed_in


def _origin(url: str) -> str:
    """The scheme and host of an address, for the Content-Security-Policy."""
    scheme, _separator, rest = url.partition("://")
    return f"{scheme}://{rest.split('/', 1)[0]}"


@login_required
def room_page(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    appointment = appointment_for(request, Action.USE_ROOM, slug, appointment_id)
    center = appointment.schedule.center
    user = signed_in(request)
    back = reverse(
        "scheduling:appointment", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    document = room.document_for(appointment)
    versions_url = reverse(
        "online:room_versions", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    context: dict[str, Any] = {
        "center": center,
        "appointment": appointment,
        "back": back,
        "versions_url": versions_url,
        "crumbs": appointment_crumbs(user, appointment),
        "available": room.has_room(appointment),
        "state": room.state(appointment) if room.has_room(appointment) else "",
        "saved": room.render(document.content) if document else "",
        "saved_chat": room.chat_of(document.chat) if document else [],
    }
    response: HttpResponse
    if context["state"] == room.OPEN:
        url = room.collab_url(request.get_host(), secure=request.is_secure())
        record(
            AuditAction.ROOM_OPENED,
            request=request,
            center=center,
            target=appointment,
        )
        # Beside the document, the session's video room, if it has one and it is open now.
        video_origin = ""
        if (
            services.has_room(appointment)
            and can(user, Action.JOIN_VIDEO, appointment)
            and services.state(appointment) == services.OPEN
        ):
            context["video_url"] = services.join_url(appointment, user)
            video_origin = f"https://{settings.SYMPRAXI_JITSI_DOMAIN}"
            record(
                AuditAction.VIDEO_JOINED,
                request=request,
                center=center,
                target=appointment,
                moderator=appointment.tutor_id == user.pk,
            )
        context["config"] = {
            "url": url,
            "document": room.document_name(appointment),
            "token": room.token(appointment, user),
            "name": user.display_name,
            "versionsUrl": versions_url,
            "toolbar": {
                "bold": _("Bold"),
                "italic": _("Italic"),
                "heading": _("Heading"),
                "bullets": _("Bulleted list"),
                "numbers": _("Numbered list"),
                "undo": _("Undo"),
                "redo": _("Redo"),
            },
            "csrf": get_token(request),
            "labels": {
                "connected": _("Connected. What you write here, everyone in the session sees."),
                "connecting": _("Connecting…"),
                "offline": _("You're offline. Your changes are kept and shared when you're back."),
                "here": _("Here now: {names}"),
                "alone": _("No one else is here yet."),
                "saved": _("A version is saved."),
                "saveFailed": _("The version couldn't be saved."),
                "message": _("Message"),
                "editor": _("Shared document"),
            },
        }
        with timezone.override(center.time_zone):
            response = render(request, "online/room.html", context)
        # The page opens a WebSocket to the editor service; allow that one address.
        policy: dict[str, Any] = dict(settings.SECURE_CSP)
        policy["connect-src"] = [*cast(list[Any], policy["connect-src"]), _origin(url)]
        if video_origin:
            policy["frame-src"] = [video_origin]
        response._csp_config = policy  # type: ignore[attr-defined]
        return response
    with timezone.override(center.time_zone):
        return render(request, "online/room.html", context)


@login_required
def room_versions(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """GET lists the saved versions, or shows one (?version=); POST saves one now."""
    appointment = appointment_for(request, Action.USE_ROOM, slug, appointment_id)
    user = signed_in(request)
    center = appointment.schedule.center
    page = reverse("online:room_versions", kwargs={"slug": slug, "appointment_id": appointment.pk})
    if request.method == "POST":
        made = room.save_version(appointment, user) if room.has_room(appointment) else None
        if made is None:
            messages.error(request, _("There is nothing to save yet."))
        else:
            record(
                AuditAction.ROOM_VERSION_SAVED,
                request=request,
                center=center,
                target=appointment,
                version=str(made.pk),
            )
            messages.success(request, _("A version is saved."))
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"saved": made is not None}, status=200 if made else 409)
        return redirect(page)
    document = room.document_for(appointment)
    versions = list(document.versions.all()) if document else []
    shown: RoomVersion | None = None
    wanted = request.GET.get("version", "")
    if wanted:
        shown = next((v for v in versions if str(v.pk) == wanted), None)
    context = {
        "center": center,
        "appointment": appointment,
        "document": document,
        "versions": versions,
        "shown": shown,
        "shown_html": room.render(shown.content) if shown else "",
        "shown_chat": room.chat_of(shown.chat) if shown else [],
        "available": room.has_room(appointment),
        "room_url": reverse("online:room", kwargs={"slug": slug, "appointment_id": appointment.pk}),
        "page": page,
        "crumbs": appointment_crumbs(user, appointment),
    }
    with timezone.override(center.time_zone):
        return render(request, "online/room_versions.html", context)


# --- The editor service's internal address ---------------------------------------------


def _authorized(request: HttpRequest) -> bool:
    return room.secret_matches(request.headers.get("X-Collab-Secret", ""))


def _body(request: HttpRequest) -> dict[str, Any] | None:
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@csrf_exempt
@require_POST
def internal_load(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return HttpResponse(status=403)
    data = _body(request)
    if data is None or not isinstance(data.get("doc"), str):
        return HttpResponse(status=400)
    state = room.load_state(data["doc"])
    return JsonResponse({"state": base64.b64encode(state).decode() if state else None})


@csrf_exempt
@require_POST
def internal_store(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return HttpResponse(status=403)
    data = _body(request)
    if (
        data is None
        or not isinstance(data.get("doc"), str)
        or not isinstance(data.get("state"), str)
    ):
        return HttpResponse(status=400)
    try:
        saved = room.save(data["doc"], data["state"], data.get("content"), data.get("chat", []))
    except room.Refused as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)
    return JsonResponse({"saved": saved})
