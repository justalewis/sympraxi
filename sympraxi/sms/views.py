"""Turning text reminders on and off, and the provider's webhook for replies."""

from typing import cast
from xml.sax.saxutils import escape

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sympraxi.accounts.models import User
from sympraxi.sms import providers, service


class OptInForm(forms.Form):
    phone = forms.CharField(
        label=gettext_lazy("Mobile number"),
        max_length=30,
        help_text=gettext_lazy(
            "With the area code. Other countries: start with + and the country code."
        ),
    )
    agree = forms.BooleanField(
        required=False,
        label=gettext_lazy("I agree"),
    )


class CodeForm(forms.Form):
    code = forms.CharField(label=gettext_lazy("The six-digit code we texted you"), max_length=12)


@login_required
def sms_settings(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    consent = service.current_consent(user)
    action = request.POST.get("action") if request.method == "POST" else None
    opt_form = OptInForm(request.POST if action == "start" else None)
    code_form = CodeForm(request.POST if action == "confirm" else None)
    if action == "start" and opt_form.is_valid():
        try:
            service.start_opt_in(
                user,
                opt_form.cleaned_data["phone"],
                agreed=opt_form.cleaned_data["agree"],
                request=request,
            )
        except service.SmsError as problem:
            opt_form.add_error(None, str(problem))
        else:
            messages.success(request, _("We texted you a code. Enter it below to finish."))
            return redirect("sms:settings")
    elif action == "confirm" and consent is not None and code_form.is_valid():
        try:
            service.confirm(consent, code_form.cleaned_data["code"], request=request)
        except service.SmsError as problem:
            code_form.add_error("code", str(problem))
        else:
            messages.success(request, _("Done. You'll get text reminders."))
            return redirect("sms:settings")
    elif action == "resend" and consent is not None:
        try:
            service.resend_code(consent)
        except service.SmsError as problem:
            messages.error(request, str(problem))
        else:
            messages.success(request, _("We texted you a new code."))
        return redirect("sms:settings")
    elif action == "stop" and consent is not None:
        service.revoke(consent, via="web", request=request)
        messages.success(request, _("Text reminders are off."))
        return redirect("sms:settings")
    context = {
        "available": service.available(),
        "consent": consent,
        "masked": service.masked(consent) if consent else "",
        "opt_form": opt_form,
        "code_form": code_form,
        "consent_text": service.consent_text(),
    }
    return render(request, "account/sms.html", context)


def _twiml(text: str) -> HttpResponse:
    body = "<Response>" + (f"<Message>{escape(text)}</Message>" if text else "") + "</Response>"
    return HttpResponse(body, content_type="text/xml")


@csrf_exempt
@require_POST
def inbound(request: HttpRequest) -> HttpResponse:
    """The provider posts each reply here. It is checked against the provider's signature."""
    provider = providers.configured()
    if provider is None or not provider.verify_inbound(request):
        return HttpResponse(status=403)
    return _twiml(service.handle_reply(request.POST.get("From", ""), request.POST.get("Body", "")))
