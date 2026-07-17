"""Shared publish pipeline used by both immediate publish and scheduled jobs.

Flow: load post → read slide JPEGs → hand them to the platform's Publisher
(Instagram, X, …) → record the published id / permalink, or mark the post failed.
The platform-specific upload+publish lives in services/publishing/*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.database import Post as PostModel
from services.instagram import InstagramPublisher
from services.publishing.factory import make_publisher_for
from services.user_settings import settings_for_post_owner


class PublishError(Exception):
    pass


async def publish_now(sessionmaker, post_id: str) -> str:
    """Publish a post to its platform immediately. Returns the platform post id.

    Raises PublishError on any failure (and marks the post as failed in DB).
    `sessionmaker` is an async_sessionmaker (app.state.sessionmaker).
    """
    async with sessionmaker() as db:
        result = await db.execute(
            select(PostModel).where(PostModel.id == post_id)
            .options(selectinload(PostModel.slides))
        )
        post = result.scalar_one_or_none()
        if not post:
            raise PublishError(f"Post {post_id} not found")

        # Idempotency: if it's already live, return the existing id instead of
        # publishing a second time. Covers the double-click and the race where a
        # manual publish and the scheduled job both fire.
        if post.status == "published" and post.instagram_media_id:
            return post.instagram_media_id

        # Publish with the POST OWNER's own keys (multi-tenant), falling back to
        # the platform .env for the local desktop user / unowned posts.
        settings = await settings_for_post_owner(db, post)

        # The factory gates credentials for the post's platform and raises
        # PublishError if they're missing.
        platform = post.platform or "instagram"
        try:
            publisher = make_publisher_for(platform, settings, name_prefix=post.id[:8])
        except Exception as e:
            raise PublishError(str(e)) from e

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

        try:
            outcome = await publisher.publish(images, caption, post.alt_text or "")
        except Exception as e:
            # Any failure (upload, platform API, timeout, network) marks the post
            # failed so it never sits stuck 'scheduled'.
            await _mark_failed(db, post, str(e))
            raise PublishError(str(e)) from e
        finally:
            await publisher.close()

        post.status = "published"
        post.instagram_media_id = outcome.media_id   # platform post id (name kept for back-compat)
        post.published_url = outcome.permalink
        post.published_at = datetime.now(timezone.utc)
        post.published_image_urls = outcome.image_urls
        post.schedule_error = None
        await db.commit()
        return outcome.media_id


async def publish_reel_now(sessionmaker, post_id: str, video_url: str) -> str:
    """Publish an already-rendered Reel MP4 (served at `video_url`) to Instagram.

    `video_url` must be publicly reachable by Instagram — in cloud mode this is
    PUBLIC_BASE_URL + /api/posts/{id}/reel/video. Raises PublishError on failure.
    """
    async with sessionmaker() as db:
        result = await db.execute(select(PostModel).where(PostModel.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise PublishError(f"Post {post_id} not found")

        # Reels are Instagram-only for now; other platforms have no video path here.
        if (post.platform or "instagram") != "instagram":
            raise PublishError("Reels are supported on Instagram only")

        # Idempotency: already live → return the existing media id (mirrors
        # publish_now; guards double-click and manual+job races).
        if post.status == "published" and post.instagram_media_id:
            return post.instagram_media_id

        # Publish with the post owner's Instagram credentials (platform .env for
        # the local desktop user / unowned posts).
        settings = await settings_for_post_owner(db, post)
        if not settings.instagram_access_token or not settings.instagram_user_id:
            raise PublishError("Instagram credentials not configured")

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
