"""Business module API — sources + leads feed (Phase 2), draft + digest (Phase 3).

Every route is gated by require_business and scoped to the caller's single
workspace (resolved lazily). Ownership is enforced by workspace_id filters +
owned_source/owned_lead (404 on a miss) — the isolation seam is tested with a
mutation. The background poller is rules-only; the LLM spend lives in Phase 3's
draft/digest routes, which run on the user's own key via the normal engine.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_content_engine, get_current_user, get_db, get_settings, require_business
from api.routes.posts import _persist, _sse, _to_preview
from config import Settings
from models.database import Lead as LeadModel
from models.database import Post as PostModel
from models.database import Source as SourceModel
from models.database import User as UserModel
from models.schemas import DigestRequest, LeadOut, PostFormat, Platform, SourceCreate, SourceOut
from services.content_engine import ContentEngine
from services.source_poller import poll_source
from services.sources import SourceFetchError, detect_source_type
from services.user_settings import resolve_ai_choice
from services.workspace import get_or_create_workspace, owned_lead, owned_source

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/business", tags=["business"],
                   dependencies=[Depends(require_business)])


def _source_out(s: SourceModel) -> SourceOut:
    return SourceOut(id=s.id, url=s.url, kind=s.kind, status=s.status,
                     last_checked_at=s.last_checked_at, created_at=s.created_at)


def _lead_out(lead: LeadModel) -> LeadOut:
    missing = lead.missing if isinstance(lead.missing, list) else None
    return LeadOut(
        id=lead.id, what_happened=lead.what_happened, source_url=lead.source_url,
        quote=lead.quote, published_at=lead.published_at, why_interesting=lead.why_interesting,
        strength=lead.strength, reason=lead.reason, missing=missing,
        status=lead.status, created_at=lead.created_at)


# ── Sources ──────────────────────────────────────────────────────────────────

@router.post("/sources", response_model=dict)
async def add_source(
    body: SourceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    ws = await get_or_create_workspace(db, user)
    kind = detect_source_type(body.url)
    source = SourceModel(workspace_id=ws.id, url=body.url, kind=kind, status="ok", active=True)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    # Prime the feed immediately so it isn't empty until the next hourly poll.
    leads_found = 0
    try:
        leads_found = await poll_source(db, source, ssl_verify=settings.ssl_verify)
        await db.commit()
    except SourceFetchError as e:
        log.warning("Initial poll failed for %s: %s", body.url, e)
        source.status = "unreachable"
        await db.commit()
    return {"source": _source_out(source).model_dump(mode="json"), "leads_found": leads_found}


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> list[SourceOut]:
    ws = await get_or_create_workspace(db, user)
    rows = (await db.execute(
        select(SourceModel).where(SourceModel.workspace_id == ws.id)
        .order_by(SourceModel.created_at.desc())
    )).scalars().all()
    return [_source_out(s) for s in rows]


@router.delete("/sources/{source_id}", response_model=dict)
async def delete_source(
    source_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    ws = await get_or_create_workspace(db, user)
    source = await owned_source(db, source_id, ws)
    await db.delete(source)
    await db.commit()
    return {"status": "deleted"}


@router.post("/sources/{source_id}/refresh", response_model=dict)
async def refresh_source(
    source_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    ws = await get_or_create_workspace(db, user)
    source = await owned_source(db, source_id, ws)
    try:
        leads_found = await poll_source(db, source, ssl_verify=settings.ssl_verify)
        await db.commit()
    except SourceFetchError as e:
        log.warning("Refresh failed for %s: %s", source.url, e)
        source.status = "unreachable"
        await db.commit()
        raise HTTPException(status_code=502, detail="Couldn't reach that source.") from e
    return {"leads_found": leads_found}


# ── Leads feed ───────────────────────────────────────────────────────────────

@router.get("/leads", response_model=list[LeadOut])
async def list_leads(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    strength: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LeadOut]:
    ws = await get_or_create_workspace(db, user)
    stmt = select(LeadModel).where(LeadModel.workspace_id == ws.id)
    if strength:
        stmt = stmt.where(LeadModel.strength == strength)
    if status:
        stmt = stmt.where(LeadModel.status == status)
    if since:
        stmt = stmt.where(LeadModel.created_at >= since)
    stmt = stmt.order_by(LeadModel.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [_lead_out(lead) for lead in rows]


@router.get("/leads/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> LeadOut:
    ws = await get_or_create_workspace(db, user)
    lead = await owned_lead(db, lead_id, ws)
    return _lead_out(lead)


@router.post("/leads/{lead_id}/dismiss", response_model=dict)
async def dismiss_lead(
    lead_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    ws = await get_or_create_workspace(db, user)
    lead = await owned_lead(db, lead_id, ws)
    lead.status = "dismissed"
    await db.commit()
    return {"status": "dismissed"}


@router.post("/leads/{lead_id}/snooze-kind", response_model=dict)
async def snooze_lead_kind(
    lead_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    ws = await get_or_create_workspace(db, user)
    lead = await owned_lead(db, lead_id, ws)
    lead.status = "snoozed_kind"
    await db.commit()
    return {"status": "snoozed_kind"}


# ── Draft + digest (Phase 3) — LLM here, on the user's own key ────────────────

_GROUND = ("Write ONLY from the facts in the SOURCE below. Do not invent numbers, "
           "names, dates, or claims; if a detail isn't in the source, leave it out.")


async def _generate_business_post(
    engine: ContentEngine, db: AsyncSession, user: UserModel, ws, *,
    topic: str, instructions: str, source_kind: str, source_url: Optional[str],
    lead_id: Optional[str], text_model: str, progress,
) -> PostModel:
    """Generate a post via the shared engine, persist it, and tag it as Business."""
    generated = await engine.generate_post(
        topic=topic, format=PostFormat.SINGLE, text_model=text_model,
        additional_instructions=instructions, platform=Platform.INSTAGRAM,
        progress=progress,
    )
    await progress("Saving draft…")
    post = await _persist(generated, db, "branded_card", user_id=user.id)
    post.workspace_id = ws.id
    post.source_kind = source_kind
    post.lead_id = lead_id
    if source_url:
        post.sources = [{"title": topic[:120], "url": source_url}]
    await db.commit()
    return post


def _draft_stream(db, user, ws, engine, text_model, *, topic, instructions,
                  source_kind, source_url, lead, digest_leads=None):
    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(message: str) -> None:
            await queue.put({"type": "progress", "message": message})

        async def run() -> None:
            try:
                post = await _generate_business_post(
                    engine, db, user, ws, topic=topic, instructions=instructions,
                    source_kind=source_kind, source_url=source_url,
                    lead_id=(lead.id if lead else None), text_model=text_model,
                    progress=progress)
                if lead is not None:
                    lead.status = "drafted"
                for dl in (digest_leads or []):
                    dl.status = "digested"
                await db.commit()
                await queue.put({"type": "complete",
                                 "post": _to_preview(post).model_dump(mode="json")})
            except Exception:
                log.exception("Business draft failed")
                await queue.put({"type": "error",
                                 "message": "Generation failed. Please try again."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse(event)
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/leads/{lead_id}/draft")
async def draft_lead(
    lead_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
) -> StreamingResponse:
    ws = await get_or_create_workspace(db, user)
    lead = await owned_lead(db, lead_id, ws)
    _p, text_model, _k = resolve_ai_choice(user, settings, "text")
    if not text_model:
        raise HTTPException(status_code=400,
                            detail="No text model selected. Choose one in Account → AI models.")
    topic = lead.what_happened or "Company update"
    instructions = (f"This is a public update from the company's own source. {_GROUND}\n\n"
                    f"SOURCE:\n{lead.what_happened or ''}\n{lead.quote or ''}")
    return _draft_stream(db, user, ws, engine, text_model, topic=topic,
                         instructions=instructions, source_kind="lead",
                         source_url=lead.source_url, lead=lead)


@router.post("/digest")
async def draft_digest(
    body: DigestRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
) -> StreamingResponse:
    ws = await get_or_create_workspace(db, user)
    leads = [await owned_lead(db, lid, ws) for lid in body.lead_ids]
    _p, text_model, _k = resolve_ai_choice(user, settings, "text")
    if not text_model:
        raise HTTPException(status_code=400,
                            detail="No text model selected. Choose one in Account → AI models.")
    bullets = "\n".join(f"- {ld.what_happened or ''}: {ld.quote or ''}" for ld in leads)
    instructions = (f"Write one 'what's new this week' post summarising these updates. {_GROUND}\n\n"
                    f"SOURCE UPDATES:\n{bullets}")
    return _draft_stream(db, user, ws, engine, text_model, topic="What's new this week",
                         instructions=instructions, source_kind="digest",
                         source_url=None, lead=None, digest_leads=leads)


@router.get("/drafts", response_model=list[dict])
async def list_drafts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> list[dict]:
    ws = await get_or_create_workspace(db, user)
    rows = (await db.execute(
        select(PostModel).where(PostModel.workspace_id == ws.id)
        .order_by(PostModel.created_at.desc()).limit(100)
    )).scalars().all()
    out = []
    for p in rows:
        src = p.sources[0] if isinstance(p.sources, list) and p.sources else {}
        out.append({
            "id": p.id, "topic": p.topic, "hook": p.hook, "caption": p.caption,
            "hashtags": p.hashtags or [], "source_kind": p.source_kind,
            "source_url": src.get("url") if isinstance(src, dict) else None,
            "platform": p.platform,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return out
