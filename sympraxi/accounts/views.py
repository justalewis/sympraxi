from typing import cast

from allauth.account.views import LoginView
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from sympraxi.accounts.forms import ProfileForm, ReminderSettingsForm
from sympraxi.accounts.models import User
from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction, record


class LocalLoginView(LoginView):
    """Break-glass password sign-in for site administrators.

    With SYMPRAXI_SSO_ONLY on, the regular sign-in page shows only the SSO buttons.
    This page keeps the password form for the day the identity provider is down or
    misconfigured. It grants nothing extra: `AccountAdapter.pre_login()` refuses
    every non-SSO sign-in except a site administrator's, whichever page it came
    from, and two-factor login still applies.
    """

    extra_context = {"break_glass": True}


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Change your own name, preferred name and pronouns. Each change is audited,
    naming the fields that changed but not what they say."""
    user = cast(User, request.user)
    data = request.POST if request.method == "POST" else None
    form = ProfileForm(data, instance=user)
    if request.method == "POST" and form.is_valid():
        if form.has_changed():
            form.save()
            record(
                AuditAction.PROFILE_CHANGED,
                request=request,
                target=user,
                fields=sorted(form.changed_data),
            )
        messages.success(request, _("Saved."))
        return redirect("accounts:profile")
    return render(request, "account/profile.html", {"form": form})


@login_required
def reminders(request: HttpRequest) -> HttpResponse:
    """Turn reminder and survey emails on or off. Students have both on unless they
    turn them off; tutors have reminders off unless they turn them on."""
    user = cast(User, request.user)
    tutor = Membership.objects.filter(user=user, role=Role.TUTOR, is_active=True).exists()
    data = request.POST if request.method == "POST" else None
    form = ReminderSettingsForm(data, instance=user, tutor=tutor)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect("accounts:reminders")
    return render(request, "account/reminders.html", {"form": form})
