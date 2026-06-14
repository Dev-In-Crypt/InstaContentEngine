import asyncio
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import get_content_engine, get_db, get_settings, load_brand_config, require_token
from services.brand_engine import PillowBrandEngine
from config import Settings
from models.database import Post as PostModel, Slide as SlideModel, TrendIdea as TrendIdeaModel
from models.schemas import (
    CaptionUpdate, GenerateRequest, Platform, PostPreview,
    PostStatus, PostSummary, SlidePreview, PublishResult,
)
from services.content_engine import ContentEngine, GeneratedPost
from services.image_router import SlideImageConfig
from services.instagram import InstagramPublisher

router = APIRouter(prefix="/api/posts", tags=["posts"])

UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads" / "posts"


def _slide_path(post_id: str, slide_num: int) -> Path:
    return UPLOADS_DIR / post_id / f"slide_{slide_num}.jpg"


def _to_preview(post: PostModel) -> PostPreview:
    height = 1350 if (post.template_style or "branded_card") == "branded_card" else 1080
    slides = [
        SlidePreview(
            slide_number=s.slide_number,
            image_url=f"/api/posts/{post.id}/slides/{s.slide_number}/image",
            image_source=s.image_source,
            width=1080,
            height=height,
            attribution=s.attribution,
        )
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
    )


async def _persist(
    generated: GeneratedPost, db: AsyncSession, template_style: str = "branded_card",
    trend_idea_id: Optional[str] = None,
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
        trend_idea_id=trend_idea_id,
        text_model=generated.text_model_used,
        image_model=generated.image_model_used,
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
            attribution=slide.attribution,
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


@router.post("/generate", dependencies=[Depends(require_token)])
async def generate_post(
    request: GenerateRequest,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
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
                    trend_idea_id=request.trend_idea_id,
                )
                preview = _to_preview(db_post)
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


@router.get("", response_model=list[PostSummary], dependencies=[Depends(require_token)])
async def list_posts(db: Annotated[AsyncSession, Depends(get_db)]) -> list[PostSummary]:
    result = await db.execute(select(PostModel).order_by(PostModel.created_at.desc()))
    posts = result.scalars().all()
    return [
        PostSummary(
            id=p.id,
            topic=p.topic,
            format=p.format,
            status=PostStatus(p.status),
            created_at=p.created_at or datetime.now(timezone.utc),
        )
        for p in posts
    ]


@router.get("/{post_id}", response_model=PostPreview, dependencies=[Depends(require_token)])
async def get_post(post_id: str, db: Annotated[AsyncSession, Depends(get_db)]) -> PostPreview:
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id).options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _to_preview(post)


@router.put("/{post_id}/caption", response_model=PostPreview, dependencies=[Depends(require_token)])
async def update_caption(
    post_id: str,
    update: CaptionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PostPreview:
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id).options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if update.caption is not None:
        post.caption = update.caption
    if update.hashtags is not None:
        post.hashtags = update.hashtags
    if update.cta is not None:
        post.cta = update.cta
    if update.seo_keywords is not None:
        post.seo_keywords = update.seo_keywords
    await db.commit()
    result2 = await db.execute(
        select(PostModel).where(PostModel.id == post_id).options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media))
    )
    return _to_preview(result2.scalar_one())


@router.post("/{post_id}/export", dependencies=[Depends(require_token)])
async def export_post(
    post_id: str,
    engine: Annotated[ContentEngine, Depends(get_content_engine)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id).options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

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


@router.post("/{post_id}/publish", response_model=PublishResult, dependencies=[Depends(require_token)])
async def publish_post(
    post_id: str,
    req: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublishResult:
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id).options(selectinload(PostModel.slides), selectinload(PostModel.trend_idea).selectinload(TrendIdeaModel.source_media))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if not settings.instagram_access_token or not settings.instagram_user_id:
        return PublishResult(success=False, error="Instagram credentials not configured")

    base_url = str(req.base_url).rstrip("/")
    image_urls = [
        f"{base_url}/api/posts/{post_id}/slides/{s.slide_number}/image"
        for s in sorted(post.slides, key=lambda s: s.slide_number)
    ]

    try:
        publisher = InstagramPublisher(
            access_token=settings.instagram_access_token,
            ig_user_id=settings.instagram_user_id,
        )
        if len(post.slides) == 1:
            media_id = await publisher.publish_single(
                image_url=image_urls[0],
                caption=f"{post.caption or ''}\n\n{' '.join(post.hashtags or [])}",
                alt_text=post.alt_text or "",
            )
        else:
            media_id = await publisher.publish_carousel(
                image_urls=image_urls,
                caption=f"{post.caption or ''}\n\n{' '.join(post.hashtags or [])}",
            )
        await publisher.close()

        post.status = "published"
        post.instagram_media_id = media_id
        post.published_at = datetime.now(timezone.utc)
        await db.commit()

        return PublishResult(success=True, instagram_media_id=media_id)
    except Exception as e:
        return PublishResult(success=False, error=str(e))


@router.get("/{post_id}/slides/{slide_num}/image", dependencies=[Depends(require_token)])
async def get_slide_image(
    post_id: str,
    slide_num: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
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
