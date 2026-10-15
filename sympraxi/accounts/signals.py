from typing import Any

from allauth.account import signals as account_signals
from django.dispatch import receiver
from django.http import HttpRequest

from sympraxi.accounts import sso


@receiver(account_signals.user_logged_in)
def remember_idp_mfa(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    """Tell the staff 2FA gate whether this sign-in proved MFA at a trusted provider.

    Set or cleared on every sign-in, so it describes the current one only.
    """
    if sso.sociallogin_proves_mfa(kwargs.get("sociallogin")):
        request.session[sso.IDP_MFA_SESSION_KEY] = True
    else:
        request.session.pop(sso.IDP_MFA_SESSION_KEY, None)
