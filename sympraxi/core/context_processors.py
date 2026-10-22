from django.conf import settings
from django.http import HttpRequest

from sympraxi.accounts import sso


def site(request: HttpRequest) -> dict[str, object]:
    sso_only = sso.sso_only()
    return {
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
        # Password sign-in and sign-up are hidden (not only refused) when SSO-only.
        "SSO_ONLY": sso_only,
        # A CAS server is set up, so the sign-in page offers it beside any other provider.
        "CAS_ENABLED": bool(settings.SYMPRAXI_CAS_SERVER_URL),
        "SAML_ENABLED": bool(settings.SYMPRAXI_SAML_IDP_SSO_URL),
        "LOCAL_SIGNUP_OPEN": settings.SYMPRAXI_SIGNUP_OPEN and not sso_only,
    }
