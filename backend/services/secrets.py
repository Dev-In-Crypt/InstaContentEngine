"""Symmetric encryption for user credentials stored at rest.

The app is custodian of users' API keys (OpenRouter, Instagram tokens, paid X
keys). They are encrypted with Fernet (AES-128-CBC + HMAC) before touching the
database and decrypted only in memory when a publish/generate actually runs.

The Fernet key is derived from settings.encryption_key (preferred) or, if that
is empty, from settings.secret_key. That key MUST stay stable across restarts —
rotating it makes every previously stored secret undecryptable (decrypt() then
returns None). This is documented loudly in .env.example / DEPLOY.md.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from config import get_settings


@lru_cache
def _fernet_for(material: str) -> Fernet:
    # Derive a 32-byte urlsafe-base64 Fernet key deterministically from the
    # secret material. sha256 gives exactly 32 bytes; Fernet wants them b64'd.
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> Fernet:
    settings = get_settings()
    material = settings.encryption_key or settings.secret_key
    return _fernet_for(material)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Empty string encrypts to empty (a cleared key)."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str | None:
    """Decrypt a stored secret. Returns None on tamper / wrong key / corruption,
    and "" for an empty (never-set) value."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
