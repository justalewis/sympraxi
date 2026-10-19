"""Joining a video room, and a center's video settings."""

from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _

from sympraxi.core.policy import Action
from sympraxi.online import services
from sympraxi.online.models import VideoSettings
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.views import appointment_crumbs, appointment_for, center_for, signed_in


@login_required
def join(request: HttpRequest, slug: str, appointment_id: UUID) -> HttpResponse:
    """Send the person to their room with their token, if the room is open now."""
    appointment = appointment_for(request, Action.JOIN_VIDEO, slug, appointment_id)
    center = appointment.schedule.center
    user = signed_in(request)
    back = reverse(
        "scheduling:appointment", kwargs={"slug": slug, "appointment_id": appointment.pk}
    )
    if services.has_room(appointment):
        when = services.joining(appointment)
        if when.state == services.OPEN:
            record(
                AuditAction.VIDEO_JOINED,
                request=request,
                center=center,
                target=appointment,
                moderator=appointment.tutor_id == user.pk,
            )
            response = HttpResponseRedirect(services.join_url(appointment, user))
            # The address holds the person's token: not cached, and not passed on.
            add_never_cache_headers(response)
            response["Referrer-Policy"] = "no-referrer"
            return response
    else:
        when = None
    context = {
        "center": center,
        "appointment": appointment,
        "when": when,
        "back": back,
        "crumbs": appointment_crumbs(user, appointment),
    }
    with timezone.override(center.time_zone):
        return render(request, "online/unavailable.html", context)


class VideoSettingsForm(forms.ModelForm[VideoSettings]):
    class Meta:
        model = VideoSettings
        fields = ["is_on"]


@login_required
def video_settings(request: HttpRequest, slug: str) -> HttpResponse:
    center = center_for(request, Action.MANAGE_SCHEDULES, slug)
    current = services.settings_for(center)
    form = VideoSettingsForm(request.POST if request.method == "POST" else None, instance=current)
    if request.method == "POST" and form.is_valid():
        changed = bool(form.changed_data)
        form.save()
        if changed:
            record(
                AuditAction.VIDEO_SETTINGS_CHANGED,
                request=request,
                center=center,
                target=current,
                is_on=current.is_on,
            )
        messages.success(request, _("Video settings are saved."))
        return redirect(reverse("online:settings", kwargs={"slug": slug}))
    context = {
        "center": center,
        "form": form,
        "configured": services.configured(),
        "crumbs": [(center.name, reverse("centers:detail", kwargs={"slug": slug}))],
    }
    return render(request, "online/settings.html", context)
