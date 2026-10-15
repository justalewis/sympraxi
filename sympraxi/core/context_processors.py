from django.conf import settings
from django.http import HttpRequest

from sympraxi.accounts import sso


def site(request: HttpRequest) -> dict[str, object]:
    sso_only = sso.sso_only()
    return {
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        # Password sign-in and sign-up are hidden (not only refused) when SSO-only.
        "SSO_ONLY": sso_only,
        "LOCAL_SIGNUP_OPEN": settings.SYMPRAXI_SIGNUP_OPEN and not sso_only,
    }
