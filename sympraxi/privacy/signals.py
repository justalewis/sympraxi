"""Audit sign-ins and account-security changes, wherever they happen."""

from typing import Any

from allauth.account import signals as account_signals
from allauth.mfa import signals as mfa_signals
from django.contrib.auth import signals as auth_signals
from django.dispatch import receiver

from sympraxi.privacy.audit import AuditAction, record


@receiver(auth_signals.user_logged_in)
def on_login(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    record(AuditAction.LOGIN, request=request, actor=user)


@receiver(auth_signals.user_logged_out)
def on_logout(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    if user is not None:
        record(AuditAction.LOGOUT, request=request, actor=user)


@receiver(auth_signals.user_login_failed)
def on_login_failed(
    sender: Any, credentials: dict[str, Any], request: Any = None, **kw: Any
) -> None:
    # Django has already masked the password. Keep only the address that was tried.
    attempted = str(credentials.get("email") or credentials.get("username") or "")
    record(AuditAction.LOGIN_FAILED, request=request, actor_label=attempted)


@receiver(account_signals.password_changed)
@receiver(account_signals.password_set)
def on_password_changed(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    record(AuditAction.PASSWORD_CHANGED, request=request, actor=user)


@receiver(account_signals.password_reset)
def on_password_reset(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    record(AuditAction.PASSWORD_RESET, request=request, actor=user)


@receiver(account_signals.email_changed)
def on_email_changed(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    record(AuditAction.EMAIL_CHANGED, request=request, actor=user)


@receiver(mfa_signals.authenticator_added)
def on_mfa_added(sender: Any, request: Any, user: Any, authenticator: Any, **kw: Any) -> None:
    record(AuditAction.MFA_ADDED, request=request, actor=user, kind=authenticator.type)


@receiver(mfa_signals.authenticator_removed)
def on_mfa_removed(sender: Any, request: Any, user: Any, authenticator: Any, **kw: Any) -> None:
    record(AuditAction.MFA_REMOVED, request=request, actor=user, kind=authenticator.type)


@receiver(mfa_signals.authenticator_reset)
def on_mfa_reset(sender: Any, request: Any, user: Any, authenticator: Any, **kw: Any) -> None:
    record(AuditAction.MFA_RECOVERY_CODES_RESET, request=request, actor=user)


@receiver(mfa_signals.authentication_failed)
def on_mfa_failed(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    record(AuditAction.LOGIN_FAILED, request=request, actor=user, factor="second")
