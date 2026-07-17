"""Per-user effective Settings: platform config overlaid with a user's own,
decrypted API keys. Lives in the services layer (not api.deps) so both the FastAPI
DI (get_effective_settings) and the request-less publisher_flow/scheduler can use
it without importing the api layer.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from models.database import User as UserModel, UserCredentials as UserCredentialsModel
from services.secrets import decrypt

# Every Settings field a user may override with their own key, mapped to the
# UserCredentials column that stores it (encrypted).
_CRED_FIELDS: dict[str, str] = {
    "openrouter_api_key": "openrouter_api_key_enc",
    "instagram_access_token": "instagram_access_token_enc",
    "instagram_user_id": "instagram_user_id_enc",
    "imgbb_api_key": "imgbb_api_key_enc",
    "x_api_key": "x_api_key_enc",
    "x_api_secret": "x_api_secret_enc",
    "x_access_token": "x_access_token_enc",
    "x_access_token_secret": "x_access_token_secret_enc",
    "unsplash_access_key": "unsplash_access_key_enc",
    "pexels_api_key": "pexels_api_key_enc",
}


async def build_settings_for_user(db: AsyncSession, user: Optional[UserModel]) -> Settings:
    """Platform Settings overlaid with the user's own decrypted API keys. Local
    user (or unknown) → platform .env as-is."""
    base = get_settings()
    if user is None or user.is_local:
        return base
    creds = await db.get(UserCredentialsModel, user.id)
    if creds is None:
        return base
    overrides: dict[str, str] = {}
    for field, column in _CRED_FIELDS.items():
        decrypted = decrypt(getattr(creds, column) or "")
        if decrypted:   # None (tamper) or "" (unset) → keep platform default
            overrides[field] = decrypted
    return base.model_copy(update=overrides) if overrides else base


async def settings_for_post_owner(db: AsyncSession, post) -> Settings:
    """Effective Settings from the post owner's stored keys, for publishing outside
    a request (publisher_flow / scheduler). Owner is the local user or unknown →
    platform .env."""
    user = await db.get(UserModel, post.user_id) if post.user_id else None
    return await build_settings_for_user(db, user)
