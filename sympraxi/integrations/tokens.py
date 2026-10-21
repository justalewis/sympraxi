"""Making and checking API tokens.

A token looks like `sym_AbCd1234_<32 random characters>`. Only its SHA-256 is stored,
so a database dump can't be used to call the API. The token is shown once.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from django.http import HttpRequest
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.integrations.models import SCOPES, ApiToken
from sympraxi.privacy.audit import AuditAction, record

PREFIX = "sym_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(
    center: Center,
    creator: User,
    *,
    name: str,
    scopes: list[str],
    days: int | None,
    request: HttpRequest | None = None,
) -> tuple[ApiToken, str]:
    """A new token and its text. The text can't be read again."""
    scopes = sorted(set(scopes) & set(SCOPES))
    if not scopes:
        raise ValueError("Choose at least one thing the token may do.")
    short = secrets.token_urlsafe(6)[:8].replace("-", "x").replace("_", "y")
    token = f"{PREFIX}{short}_{secrets.token_urlsafe(24)}"
    row = ApiToken.objects.create(
        center=center,
        name=name,
        prefix=f"{PREFIX}{short}",
        token_hash=_hash(token),
        scopes=scopes,
        created_by=creator,
        expires_at=timezone.now() + timedelta(days=days) if days else None,
    )
    record(
        AuditAction.API_TOKEN_CREATED,
        request=request,
        actor=creator,
        center=center,
        target=row,
        scopes=scopes,
        expires=row.expires_at.isoformat() if row.expires_at else "never",
    )
    return row, token


def find(token: str) -> ApiToken | None:
    """The usable token with this text, if there is one."""
    if not token.startswith(PREFIX) or len(token) > 100:
        return None
    row = (
        ApiToken.objects.select_related("center", "created_by")
        .filter(token_hash=_hash(token))
        .first()
    )
    return row if row is not None and row.is_usable else None


def touch(row: ApiToken, now: datetime | None = None) -> None:
    """Note a use, at most once a minute so reads don't write on every call."""
    now = now or timezone.now()
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        ApiToken.objects.filter(pk=row.pk).update(last_used_at=now)


def revoke(row: ApiToken, *, by: User, request: HttpRequest | None = None) -> bool:
    if row.revoked_at is not None:
        return False
    row.revoked_at = timezone.now()
    row.save(update_fields=["revoked_at"])
    record(AuditAction.API_TOKEN_REVOKED, request=request, actor=by, center=row.center, target=row)
    return True
