from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from django import forms
from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext as _


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return bool(settings.SYMPRAXI_SIGNUP_OPEN)

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        allowed = settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS
        if allowed:
            domain = email.rsplit("@", 1)[-1].lower()
            if domain not in allowed:
                raise forms.ValidationError(
                    _("Please sign up with your college email address (%(domains)s).")
                    % {"domains": ", ".join("@" + d for d in allowed)}
                )
        return email

    def save_user(self, request: HttpRequest, user: Any, form: Any, commit: bool = True) -> Any:
        data = form.cleaned_data
        user.full_name = data.get("full_name", "")
        user.preferred_name = data.get("preferred_name", "")
        user.pronouns = data.get("pronouns", "")
        return super().save_user(request, user, form, commit=commit)
