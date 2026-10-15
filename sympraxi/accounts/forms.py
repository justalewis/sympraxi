from django import forms
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _


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
