import asyncio
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import (
    get_content_engine, get_current_user, get_db, get_settings, load_brand_config,
    owned_post, require_token, require_verified,
)
from services.brand_engine import PillowBrandEngine
from config import Settings
from models.database import (
    Post as PostModel, Slide as SlideModel, TrendIdea as TrendIdeaModel,
    PostInsight as PostInsightModel, User as UserModel,
)
from models.schemas import (
    CaptionUpdate, GenerateRequest, ImageSource, OverlayUpdateRequest, Platform,
    PostInsightSchema, PostPreview, PostStatus, PostSummary, RegenFieldRequest,
    RegenFieldResponse, ReplaceSlideRequest, ScheduleRequest, SlidePreview,
    PublishResult,
)
from services.content_engine import ContentEngine, GeneratedPost
from services.pillars import classify_pillar, pillar_mix, suggest_today
from services.image_router import SlideImageConfig
from services.instagram import InstagramPublisher
from services.openrouter import OpenRouterError
from services.stock import StockError

router = APIRouter(prefix="/api/posts", tags=["posts"])

UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads" / "posts"


def _preview_opts():
    """Eager-load options for the full PostPreview shape (slides + trend source)."""
    return (
        selectinload(PostModel.slides),
        selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media),
    )


def _slide_path(post_id: str, slide_num: int) -> Path:
    return UPLOADS_DIR / post_id / f"slide_{slide_num}.jpg"


def _slide_raw_path(post_id: str, slide_num: int) -> Path:
    """Unbranded background, kept around so PUT /overlay can re-render without re-fetching."""
    return UPLOADS_DIR / post_id / f"slide_{slide_num}_raw.jpg"


def _build_slide_preview(post: PostModel, slide: SlideModel, cache_bust: bool = False) -> SlidePreview:
    """Single source of truth for SlidePreview shape, used by /generate, /regenerate,
    /upload, /overlay and the GET endpoints."""
    height = 1350 if (post.template_style or "branded_card") == "branded_card" else 1080
    rp = slide.render_params or {}
    url = f"/api/posts/{post.id}/slides/{slide.slide_number}/image"
    if cache_bust:
        url += f"?t={int(datetime.now(timezone.utc).timestamp())}"
    return SlidePreview(
        slide_number=slide.slide_number,
        image_url=url,
        image_source=ImageSource(slide.image_source),
        width=1080,
        height=height,
        attribution=slide.attribution,
        overlay_text=rp.get("overlay_text"),
        niche_text=rp.get("niche_text"),
        original_overlay_text=slide.original_overlay_text,
        original_niche_text=slide.original_niche_text,
        has_raw_image=bool(slide.raw_image_path and Path(slide.raw_image_path).exists()),
    )


def _to_preview(post: PostModel) -> PostPreview:
    slides = [
        _build_slide_preview(post, s)
        for s in sorted(post.slides, key=lambda s: s.slide_number)
    ]
    trend = getattr(post, "trend_idea", None)
    source = trend.source_media if (trend and getattr(trend, "source_media", None)) else None
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
        sources=post.sources or [],
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        schedule_error=post.schedule_error,
        instagram_media_id=post.instagram_media_id,
    )


async def _persist(
    generated: GeneratedPost, db: AsyncSession, template_style: str = "branded_card",
    trend_idea_id: Optional[str] = None, user_id: Optional[str] = None,
) -> PostModel:
    post_dir = UPLOADS_DIR / generated.id
    post_dir.mkdir(parents=True, exist_ok=True)

    db_post = PostModel(
        id=generated.id,
        user_id=user_id,
        topic=generated.topic,
        format=generated.format.value,
        status="preview",
        caption=generated.caption,
        hashtags=generated.hashtags,
        seo_keywords=generated.seo_keywords,
        sources=generated.sources,
        cta=generated.cta,
        hook=generated.hook,
        alt_text=generated.alt_text,
        platform=generated.platform.value,
        template_style=template_style,
        trend_idea_id=trend_idea_id,
        text_model=generated.text_model_used,
        image_model=generated.image_model_used,
        pillar=classify_pillar(generated.topic, generated.caption),
    )
    db.add(db_post)

    for slide in generated.slides:
        path = _slide_path(generated.id, slide.slide_number)
        path.write_bytes(slide.image_bytes)
        raw_path_str: Optional[str] = None
        if slide.raw_bytes:
            raw_path = _slide_raw_path(generated.id, slide.slide_number)
            raw_path.write_bytes(slide.raw_bytes)
            raw_path_str = str(raw_path)
        rp = slide.render_params or {}
        db.add(SlideModel(
            post_id=generated.id,
            slide_number=slide.slide_number,
            image_source=slide.image_source.value,
            image_path=str(path),
            search_query=slide.search_query,
            gen_prompt=slide.gen_prompt,
            attribution=slide.attribution,
            render_params=slide.render_params,
            raw_image_path=raw_path_str,
            original_overlay_text=rp.get("overlay_text"),
            original_niche_text=rp.get("niche_text"),
        ))

    await db.commit()

    result = await db.execute(
        select(PostModel)
        .where(PostModel.id == generated.id)
        .options(
            selectinload(PostModel.slides),
            selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media),
        )
    )
    return result.scalar_one()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/generate")
async def generate_post(
    request: GenerateRequest,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> StreamingResponse:
    slide_configs: Optional[list[SlideImageConfig]] = None
    if request.slides:
        slide_configs = [
            SlideImageConfig(
                slide_number=s.slide_number,
                image_source=s.image_source,
                search_query=s.search_query,
                gen_prompt=s.gen_prompt,
                gen_model=s.gen_model,
                canva_template_id=s.canva_template_id,
                page_number=s.page_number,
            )
            for s in request.slides
        ]

    text_model = request.text_model or settings.default_text_model
    image_model = request.image_model or settings.default_image_model

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(message: str) -> None:
            await queue.put({"type": "progress", "message": message})

        async def run() -> None:
            try:
                brand_cfg = await load_brand_config(db, request.brand_config_id)
                engine.brand_engine = PillowBrandEngine(brand_cfg)
                generated = await engine.generate_post(
                    topic=request.topic,
                    format=request.format,
                    text_model=text_model,
                    image_model=image_model,
                    default_image_source=request.default_image_source,
                    slide_configs=slide_configs,
                    tone=request.tone,
                    niche=request.niche,
                    target_audience=request.target_audience,
                    additional_instructions=request.additional_instructions,
                    apply_branding=request.apply_branding,
                    platform=request.platform,
                    length_tier=request.length_tier,
                    template_style=request.template_style,
                    niche_box_color=request.niche_box_color,
                    show_logo=request.show_logo,
                    progress=progress,
                )
                await progress("Saving to database...")
                db_post = await _persist(
                    generated, db, request.template_style.value,
                    trend_idea_id=request.trend_idea_id, user_id=user.id,
                )
                preview = _to_preview(db_post)
                # Persist any buffered LLM usage from this generation.
                try:
                    from api.routes.admin import _flush_usage
                    await _flush_usage(db)
                except Exception:
                    pass
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


@router.get("", response_model=list[PostSummary])
async def list_posts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> list[PostSummary]:
    stmt = select(PostModel).order_by(PostModel.created_at.desc()).options(selectinload(PostModel.slides))
    if not user.is_local:
        stmt = stmt.where(PostModel.user_id == user.id)
    result = await db.execute(stmt)
    posts = result.scalars().all()
    out = []
    for p in posts:
        first = min(p.slides, key=lambda s: s.slide_number) if p.slides else None
        thumb = f"/api/posts/{p.id}/slides/{first.slide_number}/image" if first else None
        out.append(PostSummary(
            id=p.id,
            topic=p.topic,
            format=p.format,
            status=PostStatus(p.status),
            thumb_url=thumb,
            scheduled_at=p.scheduled_at,
            published_at=p.published_at,
            created_at=p.created_at or datetime.now(timezone.utc),
        ))
    return out


@router.get("/{post_id}", response_model=PostPreview)
async def get_post(
    post_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PostPreview:
    post = await owned_post(db, post_id, user, options=_preview_opts())
    return _to_preview(post)


@router.put("/{post_id}/caption", response_model=PostPreview)
async def update_caption(
    post_id: str,
    update: CaptionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PostPreview:
    post = await owned_post(db, post_id, user, options=_preview_opts())
    if update.caption is not None:
        post.caption = update.caption
    if update.hashtags is not None:
        post.hashtags = update.hashtags
    if update.cta is not None:
        post.cta = update.cta
    if update.seo_keywords is not None:
        post.seo_keywords = update.seo_keywords
    await db.commit()
    post = await owned_post(db, post_id, user, options=_preview_opts())
    return _to_preview(post)


@router.post("/{post_id}/export")
async def export_post(
    post_id: str,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> StreamingResponse:
    post = await owned_post(db, post_id, user, options=_preview_opts())

    images = []
    for slide in sorted(post.slides, key=lambda s: s.slide_number):
        p = Path(slide.image_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Image file missing for slide {slide.slide_number}")
        images.append(p.read_bytes())

    zip_bytes = await engine.exporter.export_package(
        images=images,
        caption=post.caption or "",
        hashtags=post.hashtags or [],
        post_name=(post.topic or "post")[:50],
    )
    filename = f"{(post.topic or 'post')[:40].replace(' ', '_')}_template.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{post_id}/publish", response_model=PublishResult,
             dependencies=[Depends(require_verified)])
async def publish_post(
    post_id: str,
    req: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PublishResult:
    """Publish immediately: slides → imgbb (public URLs) → Instagram."""
    from services.publisher_flow import publish_now, PublishError
    from services.scheduler import cancel_publish
    await owned_post(db, post_id, user)   # ownership gate before touching the job/publish
    # Drop any pending scheduled job so it can't fire and double-publish.
    cancel_publish(post_id)
    sessionmaker = req.app.state.sessionmaker
    try:
        media_id = await publish_now(sessionmaker, post_id)
        row = await db.execute(select(PostModel.published_url).where(PostModel.id == post_id))
        return PublishResult(success=True, instagram_media_id=media_id,
                             published_url=row.scalar_one_or_none())
    except PublishError as e:
        return PublishResult(success=False, error=str(e))
    except Exception as e:
        return PublishResult(success=False, error=str(e))


@router.post("/{post_id}/schedule", response_model=PostPreview,
             dependencies=[Depends(require_verified)])
async def schedule_post_endpoint(
    post_id: str,
    body: ScheduleRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PostPreview:
    """Schedule a post for future publishing (10 min – 75 days ahead)."""
    from services.scheduler import schedule_publish

    post = await owned_post(db, post_id, user, options=_preview_opts())

    when = body.publish_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = (when - now).total_seconds()
    if delta < 600:
        raise HTTPException(status_code=400, detail="Schedule time must be at least 10 minutes ahead")
    if delta > 75 * 24 * 3600:
        raise HTTPException(status_code=400, detail="Schedule time must be within 75 days")

    try:
        schedule_publish(post_id, when)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Scheduler unavailable: {e}") from e

    post.status = "scheduled"
    post.scheduled_at = when
    post.schedule_error = None
    await db.commit()
    post = await owned_post(db, post_id, user, options=_preview_opts())
    return _to_preview(post)


@router.delete("/{post_id}/schedule", response_model=PostPreview)
async def unschedule_post(
    post_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PostPreview:
    from services.scheduler import cancel_publish

    post = await owned_post(db, post_id, user, options=_preview_opts())
    cancel_publish(post_id)
    if post.status == "scheduled":
        post.status = "preview"
    post.scheduled_at = None
    await db.commit()
    post = await owned_post(db, post_id, user, options=_preview_opts())
    return _to_preview(post)


@router.get("/{post_id}/slides/{slide_num}/image")
async def get_slide_image(
    post_id: str,
    slide_num: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    # Intentionally UNGATED (like get_reel_video): a browser <img src> cannot send
    # the Bearer token, so the SPA relies on this being reachable without auth. The
    # URL carries an unguessable post UUID and the image becomes public on publish
    # anyway (same posture as the imgbb URLs). Post/list/usage isolation is
    # unaffected — only raw slide bytes are reachable by UUID.
    result = await db.execute(
        select(SlideModel)
        .where(SlideModel.post_id == post_id, SlideModel.slide_number == slide_num)
    )
    slide = result.scalar_one_or_none()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")
    path = Path(slide.image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    return StreamingResponse(io.BytesIO(path.read_bytes()), media_type="image/jpeg")


# ─────────────────────────────────────────────────────────────────────────────
# Per-slide replace / upload (no need to regenerate the whole post)
# ─────────────────────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024     # 20 MB
_ACCEPTED_UPLOAD_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def _slide_with_post(
    db: AsyncSession, post_id: str, slide_num: int, user: UserModel,
) -> tuple[PostModel, SlideModel]:
    post = await owned_post(db, post_id, user, options=(selectinload(PostModel.slides),))
    slide = next((s for s in post.slides if s.slide_number == slide_num), None)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")
    return post, slide


def _rebrand_slide_bytes(
    raw_bytes: bytes,
    render_params: Optional[dict],
    brand_engine: PillowBrandEngine,
) -> bytes:
    """Re-apply the SAME branded card to a fresh image using stored render params.
    Falls back to unbranded JPEG bytes when params are missing (e.g. apply_branding=False)."""
    if not render_params or render_params.get("template_style") != "branded_card":
        return raw_bytes
    return brand_engine.create_branded_card(
        background_image=raw_bytes,
        niche_text=render_params.get("niche_text", ""),
        description_text=render_params.get("overlay_text", ""),
        niche_box_color=render_params.get("niche_box_color"),
        show_logo=render_params.get("show_logo"),
        show_niche_box=bool(render_params.get("show_niche_box", False)),
        page_number=render_params.get("page_number"),
        total_slides=render_params.get("total_slides"),
    )


@router.post(
    "/{post_id}/slides/{slide_num}/regenerate",
    response_model=SlidePreview,
)
async def regenerate_slide(
    post_id: str,
    slide_num: int,
    body: ReplaceSlideRequest,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> SlidePreview:
    """Replace a single slide's image (stock or AI) WITHOUT touching the rest of the post."""
    post, slide = await _slide_with_post(db, post_id, slide_num, user)

    # Build a SlideImageConfig from the existing slide, overridden by request body.
    image_source = body.image_source or ImageSource(slide.image_source)
    cfg = SlideImageConfig(
        slide_number=slide.slide_number,
        image_source=image_source,
        search_query=body.search_query or slide.search_query,
        stock_source=body.stock_source or "auto",
        gen_prompt=body.gen_prompt or slide.gen_prompt,
        gen_model=body.image_model or settings.default_image_model,
        page_number=slide.page_number,
    )

    try:
        result = await engine.image_router.fetch_image(cfg)
    except (OpenRouterError, StockError) as exc:
        raise HTTPException(status_code=502, detail=f"Image fetch failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if isinstance(result, tuple):
        raw_bytes, attribution = result
    else:
        raw_bytes, attribution = result, None

    # Re-apply the same branded card with stored render params.
    brand_cfg = await load_brand_config(db, post.brand_engine if isinstance(post.brand_engine, str) and len(post.brand_engine) > 20 else None)
    brand_engine = PillowBrandEngine(brand_cfg)
    branded = _rebrand_slide_bytes(raw_bytes, slide.render_params, brand_engine)

    # Overwrite the file and update the DB row.
    path = Path(slide.image_path) if slide.image_path else _slide_path(post.id, slide.slide_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(branded)
    # Persist the new raw background so PUT /overlay can re-render later.
    raw_path = _slide_raw_path(post.id, slide.slide_number)
    raw_path.write_bytes(raw_bytes)

    slide.image_source = image_source.value
    slide.search_query = cfg.search_query
    slide.gen_prompt = cfg.gen_prompt
    slide.attribution = attribution
    slide.raw_image_path = str(raw_path)
    await db.commit()
    await db.refresh(slide)
    return _build_slide_preview(post, slide, cache_bust=True)


@router.post(
    "/{post_id}/slides/{slide_num}/upload",
    response_model=SlidePreview,
)
async def upload_slide(
    post_id: str,
    slide_num: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> SlidePreview:
    """Replace a single slide with a user-uploaded image."""
    if file.content_type not in _ACCEPTED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {file.content_type!r}. Allowed: jpeg, png, webp.",
        )
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")

    post, slide = await _slide_with_post(db, post_id, slide_num, user)

    brand_cfg = await load_brand_config(db, None)
    brand_engine = PillowBrandEngine(brand_cfg)
    branded = _rebrand_slide_bytes(raw_bytes, slide.render_params, brand_engine)

    path = Path(slide.image_path) if slide.image_path else _slide_path(post.id, slide.slide_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(branded)
    # Save the uploaded image as the new raw so PUT /overlay can re-brand later.
    raw_path = _slide_raw_path(post.id, slide.slide_number)
    raw_path.write_bytes(raw_bytes)

    # Custom upload — no stock attribution, no search query.
    slide.image_source = ImageSource.STOCK.value     # reuse enum; treat upload as 'stock-like' source
    slide.search_query = None
    slide.gen_prompt = None
    slide.attribution = None
    slide.raw_image_path = str(raw_path)
    await db.commit()
    await db.refresh(slide)
    return _build_slide_preview(post, slide, cache_bust=True)


@router.put(
    "/{post_id}/slides/{slide_num}/overlay",
    response_model=SlidePreview,
)
async def update_slide_overlay(
    post_id: str,
    slide_num: int,
    body: OverlayUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> SlidePreview:
    """Re-render the overlay (niche box + description box) on top of the slide's
    stored raw image — no new image fetch. Used when the user types a new
    overlay caption in the preview UI and hits Apply."""
    post, slide = await _slide_with_post(db, post_id, slide_num, user)

    if not slide.raw_image_path:
        raise HTTPException(
            status_code=409,
            detail="No raw background stored for this slide. Click Replace first.",
        )
    raw_path = Path(slide.raw_image_path)
    if not raw_path.exists():
        raise HTTPException(status_code=409, detail="Raw background file missing on disk.")
    raw_bytes = raw_path.read_bytes()

    # Merge new overlay/niche text into the stored render_params.
    rp = dict(slide.render_params or {})
    if body.overlay_text is not None:
        rp["overlay_text"] = body.overlay_text
    if body.niche_text is not None:
        rp["niche_text"] = body.niche_text

    brand_cfg = await load_brand_config(db, None)
    brand_engine = PillowBrandEngine(brand_cfg)
    branded = _rebrand_slide_bytes(raw_bytes, rp, brand_engine)

    path = Path(slide.image_path) if slide.image_path else _slide_path(post.id, slide.slide_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(branded)

    slide.render_params = rp
    await db.commit()
    await db.refresh(slide)
    return _build_slide_preview(post, slide, cache_bust=True)


# ─────────────────────────────────────────────────────────────────────────────
# Export-to-disk (saves the ZIP straight to the OS Downloads folder)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-.\s]", "", name).strip().replace(" ", "_")
    return name[:60] or "post"


def _unique_path(folder: Path, stem: str, suffix: str) -> Path:
    """Return folder/<stem><suffix> with _2 / _3 / … appended if needed."""
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


@router.post("/{post_id}/export-to-disk")
async def export_post_to_disk(
    post_id: str,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    """Build the ZIP and save it directly to ~/Downloads. Returns the absolute path.
    Used by the desktop window (pywebview) where blob downloads don't surface a
    Save-As dialog and end up in unclear locations."""
    post = await owned_post(db, post_id, user, options=(selectinload(PostModel.slides),))

    images: list[bytes] = []
    for slide in sorted(post.slides, key=lambda s: s.slide_number):
        p = Path(slide.image_path) if slide.image_path else None
        if not p or not p.exists():
            raise HTTPException(status_code=404, detail=f"Image file missing for slide {slide.slide_number}")
        images.append(p.read_bytes())

    zip_bytes = await engine.exporter.export_package(
        images=images,
        caption=post.caption or "",
        hashtags=post.hashtags or [],
        post_name=(post.topic or "post")[:50],
    )
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename(post.topic or "post")[:40]
    out = _unique_path(downloads, stem, "_template.zip")
    out.write_bytes(zip_bytes)
    return {"path": str(out), "filename": out.name, "size_bytes": len(zip_bytes)}


@router.post("/open-folder", dependencies=[Depends(require_token)])
async def open_folder(path: str = Body(..., embed=True)) -> dict:
    """Open the OS file explorer at the given file (highlighted) or directory.
    Only allowed for paths under Downloads (defence-in-depth — desktop-only API)."""
    target = Path(path).resolve()
    downloads = (Path.home() / "Downloads").resolve()
    try:
        target.relative_to(downloads)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside the Downloads folder")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    try:
        if sys.platform == "win32":
            # /select highlights the file in Explorer
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent if target.is_file() else target)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open: {exc}") from exc
    return {"opened": str(target)}


# ─────────────────────────────────────────────────────────────────────────────
# Insights (on-demand refresh + history)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{post_id}/insights/refresh", response_model=PostInsightSchema)
async def refresh_insights(
    post_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PostInsightSchema:
    """Pull the latest Instagram metrics for a published post and store a snapshot."""
    post = await owned_post(db, post_id, user)
    if not post.instagram_media_id:
        raise HTTPException(status_code=409, detail="Post is not published to Instagram yet")
    if not settings.instagram_access_token or not settings.instagram_user_id:
        raise HTTPException(status_code=409, detail="Instagram credentials not configured")

    publisher = InstagramPublisher(
        access_token=settings.instagram_access_token,
        ig_user_id=settings.instagram_user_id,
    )
    try:
        is_video = (post.format or "").startswith("reel") or "video" in (post.format or "")
        metrics = await publisher.get_insights(post.instagram_media_id, is_video=is_video)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Insights fetch failed: {e}") from e
    finally:
        await publisher.close()

    snap = PostInsightModel(
        post_id=post.id,
        reach=metrics.get("reach"),
        impressions=metrics.get("impressions") or metrics.get("views"),
        likes=metrics.get("likes"),
        comments=metrics.get("comments"),
        saved=metrics.get("saved"),
        shares=metrics.get("shares"),
        total_interactions=metrics.get("total_interactions"),
        plays=metrics.get("plays"),
        video_views=metrics.get("views"),
        raw=metrics.get("raw"),
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return PostInsightSchema.model_validate(snap)


@router.get("/{post_id}/insights", response_model=list[PostInsightSchema])
async def list_insights(
    post_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> list[PostInsightSchema]:
    await owned_post(db, post_id, user)   # ownership gate on the parent post
    result = await db.execute(
        select(PostInsightModel).where(PostInsightModel.post_id == post_id)
        .order_by(PostInsightModel.snapshot_at.desc())
    )
    return [PostInsightSchema.model_validate(r) for r in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# Regenerate a single field (caption / hook / cta / hashtags / seo_keywords)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{post_id}/regenerate-field", response_model=RegenFieldResponse)
async def regenerate_field(
    post_id: str,
    body: RegenFieldRequest,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> RegenFieldResponse:
    """Cheap targeted regeneration: returns N alternatives for one field.
    Does not persist — the client applies a chosen variant via PUT /caption."""
    post = await owned_post(db, post_id, user)

    current = {
        "caption": post.caption,
        "hook": post.hook,
        "cta": post.cta,
        "hashtags": post.hashtags or [],
        "seo_keywords": post.seo_keywords or [],
    }.get(body.field)

    try:
        variants = await engine.caption_gen.regenerate_field(
            field=body.field,
            topic=post.topic,
            current_value=current,
            caption=post.caption or "",
            platform=Platform(post.platform or "instagram"),
            tone="professional",
            text_model=post.text_model or settings.default_text_model,
            count=body.count,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Regeneration failed: {e}") from e

    return RegenFieldResponse(field=body.field, variants=variants)


# ─────────────────────────────────────────────────────────────────────────────
# Content pillars mix + "what to post today"
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pillars/mix")
async def pillars_mix(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    stmt = select(PostModel.pillar, PostModel.topic, PostModel.caption)
    if not user.is_local:
        stmt = stmt.where(PostModel.user_id == user.id)
    result = await db.execute(stmt)
    rows = result.all()
    pillars = [
        (p if p else classify_pillar(topic, caption))
        for (p, topic, caption) in rows
    ]
    mix = pillar_mix(pillars)
    return {"pillars": mix, "suggestion": suggest_today(mix), "total": len(pillars)}


# ─────────────────────────────────────────────────────────────────────────────
# Reels — render a vertical video from the post's slides (Ken Burns), serve it,
# and publish it to Instagram (cloud mode, where the video URL is public).
# ─────────────────────────────────────────────────────────────────────────────

def _reel_path(post_id: str) -> Path:
    return UPLOADS_DIR / post_id / "reel.mp4"


@router.post("/{post_id}/reel")
async def make_reel(
    post_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> dict:
    """Render a Reel MP4 from the post's slides and store it on disk."""
    from services.video import get_video_provider, VideoError

    post = await owned_post(db, post_id, user, options=(selectinload(PostModel.slides),))

    slides = sorted(post.slides, key=lambda s: s.slide_number)
    images: list[bytes] = []
    overlays: list[str] = []
    for s in slides:
        p = Path(s.image_path) if s.image_path else None
        if not p or not p.exists():
            raise HTTPException(status_code=404, detail=f"Image missing for slide {s.slide_number}")
        images.append(p.read_bytes())
        rp = s.render_params or {}
        overlays.append(rp.get("overlay_text") or "")
    if not images:
        raise HTTPException(status_code=400, detail="No slides to build a reel from")

    provider = get_video_provider(settings.video_provider)
    try:
        mp4 = await provider.make_reel(images, overlays=overlays, duration_per=3.0)
    except VideoError as e:
        raise HTTPException(status_code=502, detail=f"Reel render failed: {e}") from e

    path = _reel_path(post.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mp4)
    post.video_path = str(path)
    await db.commit()
    ts = int(datetime.now(timezone.utc).timestamp())
    return {"video_url": f"/api/posts/{post.id}/reel/video?t={ts}", "size_bytes": len(mp4)}


@router.get("/{post_id}/reel/video")
async def get_reel_video(
    post_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    # Intentionally UNGATED: Instagram's servers fetch this URL directly (no auth
    # header possible) when publishing a Reel in cloud mode. The post_id is an
    # unguessable UUID and the content is about to be public — same posture as the
    # imgbb public image URLs used for photo publishing.
    post = await db.get(PostModel, post_id)
    if not post or not post.video_path:
        raise HTTPException(status_code=404, detail="Reel not rendered yet")
    p = Path(post.video_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Reel file missing on disk")
    return FileResponse(str(p), media_type="video/mp4", filename="reel.mp4")


@router.post("/{post_id}/publish-reel", response_model=PublishResult,
             dependencies=[Depends(require_verified)])
async def publish_reel(
    post_id: str,
    req: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserModel, Depends(get_current_user)],
) -> PublishResult:
    """Publish the rendered Reel to Instagram. Requires a publicly reachable
    video URL — only works in cloud mode (PUBLIC_BASE_URL set)."""
    from services.publisher_flow import publish_reel_now, PublishError

    post = await owned_post(db, post_id, user)
    if not post.video_path:
        raise HTTPException(status_code=409, detail="Render the reel first (Make Reel)")

    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return PublishResult(
            success=False,
            error="Reel publishing needs a public video URL. Set PUBLIC_BASE_URL "
                  "(cloud mode) — Instagram cannot fetch a video from localhost.",
        )
    video_url = f"{base}/api/posts/{post_id}/reel/video"
    try:
        media_id = await publish_reel_now(req.app.state.sessionmaker, post_id, video_url)
        return PublishResult(success=True, instagram_media_id=media_id)
    except PublishError as e:
        return PublishResult(success=False, error=str(e))
