from typing import Any

from allauth.mfa import stages
from django.http import HttpRequest

from sympraxi.accounts import sso


def sociallogin_of(login: Any) -> Any:
    """The SSO sign-in behind an allauth login in progress, or None."""
    return (login.signal_kwargs or {}).get("sociallogin")


class AuthenticateStage(stages.AuthenticateStage):
    """allauth's two-factor step, which every sign-in goes through, SSO included.

    The one exception: SYMPRAXI_TRUST_IDP_MFA is on and the identity provider's
    token proves it did multi-factor login (`sso.sociallogin_proves_mfa`).
    """

    def _should_handle(self, request: HttpRequest) -> bool:
        if sso.sociallogin_proves_mfa(sociallogin_of(self.login)):
            return False
        return super()._should_handle(request)
