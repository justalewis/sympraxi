"""The three SAML addresses: start a sign-in, take the response, and describe this service."""

from django.contrib import messages
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from sympraxi.accounts import cas, saml
from sympraxi.privacy.audit import AuditAction, record


@require_GET
def saml_login(request: HttpRequest) -> HttpResponse:
    if not saml.configured():
        raise Http404
    target = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "/"
    try:
        return HttpResponseRedirect(saml.begin(request, target))
    except saml.SamlError as problem:
        messages.error(request, str(problem.message))
        return HttpResponseRedirect(reverse("account_login"))


@csrf_exempt  # The identity provider posts from its own site; the signed response is the proof.
@never_cache
def saml_acs(request: HttpRequest) -> HttpResponse:
    if not saml.configured():
        raise Http404
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    failure = HttpResponseRedirect(reverse("account_login"))
    try:
        identity, target = saml.consume(request)
        user = saml.user_for(identity, request)
    except saml.SamlError as problem:
        record(AuditAction.LOGIN_FAILED, request=request, reason=f"saml_{problem.reason}")
        messages.error(request, str(problem.message))
        return failure
    return cas.sign_in(request, user, target)


@require_GET
def saml_metadata(request: HttpRequest) -> HttpResponse:
    if not saml.configured():
        raise Http404
    return HttpResponse(saml.metadata(request), content_type="application/samlmetadata+xml")
