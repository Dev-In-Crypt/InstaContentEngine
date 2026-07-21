"""Managed accounts (Phase 7): agency multi-account resolution + ownership gate.

A managed account is a client brand under one owner. It is NEVER a security boundary
— posts are always owned by user_id; the active account only scopes the view and
supplies the brand identity for generation. Its columns mirror User's brand fields,
so the existing resolvers (resolve_user_profile / resolve_user_brand_voice /
apply_user_slide_style) accept it directly via duck typing.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import ManagedAccount as ManagedAccountModel
from models.database import User as UserModel


async def resolve_active_account(db: AsyncSession, user: UserModel):
    """The user's active managed account, or None for Personal. Defensive: returns
    None if the stored id doesn't resolve to an account this user owns."""
    account_id = getattr(user, "active_account_id", None)
    if not account_id:
        return None
    acct = await db.get(ManagedAccountModel, account_id)
    if acct is None or acct.owner_user_id != user.id:
        return None
    return acct


def brand_source(account, user: UserModel):
    """Where generation reads brand identity from: the active account, else the user."""
    return account or user


async def owned_account(db: AsyncSession, account_id: str, user: UserModel) -> ManagedAccountModel:
    """Fetch a managed account this user owns, else 404 (don't reveal another's)."""
    acct = (await db.execute(
        select(ManagedAccountModel).where(
            ManagedAccountModel.id == account_id,
            ManagedAccountModel.owner_user_id == user.id)
    )).scalar_one_or_none()
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


def _active_filter_value(user: UserModel) -> Optional[str]:
    """The managed_account_id a listing should filter on for this user (None=Personal)."""
    return getattr(user, "active_account_id", None)
