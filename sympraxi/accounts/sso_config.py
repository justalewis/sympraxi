"""Single sign-on providers, built from environment variables.

Each installation serves one college, so it knows at most one Microsoft Entra ID
tenant and one Google Workspace domain. Client secrets come from the environment
and never touch the database. A provider appears only when its variables are set;
a provider that is half set up stops the site from starting, so a typo can't
quietly turn SSO off or loosen it.

Both providers go through allauth's generic OpenID Connect provider, because the
checks in `sympraxi.accounts.sso` read claims from the ID token (tenant, hosted
domain, verified email, `amr`). allauth's dedicated Microsoft provider reads the
Graph profile instead, which carries none of them.

settings.py imports this module, so it must not import anything from Django apps.
"""

import json
import uuid
from typing import Any

import environ
from django.core.exceptions import ImproperlyConfigured

ENTRA = "entra"
GOOGLE = "google"

# Where each provider's sign-in page lives. The sign-in buttons are forms, and the
# Content Security Policy's form-action must allow the redirect to these origins.
LOGIN_ORIGINS = {
    ENTRA: "https://login.microsoftonline.com",
    GOOGLE: "https://accounts.google.com",
}

# Entra's shared endpoints accept accounts from any organization, or any personal
# Microsoft account. An installation must name its own tenant.
SHARED_TENANTS = frozenset({"common", "organizations", "consumers"})

SCOPES = ["openid", "profile", "email"]


def providers_from_env(env: environ.Env, *, trust_idp_mfa: bool) -> list[dict[str, Any]]:
    """The allauth app entries for every provider whose variables are set."""
    apps = []
    entra = _read(
        env,
        tenant_id="SYMPRAXI_ENTRA_TENANT_ID",
        client_id="SYMPRAXI_ENTRA_CLIENT_ID",
        secret="SYMPRAXI_ENTRA_CLIENT_SECRET",  # noqa: S106 (a variable's name, not a secret)
    )
    if entra:
        apps.append(entra_app(**entra))
    google = _read(
        env,
        hosted_domain="SYMPRAXI_GOOGLE_HOSTED_DOMAIN",
        client_id="SYMPRAXI_GOOGLE_CLIENT_ID",
        secret="SYMPRAXI_GOOGLE_CLIENT_SECRET",  # noqa: S106
    )
    if google:
        apps.append(google_app(**google, trust_idp_mfa=trust_idp_mfa))
    return apps


def _read(env: environ.Env, **names: str) -> dict[str, str]:
    values = {key: env.str(name, default="").strip() for key, name in names.items()}
    if not any(values.values()):
        return {}
    missing = [names[key] for key, value in values.items() if not value]
    if missing:
        raise ImproperlyConfigured(
            f"Single sign-on is half set up. Set {', '.join(missing)}, or clear the "
            "provider's other variables to turn it off."
        )
    return values


def entra_tenant(value: str) -> str:
    """The tenant ID in canonical form. Shared endpoints and domain names are refused."""
    if value.lower() in SHARED_TENANTS:
        raise ImproperlyConfigured(
            f'SYMPRAXI_ENTRA_TENANT_ID can\'t be "{value}": that endpoint accepts accounts '
            "from any organization. Use your college's Directory (tenant) ID."
        )
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ImproperlyConfigured(
            "SYMPRAXI_ENTRA_TENANT_ID must be the Directory (tenant) ID, a GUID such as "
            "00000000-0000-0000-0000-000000000000, from the Entra admin center."
        ) from None


def entra_issuer(tenant_id: str) -> str:
    return f"{LOGIN_ORIGINS[ENTRA]}/{tenant_id}/v2.0"


def entra_app(*, tenant_id: str, client_id: str, secret: str) -> dict[str, Any]:
    tenant_id = entra_tenant(tenant_id)
    return {
        "provider_id": ENTRA,
        "name": "Microsoft",
        "client_id": client_id,
        "secret": secret,
        "settings": {
            # The tenant's own discovery document. Its issuer names the tenant, and
            # allauth rejects ID tokens from any other issuer.
            "server_url": entra_issuer(tenant_id),
            "token_auth_method": "client_secret_post",
            "oauth_pkce_enabled": True,
            # Use the ID token's claims only; the userinfo endpoint lacks tid and amr.
            "fetch_userinfo": False,
            "scope": SCOPES,
            "tenant_id": tenant_id,
        },
    }


def google_domain(value: str) -> str:
    domain = value.lower().lstrip("@")
    if "." not in domain or any(c in domain for c in "/:@ "):
        raise ImproperlyConfigured(
            "SYMPRAXI_GOOGLE_HOSTED_DOMAIN must be your Google Workspace domain, "
            "such as example.edu."
        )
    return domain


def google_app(
    *, hosted_domain: str, client_id: str, secret: str, trust_idp_mfa: bool
) -> dict[str, Any]:
    hosted_domain = google_domain(hosted_domain)
    # `hd` only preselects the college's accounts on Google's page; the `hd` claim
    # in the ID token is what's checked.
    auth_params = {"hd": hosted_domain}
    if trust_idp_mfa:
        # Google adds `amr` only when asked, and only for apps it has verified.
        auth_params["claims"] = json.dumps({"id_token": {"amr": {"essential": True}}})
    return {
        "provider_id": GOOGLE,
        "name": "Google",
        "client_id": client_id,
        "secret": secret,
        "settings": {
            "server_url": "https://accounts.google.com",
            "token_auth_method": "client_secret_post",
            "oauth_pkce_enabled": True,
            "fetch_userinfo": False,
            "scope": SCOPES,
            "auth_params": auth_params,
            "hosted_domain": hosted_domain,
        },
    }


def login_origins(apps: list[dict[str, Any]]) -> list[str]:
    return [LOGIN_ORIGINS[app["provider_id"]] for app in apps]


def check_sso_only(sso_only: bool, apps: list[dict[str, Any]]) -> None:
    """SSO-only with no provider would leave only site administrators able to sign in."""
    if sso_only and not apps:
        raise ImproperlyConfigured(
            "SYMPRAXI_SSO_ONLY needs a single sign-on provider. Set one up, or turn it off."
        )
