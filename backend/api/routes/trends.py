"""Trend Finder API — discover trending media, adapt to MLMG ideas, generate posts."""
from __future__ import annotations

import asyncio
import io  # noqa: F401 (kept for parity with posts.py utilities)
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import (
    get_content_engine, get_db, get_openrouter, get_settings,
    get_trend_adapter, load_brand_config, make_trend_provider_for, require_token,
)
from config import Settings
from models.database import (
    CompetitorAccount as CompetitorModel,
    Post as PostModel,
    Slide as SlideModel,
    TrendIdea as TrendIdeaModel,
    TrendingMedia as TrendingMediaModel,
)
from models.schemas import (
    AdaptTrendRequest, CompetitorAccount as CompetitorSchema, CompetitorCreate,
    CompetitorUpdate, GenerateFromIdeaRequest, LengthTier, Platform, PostStatus,
    PostPreview, RefreshTrendsRequest, SlidePreview, TrendIdeaSchema, TrendIdeaUpdate,
    TrendingMediaPreview, TrendMediaType,
)
from services.brand_engine import PillowBrandEngine
from services.content_engine import ContentEngine, GeneratedPost
from services.trend_adapter import TrendAdapter, TrendAdaptError
from services.trend_extractor import (
    compute_engagement_score, extract_cta, extract_hashtags, extract_hook,
)
from services.trend_provider import FetchedMedia, TrendProviderError

router = APIRouter(prefix="/api/trends", tags=["trends"])

UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads" / "posts"


def _slide_path(post_id: str, slide_num: int) -> Path:
    return UPLOADS_DIR / post_id / f"slide_{slide_num}.jpg"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Competitors CRUD
# ---------------------------------------------------------------------------

@router.get("/competitors", response_model=list[CompetitorSchema],
            dependencies=[Depends(require_token)])
async def list_competitors(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CompetitorSchema]:
    result = await db.execute(select(CompetitorModel).order_by(CompetitorModel.created_at.desc()))
    return [CompetitorSchema.model_validate(c) for c in result.scalars().all()]


@router.post("/competitors", response_model=CompetitorSchema,
             dependencies=[Depends(require_token)])
async def add_competitor(
    body: CompetitorCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CompetitorSchema:
    handle = body.handle.lstrip("@").strip()
    if not handle:
        raise HTTPException(status_code=400, detail="handle is required")
    existing = await db.execute(
        select(CompetitorModel).where(CompetitorModel.handle == handle)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Handle @{handle} already exists")
    row = CompetitorModel(
        id=str(uuid.uuid4()),
        handle=handle,
        niche=body.niche,
        active=body.active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CompetitorSchema.model_validate(row)


@router.put("/competitors/{competitor_id}", response_model=CompetitorSchema,
            dependencies=[Depends(require_token)])
async def update_competitor(
    competitor_id: str,
    body: CompetitorUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CompetitorSchema:
    row = await db.get(CompetitorModel, competitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="Competitor not found")
    if body.niche is not None:
        row.niche = body.niche
    if body.active is not None:
        row.active = body.active
    await db.commit()
    await db.refresh(row)
    return CompetitorSchema.model_validate(row)


@router.delete("/competitors/{competitor_id}", dependencies=[Depends(require_token)])
async def delete_competitor(
    competitor_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = await db.get(CompetitorModel, competitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="Competitor not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": competitor_id}


# ---------------------------------------------------------------------------
# Discovery (on-demand)
# ---------------------------------------------------------------------------

async def _resolve_handles(
    db: AsyncSession, requested: Optional[list[str]]
) -> tuple[list[str], dict[str, Optional[str]]]:
    """Return (handles, handle→niche map)."""
    result = await db.execute(select(CompetitorModel).where(CompetitorModel.active == True))  # noqa: E712
    rows = list(result.scalars().all())
    niche_map = {r.handle: r.niche for r in rows}
    if requested:
        wanted = {h.lstrip("@").strip().lower() for h in requested if h}
        handles = [r.handle for r in rows if r.handle.lower() in wanted]
    else:
        handles = [r.handle for r in rows]
    return handles, niche_map


def _media_type_safe(value: str) -> TrendMediaType:
    try:
        return TrendMediaType(value)
    except ValueError:
        return TrendMediaType.IMAGE


async def _upsert_fetched(
    db: AsyncSession, item: FetchedMedia
) -> TrendingMediaModel:
    """Upsert a fetched media row by ig_media_id, refreshing extracted fields."""
    result = await db.execute(
        select(TrendingMediaModel).where(TrendingMediaModel.ig_media_id == item.ig_media_id)
    )
    row = result.scalar_one_or_none()

    hashtags = extract_hashtags(item.caption)
    hook = extract_hook(item.caption)
    cta = extract_cta(item.caption)
    score = compute_engagement_score(item.likes, item.comments, item.views)

    if row is None:
        row = TrendingMediaModel(
            id=str(uuid.uuid4()),
            ig_media_id=item.ig_media_id,
            source_handle=item.source_handle,
            media_type=item.media_type,
            permalink=item.permalink,
            thumbnail_url=item.thumbnail_url,
            caption=item.caption,
            extracted_hook=hook,
            extracted_cta=cta,
            hashtags=hashtags,
            likes=item.likes,
            comments=item.comments,
            views=item.views,
            engagement_score=score,
            posted_at=item.posted_at,
            raw_payload=item.raw,
        )
        db.add(row)
    else:
        # Refresh mutable fields (metrics evolve).
        row.media_type = item.media_type
        row.permalink = item.permalink or row.permalink
        row.thumbnail_url = item.thumbnail_url or row.thumbnail_url
        row.caption = item.caption or row.caption
        row.extracted_hook = hook or row.extracted_hook
        row.extracted_cta = cta or row.extracted_cta
        row.hashtags = hashtags or row.hashtags
        row.likes = item.likes
        row.comments = item.comments
        row.views = item.views if item.views is not None else row.views
        row.engagement_score = score
        row.raw_payload = item.raw
        row.fetched_at = datetime.now(timezone.utc)
    return row


@router.post("/refresh", dependencies=[Depends(require_token)])
async def refresh_trends(
    body: RefreshTrendsRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Fetch latest media for every active competitor (or the subset requested).

    Streams Server-Sent Events with per-handle progress and a final summary.
    """
    handles, _niche_map = await _resolve_handles(db, body.handles)
    provider = make_trend_provider_for(body.source.value, settings)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            if not handles:
                yield _sse({"type": "complete", "fetched": 0, "handles": []})
                return

            total = 0
            for handle in handles:
                yield _sse({"type": "progress", "handle": handle, "message": f"Fetching @{handle}…"})
                try:
                    items = await provider.fetch_for_handles([handle], body.limit_per_account)
                except TrendProviderError as exc:
                    yield _sse({"type": "error", "handle": handle, "message": str(exc)})
                    continue
                for it in items:
                    await _upsert_fetched(db, it)
                await db.commit()
                total += len(items)
                yield _sse({
                    "type": "progress", "handle": handle,
                    "message": f"@{handle} → {len(items)} items", "fetched": len(items),
                })
            yield _sse({"type": "complete", "fetched": total, "handles": handles})
        except Exception as exc:  # pragma: no cover - safety net
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            await provider.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Trending media browse
# ---------------------------------------------------------------------------

def _to_media_preview(row: TrendingMediaModel) -> TrendingMediaPreview:
    return TrendingMediaPreview(
        id=row.id,
        source_handle=row.source_handle,
        ig_media_id=row.ig_media_id,
        media_type=_media_type_safe(row.media_type),
        permalink=row.permalink,
        thumbnail_url=row.thumbnail_url,
        caption=row.caption,
        extracted_hook=row.extracted_hook,
        extracted_topic=row.extracted_topic,
        extracted_cta=row.extracted_cta,
        hashtags=row.hashtags or [],
        likes=row.likes or 0,
        comments=row.comments or 0,
        views=row.views,
        engagement_score=row.engagement_score or 0.0,
        posted_at=row.posted_at,
        fetched_at=row.fetched_at or datetime.now(timezone.utc),
    )


@router.get("/media", response_model=list[TrendingMediaPreview],
            dependencies=[Depends(require_token)])
async def list_media(
    db: Annotated[AsyncSession, Depends(get_db)],
    source_handle: Optional[str] = Query(None),
    media_type: Optional[TrendMediaType] = Query(None),
    sort: str = Query("engagement", pattern="^(engagement|recency)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TrendingMediaPreview]:
    stmt = select(TrendingMediaModel)
    if source_handle:
        stmt = stmt.where(TrendingMediaModel.source_handle == source_handle.lstrip("@"))
    if media_type:
        stmt = stmt.where(TrendingMediaModel.media_type == media_type.value)
    if sort == "engagement":
        stmt = stmt.order_by(TrendingMediaModel.engagement_score.desc())
    else:
        stmt = stmt.order_by(TrendingMediaModel.fetched_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return [_to_media_preview(r) for r in result.scalars().all()]


@router.get("/media/{media_id}", response_model=TrendingMediaPreview,
            dependencies=[Depends(require_token)])
async def get_media(
    media_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrendingMediaPreview:
    row = await db.get(TrendingMediaModel, media_id)
    if not row:
        raise HTTPException(status_code=404, detail="Trending media not found")
    return _to_media_preview(row)


# ---------------------------------------------------------------------------
# Adapt → Idea
# ---------------------------------------------------------------------------

def _to_idea_schema(row: TrendIdeaModel, source: Optional[TrendingMediaModel] = None) -> TrendIdeaSchema:
    return TrendIdeaSchema(
        id=row.id,
        source_media_id=row.source_media_id,
        hook=row.hook,
        short_script=row.short_script,
        shot_list=row.shot_list or [],
        caption=row.caption,
        cta=row.cta,
        hashtags=row.hashtags or [],
        seo_keywords=row.seo_keywords or [],
        platform=Platform(row.platform or "instagram"),
        length_tier=LengthTier(row.length_tier or "sweet_spot"),
        additional_instructions=row.additional_instructions,
        created_at=row.created_at or datetime.now(timezone.utc),
        source_handle=(source.source_handle if source else None),
        source_permalink=(source.permalink if source else None),
    )


@router.post("/media/{media_id}/adapt", response_model=TrendIdeaSchema,
             dependencies=[Depends(require_token)])
async def adapt_media(
    media_id: str,
    body: AdaptTrendRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    adapter: Annotated[TrendAdapter, Depends(get_trend_adapter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrendIdeaSchema:
    media = await db.get(TrendingMediaModel, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Trending media not found")

    try:
        idea = await adapter.adapt(
            source_handle=media.source_handle,
            media_type=media.media_type,
            permalink=media.permalink,
            caption=media.caption,
            source_hook=media.extracted_hook,
            likes=media.likes or 0,
            comments=media.comments or 0,
            views=media.views,
            platform=body.platform,
            length_tier=body.length_tier,
            additional_instructions=body.additional_instructions,
            text_model=settings.default_text_model,
        )
    except TrendAdaptError as exc:
        raise HTTPException(status_code=502, detail=f"Adapter failed: {exc}") from exc

    row = TrendIdeaModel(
        id=str(uuid.uuid4()),
        source_media_id=media.id,
        hook=idea.hook,
        short_script=idea.short_script,
        shot_list=idea.shot_list,
        caption=idea.caption,
        cta=idea.cta,
        hashtags=idea.hashtags,
        seo_keywords=idea.seo_keywords,
        platform=body.platform.value,
        length_tier=body.length_tier.value,
        additional_instructions=body.additional_instructions,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_idea_schema(row, media)


@router.get("/ideas", response_model=list[TrendIdeaSchema],
            dependencies=[Depends(require_token)])
async def list_ideas(
    db: Annotated[AsyncSession, Depends(get_db)],
    source_media_id: Optional[str] = Query(None),
    platform: Optional[Platform] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TrendIdeaSchema]:
    stmt = select(TrendIdeaModel).options(selectinload(TrendIdeaModel.source_media))
    if source_media_id:
        stmt = stmt.where(TrendIdeaModel.source_media_id == source_media_id)
    if platform:
        stmt = stmt.where(TrendIdeaModel.platform == platform.value)
    stmt = stmt.order_by(TrendIdeaModel.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return [_to_idea_schema(r, r.source_media) for r in result.scalars().all()]


@router.get("/ideas/{idea_id}", response_model=TrendIdeaSchema,
            dependencies=[Depends(require_token)])
async def get_idea(
    idea_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrendIdeaSchema:
    result = await db.execute(
        select(TrendIdeaModel)
        .where(TrendIdeaModel.id == idea_id)
        .options(selectinload(TrendIdeaModel.source_media))
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Idea not found")
    return _to_idea_schema(row, row.source_media)


@router.put("/ideas/{idea_id}", response_model=TrendIdeaSchema,
            dependencies=[Depends(require_token)])
async def update_idea(
    idea_id: str,
    body: TrendIdeaUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrendIdeaSchema:
    result = await db.execute(
        select(TrendIdeaModel)
        .where(TrendIdeaModel.id == idea_id)
        .options(selectinload(TrendIdeaModel.source_media))
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Idea not found")
    if body.hook is not None: row.hook = body.hook
    if body.short_script is not None: row.short_script = body.short_script
    if body.shot_list is not None: row.shot_list = body.shot_list
    if body.caption is not None: row.caption = body.caption
    if body.cta is not None: row.cta = body.cta
    if body.hashtags is not None: row.hashtags = body.hashtags
    if body.seo_keywords is not None: row.seo_keywords = body.seo_keywords
    await db.commit()
    await db.refresh(row)
    return _to_idea_schema(row, row.source_media)


@router.delete("/ideas/{idea_id}", dependencies=[Depends(require_token)])
async def delete_idea(
    idea_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = await db.get(TrendIdeaModel, idea_id)
    if not row:
        raise HTTPException(status_code=404, detail="Idea not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": idea_id}


# ---------------------------------------------------------------------------
# Idea → Post (reuses ContentEngine + the same SSE pattern as posts.generate)
# ---------------------------------------------------------------------------

async def _persist_post(
    generated: GeneratedPost, db: AsyncSession,
    template_style: str, trend_idea_id: str,
) -> PostModel:
    post_dir = UPLOADS_DIR / generated.id
    post_dir.mkdir(parents=True, exist_ok=True)

    db_post = PostModel(
        id=generated.id,
        topic=generated.topic,
        format=generated.format.value,
        status="preview",
        caption=generated.caption,
        hashtags=generated.hashtags,
        seo_keywords=generated.seo_keywords,
        cta=generated.cta,
        hook=generated.hook,
        alt_text=generated.alt_text,
        platform=generated.platform.value,
        template_style=template_style,
        text_model=generated.text_model_used,
        image_model=generated.image_model_used,
        trend_idea_id=trend_idea_id,
    )
    db.add(db_post)
    for slide in generated.slides:
        path = _slide_path(generated.id, slide.slide_number)
        path.write_bytes(slide.image_bytes)
        db.add(SlideModel(
            post_id=generated.id,
            slide_number=slide.slide_number,
            image_source=slide.image_source.value,
            image_path=str(path),
            search_query=slide.search_query,
            gen_prompt=slide.gen_prompt,
        ))
    await db.commit()
    result = await db.execute(
        select(PostModel)
        .where(PostModel.id == generated.id)
        .options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea)
                 .selectinload(TrendIdeaModel.source_media))
    )
    return result.scalar_one()


def _post_to_preview(post: PostModel) -> PostPreview:
    height = 1350 if (post.template_style or "branded_card") == "branded_card" else 1080
    slides = [
        SlidePreview(
            slide_number=s.slide_number,
            image_url=f"/api/posts/{post.id}/slides/{s.slide_number}/image",
            image_source=s.image_source,
            width=1080,
            height=height,
        )
        for s in sorted(post.slides, key=lambda s: s.slide_number)
    ]
    trend = post.trend_idea
    source = trend.source_media if trend else None
    return PostPreview(
        id=post.id,
        topic=post.topic,
        format=post.format,
        status=PostStatus(post.status),
        caption=post.caption or "",
        hashtags=post.hashtags or [],
        seo_keywords=post.seo_keywords or [],
        cta=post.cta or "",
        hook=post.hook or "",
        platform=Platform(post.platform or "instagram"),
        slides=slides,
        text_model_used=post.text_model or "",
        image_model_used=post.image_model,
        created_at=post.created_at or datetime.now(timezone.utc),
        trend_idea_id=post.trend_idea_id,
        trend_source_handle=(source.source_handle if source else None),
        trend_source_permalink=(source.permalink if source else None),
    )


@router.post("/ideas/{idea_id}/generate", dependencies=[Depends(require_token)])
async def generate_from_idea(
    idea_id: str,
    body: GenerateFromIdeaRequest,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    result = await db.execute(
        select(TrendIdeaModel)
        .where(TrendIdeaModel.id == idea_id)
        .options(selectinload(TrendIdeaModel.source_media))
    )
    idea: Optional[TrendIdeaModel] = result.scalar_one_or_none()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    source_handle = idea.source_media.source_handle if idea.source_media else "trend"
    topic = idea.hook
    niche = body.niche or f"@{source_handle}"
    # Combine idea body into guidance for the caption generator.
    guidance_parts = []
    if idea.short_script:
        guidance_parts.append(f"Script outline:\n{idea.short_script}")
    if idea.shot_list:
        guidance_parts.append("Shot list:\n- " + "\n- ".join(idea.shot_list))
    if body.additional_instructions:
        guidance_parts.append(body.additional_instructions)
    extra = "\n\n".join(guidance_parts) if guidance_parts else None

    text_model = body.text_model or settings.default_text_model
    image_model = body.image_model or settings.default_image_model
    platform = Platform(idea.platform or "instagram")
    length_tier = LengthTier(idea.length_tier or "sweet_spot")

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(message: str) -> None:
            await queue.put({"type": "progress", "message": message})

        async def run() -> None:
            try:
                brand_cfg = await load_brand_config(db, body.brand_config_id)
                engine.brand_engine = PillowBrandEngine(brand_cfg)
                generated = await engine.generate_post(
                    topic=topic,
                    format=body.format,
                    text_model=text_model,
                    image_model=image_model,
                    default_image_source=body.default_image_source,
                    tone=body.tone,
                    niche=niche,
                    target_audience=body.target_audience,
                    additional_instructions=extra,
                    apply_branding=body.apply_branding,
                    platform=platform,
                    length_tier=length_tier,
                    template_style=body.template_style,
                    niche_box_color=body.niche_box_color,
                    show_logo=body.show_logo,
                    progress=progress,
                )
                await progress("Saving to database...")
                db_post = await _persist_post(generated, db, body.template_style.value, idea.id)
                preview = _post_to_preview(db_post)
                await queue.put({"type": "complete", "post": preview.model_dump(mode="json")})
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc)})
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
