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


def create_access_token(user_id: str, *, now: datetime | None = None) -> str:
    issued = now or datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(issued.timestamp()),
        "exp": int((issued + _TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=_ALGO)


def decode_access_token(token: str) -> str | None:
    """Return the user id from a valid token, or None if invalid/expired/forged."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
