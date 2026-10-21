"""The two CAS addresses: send the person to the college's sign-in, and take them back."""

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from sympraxi.accounts import cas
from sympraxi.privacy.audit import AuditAction, record

NEXT_KEY = "sympraxi_cas_next"


def _service(request: HttpRequest) -> str:
    """The address CAS sends the person back to. Built from the host's configured address."""
    base = str(request.build_absolute_uri("/")).rstrip("/")
    return base + reverse("accounts:cas_callback")


def cas_login(request: HttpRequest) -> HttpResponse:
    if not cas.configured():
        raise Http404
    target = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "/"
    request.session[NEXT_KEY] = target
    return HttpResponseRedirect(cas.login_url(_service(request)))


def cas_callback(request: HttpRequest) -> HttpResponse:
    if not cas.configured():
        raise Http404
    ticket = request.GET.get("ticket", "")
    target = request.session.pop(NEXT_KEY, "/")
    failure = HttpResponseRedirect(reverse("account_login"))
    if not ticket:
        return failure
    try:
        identity = cas.validate(_service(request), ticket)
        user = cas.user_for(identity, request)
    except cas.CasError as problem:
        record(
            AuditAction.LOGIN_FAILED,
            request=request,
            actor_label=request.GET.get("user", "")[:80],
            reason=f"cas_{problem.reason}",
        )
        messages.error(request, str(problem.message))
        return failure
    return cas.sign_in(request, user, target)
