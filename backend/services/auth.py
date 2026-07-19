"""Password hashing (argon2) and stateless JWT sessions.

Pure functions so they're easy to unit-test. The JWT is signed with
settings.secret_key (HS256) and carries the user id in `sub`; cloud deployments
must set a real SECRET_KEY (enforced at startup) or these tokens are forgeable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from config import get_settings

_ph = PasswordHasher()
_ALGO = "HS256"
_TOKEN_TTL = timedelta(days=7)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def create_access_token(user_id: str, token_version: int = 0, *,
                        now: datetime | None = None) -> str:
    issued = now or datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tv": int(token_version),   # user.token_version at mint time (for revocation)
        "iat": int(issued.timestamp()),
        "exp": int((issued + _TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=_ALGO)


def decode_access_token_claims(token: str) -> dict | None:
    """Return the full validated payload of an access token, or None if
    invalid/expired/forged/wrong-purpose. Callers that need the token_version
    (revocation check) use this; decode_access_token is the sub-only shortcut."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") not in (None, "access"):
        return None   # a verify/reset token must not authenticate API calls
    if not isinstance(payload.get("sub"), str):
        return None
    return payload


def decode_access_token(token: str) -> str | None:
    """Return the user id from a valid token, or None if invalid/expired/forged."""
    claims = decode_access_token_claims(token)
    return claims["sub"] if claims else None


# ── single-use purpose tokens (email verification, password reset) ──────────

def create_purpose_token(user_id: str, purpose: str, ttl: timedelta,
                         *, now: datetime | None = None) -> str:
    issued = now or datetime.now(timezone.utc)
    payload = {
        "sub": user_id, "purpose": purpose,
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=_ALGO)


def decode_purpose_token(token: str, purpose: str) -> str | None:
    """Return the user id if the token is valid AND carries the expected purpose."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != purpose:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
