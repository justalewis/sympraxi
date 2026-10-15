from collections.abc import Callable

from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _

from sympraxi.core import policy

# Pages a staff member can reach before setting up two-factor login:
# the account and 2FA screens themselves, logout, static files and health checks.
EXEMPT_PREFIXES = ("/accounts/", "/static/", "/healthz", "/source/")
STRONG_FACTORS = (Authenticator.Type.TOTP, Authenticator.Type.WEBAUTHN)


def needs_mfa_setup(user: policy.UserLike) -> bool:
    """True for staff and site admins who have no authenticator app or passkey yet."""
    if not user.is_authenticated or not policy.is_staff_member(user):
        return False
    return not Authenticator.objects.filter(user=user, type__in=STRONG_FACTORS).exists()


class RequireStaffMFAMiddleware:
    """Send tutors, front desk, directors and site admins to 2FA setup until they finish it.

    Staff can read student records, so a stolen password alone must not be enough.
    Students are not forced, but can turn 2FA on themselves.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            settings.SYMPRAXI_REQUIRE_STAFF_MFA
            and not request.path.startswith(EXEMPT_PREFIXES)
            and needs_mfa_setup(request.user)
        ):
            messages.info(
                request,
                _(
                    "Staff accounts need two-factor login. Set up an authenticator app "
                    "or a passkey to continue."
                ),
            )
            url = reverse("mfa_index") + "?" + urlencode({"next": request.get_full_path()})
            return HttpResponseRedirect(url)
        return self.get_response(request)
