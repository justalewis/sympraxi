"""The calendar feed itself, and the page where people turn theirs on, replace it or
turn it off."""

from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.notify import feeds
from sympraxi.notify.models import CalendarFeed
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.notifications import base_url

# Where a new feed's address waits to be shown once, after the redirect.
NEW_ADDRESS = "sympraxi_calendar_feed_address"

DEAD = (
    "This calendar address doesn't work anymore. Its owner may have replaced it or turned it off.\n"
)


def _dead() -> HttpResponse:
    response = HttpResponse(DEAD, status=404, content_type="text/plain; charset=utf-8")
    add_never_cache_headers(response)
    return response


def feed(request: HttpRequest, token: str) -> HttpResponse:
    """The calendar a subscribed app fetches. Never looks at who is signed in."""
    if request.method not in ("GET", "HEAD"):
        return _dead()
    found = feeds.open_feed(token)
    if found is None:
        return _dead()
    body = feeds.feed_body(found.user, base_url=base_url(request))
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    add_never_cache_headers(response)
    return response


def feed_address(request: HttpRequest, token: str) -> str:
    return request.build_absolute_uri(reverse("notify:feed", kwargs={"token": token}))


@login_required
def calendar(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if request.method == "POST":
        if request.POST.get("action") == "off":
            if feeds.stop_feed(user):
                record(AuditAction.CALENDAR_FEED_TURNED_OFF, request=request)
                messages.success(
                    request, _("Your calendar feed is off. Its address no longer works.")
                )
        else:
            replacing = CalendarFeed.objects.filter(user=user).exists()
            token = feeds.start_feed(user)
            record(
                AuditAction.CALENDAR_FEED_REPLACED
                if replacing
                else AuditAction.CALENDAR_FEED_STARTED,
                request=request,
            )
            request.session[NEW_ADDRESS] = feed_address(request, token)
        return redirect("notify:calendar")
    context = {
        "feed": CalendarFeed.objects.filter(user=user).first(),
        # Shown once: the address isn't stored anywhere it could be shown again.
        "address": request.session.pop(NEW_ADDRESS, None),
    }
    return render(request, "notify/calendar.html", context)
