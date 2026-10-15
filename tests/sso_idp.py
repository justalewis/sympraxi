"""A fake Microsoft Entra ID and Google for single sign-on tests. No network calls.

allauth makes two requests during an OpenID Connect sign-in: it fetches the
provider's discovery document and exchanges the code for tokens. `FakeIdP` is a
`requests` transport adapter that answers both from memory and fails the test on
any other URL. `sign_in()` then walks a test client through the real views:
start at the SSO button, follow the redirect's `state` back to the callback.
"""

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import requests
from requests.adapters import BaseAdapter

from sympraxi.accounts import sso_config

TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-8888-7777-6666-555555555555"
ENTRA_CLIENT_ID = "entra-test-client"
GOOGLE_CLIENT_ID = "google-test-client.apps.googleusercontent.com"
GOOGLE_DOMAIN = "example.edu"

SIGNING_KEY = "fake-idp-signing-key-for-tests-only-0123456789"

ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
GOOGLE_ISSUER = "https://accounts.google.com"

DISCOVERY = {
    f"{ENTRA_ISSUER}/.well-known/openid-configuration": {
        "issuer": ENTRA_ISSUER,
        "authorization_endpoint": (
            f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
        ),
        "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
        "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "private_key_jwt",
            "client_secret_basic",
        ],
    },
    f"{GOOGLE_ISSUER}/.well-known/openid-configuration": {
        "issuer": GOOGLE_ISSUER,
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    },
}
TOKEN_ENDPOINTS = {doc["token_endpoint"] for doc in DISCOVERY.values()}


def providers(*, entra: bool = True, google: bool = True, trust_idp_mfa: bool = False) -> dict:
    """A SOCIALACCOUNT_PROVIDERS value, built the way settings.py builds it."""
    apps = []
    if entra:
        apps.append(
            sso_config.entra_app(tenant_id=TENANT, client_id=ENTRA_CLIENT_ID, secret="entra-secret")
        )
    if google:
        apps.append(
            sso_config.google_app(
                hosted_domain=GOOGLE_DOMAIN,
                client_id=GOOGLE_CLIENT_ID,
                secret="google-secret",
                trust_idp_mfa=trust_idp_mfa,
            )
        )
    return {"openid_connect": {"APPS": apps}}


def _token_times() -> dict[str, int]:
    now = int(time.time())
    return {"iat": now - 5, "nbf": now - 5, "exp": now + 3600}


def entra_claims(**changes: Any) -> dict[str, Any]:
    """An Entra v2.0 ID token for a student at the college. Pass None to drop a claim."""
    claims = {
        "iss": ENTRA_ISSUER,
        "aud": ENTRA_CLIENT_ID,
        "tid": TENANT,
        "sub": "entra-subject-1",
        "oid": "00000000-0000-0000-0000-00000000aaaa",
        "email": "sam@example.edu",
        "xms_edov": True,
        "name": "Sam Rivera",
        "preferred_username": "sam@example.edu",
        "ver": "2.0",
        **_token_times(),
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def google_claims(**changes: Any) -> dict[str, Any]:
    """A Google Workspace ID token for a student at the college."""
    claims = {
        "iss": GOOGLE_ISSUER,
        "aud": GOOGLE_CLIENT_ID,
        "azp": GOOGLE_CLIENT_ID,
        "sub": "google-subject-1",
        "hd": GOOGLE_DOMAIN,
        "email": "sam@example.edu",
        "email_verified": True,
        "name": "Sam Rivera",
        **_token_times(),
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def _json_response(request: requests.PreparedRequest, data: dict) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(data).encode()
    response.headers["Content-Type"] = "application/json"
    response.url = request.url or ""
    response.request = request
    response.encoding = "utf-8"
    return response


class FakeIdP(BaseAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.claims: dict[str, Any] = {}
        self.token_requests: list[str] = []

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        url = request.url or ""
        if url in DISCOVERY:
            return _json_response(request, DISCOVERY[url])
        if url in TOKEN_ENDPOINTS:
            self.token_requests.append(str(request.body))
            # allauth reads these claims without checking the signature, as OIDC
            # allows for a token fetched straight from the provider over TLS. It
            # still checks the issuer, audience and expiry.
            id_token = jwt.encode(self.claims, SIGNING_KEY, algorithm="HS256")
            return _json_response(
                request,
                {"access_token": "fake-access", "token_type": "Bearer", "id_token": id_token},
            )
        raise AssertionError(f"Test tried to reach the network: {request.method} {url}")

    def close(self) -> None:
        pass

    def session(self) -> requests.Session:
        session = requests.Session()
        session.mount("https://", self)
        session.mount("http://", self)
        return session


def start(client: Any, provider_id: str, *, process: str = "login") -> Any:
    """Press the provider's button. Returns the redirect to the provider."""
    return client.post(f"/accounts/oidc/{provider_id}/login/", {"process": process})


def sign_in(
    client: Any, idp: FakeIdP, provider_id: str, claims: dict[str, Any], *, process: str = "login"
) -> Any:
    """A full sign-in: button, provider (faked), callback. Returns the callback's response."""
    idp.claims = claims
    redirect = start(client, provider_id, process=process)
    assert redirect.status_code == 302, redirect
    state = parse_qs(urlparse(redirect["Location"]).query)["state"][0]
    return client.get(
        f"/accounts/oidc/{provider_id}/login/callback/", {"code": "fake-code", "state": state}
    )
