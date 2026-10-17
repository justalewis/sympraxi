"""Encrypt the two-factor secrets that allauth keeps in the database.

allauth stores each authenticator app's TOTP secret, and the seed its recovery
codes are made from, in `mfa_authenticator.data`. Its default MFA adapter stores
them as they are, so anyone with a database dump or a backup could make every
person's codes. `MFAAdapter` (adapter.py) passes them through here instead.

The cipher is Fernet: AES-128 in CBC mode with an HMAC-SHA256 tag. Its key is
derived from SECRET_KEY, which lives in deploy/.env and never in the database or
its backups. A backup is useless for two-factor login without that key.

Rotating SECRET_KEY: move the old key to SYMPRAXI_SECRET_KEY_FALLBACKS, so secrets
written under it still decrypt, run `manage.py reencrypt_mfa_secrets` to rewrite
them under the new key, and then drop the fallback (docs/install.md).
"""

import base64
import binascii
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

# Where the secrets are in Authenticator.data, by authenticator type. Passkeys
# (webauthn) hold only a public key, so they have nothing to encrypt.
SECRET_FIELDS = {"totp": ("secret",), "recovery_codes": ("seed",)}
# Recovery codes carried over from django-allauth's old format: a list.
SECRET_LISTS = {"recovery_codes": ("migrated_codes",)}
KEY_PURPOSE = b"sympraxi.accounts.encryption.mfa-secrets"
FERNET_VERSION = 0x80


class UnreadableSecretError(Exception):
    """A stored secret didn't decrypt with SECRET_KEY or any of its fallbacks."""


def fernet_key(secret_key: str) -> bytes:
    """A Fernet key for this purpose only, derived from a Django secret key."""
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=KEY_PURPOSE).derive(
        secret_key.encode()
    )
    return base64.urlsafe_b64encode(derived)


def cipher() -> MultiFernet:
    """Encrypts with SECRET_KEY; decrypts with it or any of its fallbacks."""
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    return MultiFernet([Fernet(fernet_key(key)) for key in keys])


def encrypt(text: str) -> str:
    return cipher().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        raise UnreadableSecretError(
            "A two-factor secret didn't decrypt with SYMPRAXI_SECRET_KEY or "
            "SYMPRAXI_SECRET_KEY_FALLBACKS. Put back the key it was written under "
            "(docs/install.md, 'Keys and passwords')."
        ) from None


def reencrypt(token: str) -> str:
    """The same secret under the current SECRET_KEY."""
    try:
        return cipher().rotate(token.encode()).decode()
    except InvalidToken:
        raise UnreadableSecretError("A two-factor secret didn't decrypt.") from None


def is_encrypted(value: str) -> bool:
    """Whether a stored value is a Fernet token, readable or not.

    TOTP secrets are base32 and recovery-code seeds are hex, so neither can start
    with the version byte a Fernet token starts with.
    """
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except (binascii.Error, ValueError):
        return False
    return len(raw) > 1 and raw[0] == FERNET_VERSION


def map_secrets(kind: str, data: dict[str, Any], change: Callable[[str], str]) -> dict[str, Any]:
    """A copy of an authenticator's data with `change` applied to each secret in it."""
    data = dict(data)
    for field in SECRET_FIELDS.get(kind, ()):
        if isinstance(data.get(field), str):
            data[field] = change(data[field])
    for field in SECRET_LISTS.get(kind, ()):
        if isinstance(data.get(field), list):
            data[field] = [change(item) for item in data[field]]
    return data
