from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.mfa.adapter import DefaultMFAAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.base.constants import AuthError
from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _

from sympraxi.accounts import encryption, sso
from sympraxi.privacy.audit import AuditAction, record

MFA_STAGE = "allauth.mfa.stages.AuthenticateStage"


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        # Local sign-up only. SSO sign-up asks SocialAccountAdapter.is_open_for_signup.
        return bool(settings.SYMPRAXI_SIGNUP_OPEN) and not sso.sso_only()

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

    def pre_login(self, request: HttpRequest, user: Any, **kwargs: Any) -> HttpResponse | None:
        """With SYMPRAXI_SSO_ONLY, only site administrators may sign in without SSO.

        This covers passwords, passkeys and email-confirmation sign-ins, whichever
        page they come from, so hiding the form is not the only thing stopping them.
        """
        signal_kwargs = kwargs.get("signal_kwargs") or {}
        signed_in_by_sso = signal_kwargs.get("sociallogin") or signal_kwargs.get("cas")
        if sso.sso_only() and not signed_in_by_sso and not user.is_superuser:
            record(
                AuditAction.LOGIN_FAILED,
                request=request,
                actor=user,
                reason=sso.LOCAL_LOGIN_OFF.reason,
            )
            messages.error(request, sso.LOCAL_LOGIN_OFF.message)
            return HttpResponseRedirect(reverse("account_login"))
        return super().pre_login(request, user, **kwargs)

    def get_login_stages(self) -> list[str]:
        # Same step, plus one exception: a trusted provider's proven MFA (sso.py).
        return [
            "sympraxi.accounts.stages.AuthenticateStage" if stage == MFA_STAGE else stage
            for stage in super().get_login_stages()
        ]


class MFAAdapter(DefaultMFAAdapter):
    """Encrypts the TOTP secret and the recovery-code seed before allauth stores
    them. allauth's own adapter stores them as they are (encryption.py)."""

    def encrypt(self, text: str) -> str:
        return encryption.encrypt(text)

    def decrypt(self, encrypted_text: str) -> str:
        return encryption.decrypt(encrypted_text)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Single sign-on through the providers in settings.SYMPRAXI_SSO_PROVIDERS.

    The checks themselves are in `sympraxi.accounts.sso`.
    """

    def list_apps(self, request: HttpRequest, provider: Any = None, client_id: Any = None) -> Any:
        # Only the providers built from the environment. An app saved in the database
        # (from a shell, say) is ignored, so a client secret never needs to live there.
        apps = super().list_apps(request, provider=provider, client_id=client_id)
        return [app for app in apps if app.pk is None]

    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any) -> bool:
        # SYMPRAXI_SSO_ONLY closes local sign-up, not this.
        return bool(settings.SYMPRAXI_SIGNUP_OPEN)

    def authenticate_by_email(self, sociallogin: Any) -> Any:
        # allauth looks for an account with the same address before calling
        # pre_social_login(), so the rules have to be applied here as well.
        if sso.check(sociallogin) is not None:
            return None
        sso.mark_email_verified(sociallogin)
        return super().authenticate_by_email(sociallogin)

    def pre_social_login(self, request: HttpRequest, sociallogin: Any) -> None:
        rejection = sso.check(sociallogin)
        provider_id = sociallogin.account.provider
        if (
            rejection is None
            and sociallogin.account.pk is None
            and sociallogin.is_existing
            and SocialAccount.objects.filter(user=sociallogin.user, provider=provider_id).exists()
        ):
            # Matched by address, but the account is linked to someone else at this
            # provider. A college can hand an old address to a new person; they must
            # not inherit the records.
            rejection = sso.ALREADY_LINKED
        if rejection is not None:
            self._reject(request, sociallogin, rejection)
        sso.mark_email_verified(sociallogin)
        if sociallogin.is_existing:
            sso.fill_student_id(sociallogin.user, sso.id_token_claims(sociallogin), request)

    def on_authentication_error(
        self,
        request: HttpRequest,
        provider: Any,
        error: Any = None,
        exception: Any = None,
        extra_context: Any = None,
    ) -> None:
        # The provider's answer didn't hold up: an ID token from another tenant or
        # issuer, a bad state, an error from the provider. Canceling isn't a failure.
        if error != AuthError.CANCELLED:
            record(
                AuditAction.LOGIN_FAILED,
                request=request,
                provider=getattr(provider, "sub_id", ""),
                reason=f"provider_{error or AuthError.UNKNOWN}",
            )
        super().on_authentication_error(
            request, provider, error=error, exception=exception, extra_context=extra_context
        )

    def _reject(self, request: HttpRequest, sociallogin: Any, rejection: sso.Rejection) -> None:
        provider_id = sociallogin.account.provider
        record(
            AuditAction.LOGIN_FAILED,
            request=request,
            actor_label=sso.asserted_email(sso.id_token_claims(sociallogin)),
            provider=provider_id,
            reason=rejection.reason,
        )
        messages.error(
            request, str(rejection.message) % {"provider": sso.provider_name(provider_id)}
        )
        connecting = sociallogin.state.get("process") == "connect"
        url = reverse("socialaccount_connections" if connecting else "account_login")
        raise ImmediateHttpResponse(HttpResponseRedirect(url))

    def populate_user(self, request: HttpRequest, sociallogin: Any, data: dict[str, Any]) -> Any:
        user = super().populate_user(request, sociallogin, data)
        claims = sso.id_token_claims(sociallogin)
        name = claims.get("name") or " ".join(
            part for part in (claims.get("given_name"), claims.get("family_name")) if part
        )
        user.full_name = str(name or user.email.split("@")[0])[:200]
        sent = sso.student_id_in(claims)
        if sent and not type(user).objects.filter(student_id=sent).exists():
            user.student_id = sent
        return user
