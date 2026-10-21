"""The director's page for API tokens and webhooks."""

from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, get_for_action
from sympraxi.integrations import tokens, webhooks
from sympraxi.integrations.models import SCOPES, ApiToken, WebhookDelivery, WebhookEndpoint
from sympraxi.privacy.audit import AuditAction, record


class TokenForm(forms.Form):
    name = forms.CharField(label=gettext_lazy("What it's for"), max_length=100)
    scopes = forms.MultipleChoiceField(
        label=gettext_lazy("What it may do"),
        choices=[(k, v) for k, v in SCOPES.items()],
        widget=forms.CheckboxSelectMultiple,
    )
    days = forms.ChoiceField(
        label=gettext_lazy("Expires"),
        choices=[
            ("30", gettext_lazy("In 30 days")),
            ("90", gettext_lazy("In 90 days")),
            ("365", gettext_lazy("In a year")),
            ("", gettext_lazy("Never")),
        ],
        initial="90",
        required=False,
    )


class WebhookForm(forms.Form):
    url = forms.URLField(
        label=gettext_lazy("Address to notify"),
        max_length=500,
        help_text=gettext_lazy("Must start with https:// and be reachable from the internet."),
    )
    description = forms.CharField(
        label=gettext_lazy("What it's for"), max_length=200, required=False
    )
    events = forms.MultipleChoiceField(
        label=gettext_lazy("Tell it about"),
        choices=[(name, name) for name in webhooks.EVENTS],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=gettext_lazy("Choose none to be told about everything."),
    )

    def clean_url(self) -> str:
        value: str = self.cleaned_data["url"]
        from django.conf import settings

        allow = getattr(settings, "SYMPRAXI_WEBHOOKS_ALLOW_PRIVATE", False)
        if urlsplit(value).scheme != "https" and not allow:
            raise forms.ValidationError(_("The address must start with https://."))
        return value


def _uuid(raw: str | None) -> UUID:
    try:
        return UUID(raw or "")
    except ValueError:
        return UUID(int=0)


@login_required
def home(request: HttpRequest, slug: str) -> HttpResponse:
    center = get_for_action(
        request.user, Action.MANAGE_INTEGRATIONS, Center.objects.all(), slug=slug
    )
    me = cast(User, request.user)
    here = reverse("integrations:home", kwargs={"slug": slug})
    action = request.POST.get("action") if request.method == "POST" else None
    token_form = TokenForm(request.POST if action == "token" else None)
    hook_form = WebhookForm(request.POST if action == "webhook" else None)
    shown_token: tuple[ApiToken, str] | None = None
    shown_secret: tuple[WebhookEndpoint, str] | None = None

    if action == "token" and token_form.is_valid():
        data = token_form.cleaned_data
        shown_token = tokens.create(
            center,
            me,
            name=data["name"],
            scopes=data["scopes"],
            days=int(data["days"]) if data["days"] else None,
            request=request,
        )
    elif action == "webhook" and hook_form.is_valid():
        data = hook_form.cleaned_data
        secret = webhooks.new_secret()
        endpoint = WebhookEndpoint.objects.create(
            center=center,
            url=data["url"],
            description=data["description"],
            events=sorted(data["events"]),
            secret_encrypted=webhooks.seal(secret),
            created_by=me,
        )
        record(
            AuditAction.WEBHOOK_SAVED,
            request=request,
            center=center,
            target=endpoint,
            what="created",
        )
        shown_secret = (endpoint, secret)
    elif action == "revoke":
        row = ApiToken.objects.filter(center=center, pk=_uuid(request.POST.get("token"))).first()
        if row is not None and tokens.revoke(row, by=me, request=request):
            messages.success(request, _("Token revoked."))
        return redirect(here)
    elif action in ("remove", "toggle", "ping"):
        chosen = WebhookEndpoint.objects.filter(
            center=center, pk=_uuid(request.POST.get("endpoint"))
        ).first()
        if chosen is not None:
            if action == "remove":
                record(
                    AuditAction.WEBHOOK_REMOVED,
                    request=request,
                    center=center,
                    target=chosen,
                    url=chosen.url,
                )
                chosen.delete()
                messages.success(request, _("Webhook removed."))
            elif action == "toggle":
                chosen.is_active = not chosen.is_active
                chosen.save(update_fields=["is_active"])
                record(
                    AuditAction.WEBHOOK_SAVED,
                    request=request,
                    center=center,
                    target=chosen,
                    what="on" if chosen.is_active else "off",
                )
            else:
                webhooks.ping(chosen)
                messages.success(request, _("A test notice is on its way. Check the list below."))
        return redirect(here)

    context: dict[str, Any] = {
        "center": center,
        "tokens": list(ApiToken.objects.filter(center=center).select_related("created_by")),
        "endpoints": list(WebhookEndpoint.objects.filter(center=center)),
        "deliveries": list(
            WebhookDelivery.objects.filter(endpoint__center=center).select_related("endpoint")[:20]
        ),
        "token_form": token_form,
        "hook_form": hook_form,
        "shown_token": shown_token,
        "shown_secret": shown_secret,
        "api_base": request.build_absolute_uri(f"/api/v1/centers/{slug}/"),
        "scopes": SCOPES,
        "crumbs": [(center.name, center.get_absolute_url())],
    }
    return render(request, "integrations/home.html", context)
