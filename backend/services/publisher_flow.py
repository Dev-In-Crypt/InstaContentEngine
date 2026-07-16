"""Shared publish pipeline used by both immediate publish and scheduled jobs.

Flow: load post → read slide JPEGs → upload to imgbb (public URLs IG can fetch)
→ create IG media container(s) → publish → record media_id / published_at, or
mark the post failed with the error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import get_settings
from models.database import Post as PostModel
from services.image_host import ImgbbUploader
from services.instagram import InstagramPublisher


class PublishError(Exception):
    pass


async def publish_now(sessionmaker, post_id: str) -> str:
    """Publish a post to Instagram immediately. Returns the IG media id.

    Raises PublishError on any failure (and marks the post as failed in DB).
    `sessionmaker` is an async_sessionmaker (app.state.sessionmaker).
    """
    settings = get_settings()
    if not settings.instagram_access_token or not settings.instagram_user_id:
        raise PublishError("Instagram credentials not configured")
    if not settings.imgbb_api_key:
        raise PublishError("IMGBB_API_KEY not configured (needed for public image URLs)")

    async with sessionmaker() as db:
        result = await db.execute(
            select(PostModel).where(PostModel.id == post_id)
            .options(selectinload(PostModel.slides))
        )
        post = result.scalar_one_or_none()
        if not post:
            raise PublishError(f"Post {post_id} not found")

        # Idempotency: if it's already live, return the existing media id instead
        # of publishing a second time. Covers the double-click and the race where
        # a manual publish and the scheduled job both fire.
        if post.status == "published" and post.instagram_media_id:
            return post.instagram_media_id

        # Read slide images from disk in slide order.
        slides = sorted(post.slides, key=lambda s: s.slide_number)
        images: list[bytes] = []
        for s in slides:
            p = Path(s.image_path) if s.image_path else None
            if not p or not p.exists():
                await _mark_failed(db, post, f"Image missing for slide {s.slide_number}")
                raise PublishError(f"Image missing for slide {s.slide_number}")
            images.append(p.read_bytes())
        if not images:
            await _mark_failed(db, post, "No slides to publish")
            raise PublishError("No slides to publish")

        caption = f"{post.caption or ''}\n\n{' '.join(post.hashtags or [])}".strip()

        uploader = ImgbbUploader(settings.imgbb_api_key)
        publisher = InstagramPublisher(
            access_token=settings.instagram_access_token,
            ig_user_id=settings.instagram_user_id,
        )
        try:
            image_urls = await uploader.upload_many(images, name_prefix=post.id[:8])
            if len(image_urls) == 1:
                media_id = await publisher.publish_single(
                    image_url=image_urls[0], caption=caption, alt_text=post.alt_text or "",
                )
            else:
                media_id = await publisher.publish_carousel(
                    image_urls=image_urls, caption=caption,
                )
        except Exception as e:
            # Any failure (imgbb, IG, timeout, network) marks the post failed so it
            # never sits stuck 'scheduled'. ImageHostError/InstagramError carry
            # useful messages; the rest still get recorded.
            await _mark_failed(db, post, str(e))
            raise PublishError(str(e)) from e
        finally:
            await uploader.close()
            await publisher.close()

        post.status = "published"
        post.instagram_media_id = media_id
        post.published_at = datetime.now(timezone.utc)
        post.published_image_urls = image_urls
        post.schedule_error = None
        await db.commit()
        return media_id


async def publish_reel_now(sessionmaker, post_id: str, video_url: str) -> str:
    """Publish an already-rendered Reel MP4 (served at `video_url`) to Instagram.

    `video_url` must be publicly reachable by Instagram — in cloud mode this is
    PUBLIC_BASE_URL + /api/posts/{id}/reel/video. Raises PublishError on failure.
    """
    settings = get_settings()
    if not settings.instagram_access_token or not settings.instagram_user_id:
        raise PublishError("Instagram credentials not configured")

    async with sessionmaker() as db:
        result = await db.execute(select(PostModel).where(PostModel.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise PublishError(f"Post {post_id} not found")

        # Idempotency: already live → return the existing media id (mirrors
        # publish_now; guards double-click and manual+job races).
        if post.status == "published" and post.instagram_media_id:
            return post.instagram_media_id

        caption = f"{post.caption or ''}\n\n{' '.join(post.hashtags or [])}".strip()
        publisher = InstagramPublisher(
            access_token=settings.instagram_access_token,
            ig_user_id=settings.instagram_user_id,
        )
        try:
            media_id = await publisher.publish_reel(video_url=video_url, caption=caption)
        except Exception as e:
            # Any failure (IG, timeout, network) marks the post failed so it never
            # sits stuck in its prior status.
            await _mark_failed(db, post, str(e))
            raise PublishError(str(e)) from e
        finally:
            await publisher.close()

        post.status = "published"
        post.instagram_media_id = media_id
        post.published_at = datetime.now(timezone.utc)
        post.schedule_error = None
        await db.commit()
        return media_id


async def _mark_failed(db, post: PostModel, error: str) -> None:
    post.status = "failed"
    post.schedule_error = error[:1000]
    await db.commit()
