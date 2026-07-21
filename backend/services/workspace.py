"""Workspace resolution + ownership gates for the Business module.

A business user gets exactly one workspace for now. It's created lazily (business
accounts from Phase 0 have none yet), so every business route starts by resolving
it. Ownership gates mirror api.deps.owned_post: filter by workspace_id, 404 (not
403) on a miss so one tenant can't probe another's rows.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Lead as LeadModel
from models.database import Source as SourceModel
from models.database import User as UserModel
from models.database import Workspace as WorkspaceModel


async def get_or_create_workspace(db: AsyncSession, user: UserModel) -> WorkspaceModel:
    """The user's single workspace, created on first use."""
    ws = (await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.owner_user_id == user.id)
    )).scalar_one_or_none()
    if ws is None:
        name = (user.brand_name or (user.email.split("@")[0] if user.email else "") or "My workspace")
        ws = WorkspaceModel(owner_user_id=user.id, name=name)
        db.add(ws)
        await db.commit()
        await db.refresh(ws)
    return ws


async def owned_source(db: AsyncSession, source_id: str, workspace: WorkspaceModel) -> SourceModel:
    src = (await db.execute(
        select(SourceModel).where(
            SourceModel.id == source_id, SourceModel.workspace_id == workspace.id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return src


async def owned_lead(db: AsyncSession, lead_id: str, workspace: WorkspaceModel) -> LeadModel:
    lead = (await db.execute(
        select(LeadModel).where(
            LeadModel.id == lead_id, LeadModel.workspace_id == workspace.id)
    )).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
