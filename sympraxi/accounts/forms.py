from typing import Any

from django import forms
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User


class SignupForm(forms.Form):
    """Extra fields added to allauth's signup form. The adapter saves them."""

    full_name = forms.CharField(
        label=_("Full name"),
        max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    preferred_name = forms.CharField(
        label=_("Preferred name"),
        max_length=100,
        required=False,
        help_text=_("Optional. We'll use this instead of your full name wherever we can."),
        widget=forms.TextInput(attrs={"autocomplete": "nickname"}),
    )
    pronouns = forms.CharField(
        label=_("Pronouns"),
        max_length=50,
        required=False,
        help_text=_("Optional. For example: she/her, he/him, they/them."),
    )

    def signup(self, request: HttpRequest, user: object) -> None:
        # Required by allauth; the adapter has already saved these fields.
        pass


class ProfileForm(forms.ModelForm):
    """A person's own name, preferred name and pronouns."""

    class Meta:
        model = User
        fields = ["full_name", "preferred_name", "pronouns"]
        labels = {
            "full_name": _("Full name"),
            "preferred_name": _("Preferred name"),
            "pronouns": _("Pronouns"),
        }
        help_texts = {
            "preferred_name": _("We'll use this instead of your full name wherever we can."),
            "pronouns": _("For example: she/her, he/him, they/them."),
        }
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "preferred_name": forms.TextInput(attrs={"autocomplete": "nickname"}),
        }


class ReminderSettingsForm(forms.ModelForm):
    """Reminder emails before appointments, and survey emails after sessions.
    Only tutors see the tutoring choice."""

    class Meta:
        model = User
        fields = [
            "reminders_for_appointments",
            "reminders_for_tutoring",
            "survey_emails",
            "center_news_emails",
        ]

    def __init__(self, *args: Any, tutor: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not tutor:
            del self.fields["reminders_for_tutoring"]
