"""Publish flow: no duplicate publishes, no posts stuck 'scheduled' on timeout."""
import asyncio
import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.publisher_flow as pf
from config import Settings
from models.database import Base, Post as PostModel, Slide as SlideModel
from services.instagram import InstagramError, InstagramPublisher


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "red").save(buf, format="JPEG")
    return buf.getvalue()


# ── IG container timeout raises the flow's own error type ───────────────────

async def test_wait_for_container_timeout_raises_instagram_error():
    """It used to raise builtin TimeoutError, which publisher_flow doesn't catch,
    so a slow-to-process post stayed 'scheduled' forever with images already up."""
    pub = InstagramPublisher(access_token="t", ig_user_id="u")
    try:
        with pytest.raises(InstagramError):
            await pub._wait_for_container("cid", max_retries=0)
    finally:
        await pub.close()


# ── publish_now DB-backed tests ─────────────────────────────────────────────

@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'pub.db'}"


@pytest.fixture
def sessionmaker(db_url):
    eng = create_async_engine(db_url)

    async def _ensure():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_ensure())
    return async_sessionmaker(eng, expire_on_commit=False)


async def _seed(sm, **cols):
    async with sm() as s:
        s.add(PostModel(**cols))
        await s.commit()


def _fake_settings():
    return Settings(
        instagram_access_token="tok", instagram_user_id="uid", imgbb_api_key="key",
    )


class _FakePublisher:
    """Records the images/caption it was handed, returns a canned outcome."""
    def __init__(self, outcome=None, error=None):
        from services.publishing.base import PublishOutcome
        self.outcome = outcome or PublishOutcome(media_id="pub-1", permalink="https://x/1")
        self.error = error
        self.called_with = None

    async def publish(self, images, caption, alt_text=None):
        self.called_with = (images, caption, alt_text)
        if self.error:
            raise self.error
        return self.outcome

    async def close(self):
        ...


async def test_publish_now_idempotent_when_already_published(sessionmaker, monkeypatch):
    """A post already published must not be published a second time — guards the
    double-click and the manual+scheduled-job race."""
    pid = str(uuid.uuid4())
    await _seed(sessionmaker, id=pid, topic="t", format="single", status="published",
                instagram_media_id="existing-123")

    monkeypatch.setattr(pf, "get_settings", _fake_settings)

    def boom(*a, **k):
        raise AssertionError("should not build a publisher for an already-published post")

    monkeypatch.setattr(pf, "make_publisher_for", boom)

    media_id = await pf.publish_now(sessionmaker, pid)
    assert media_id == "existing-123"


async def test_publish_now_routes_to_platform_publisher(sessionmaker, monkeypatch, tmp_path):
    """publish_now hands the slide bytes to the publisher built for the post's
    platform, and records the returned id + permalink."""
    from services.publishing.base import PublishOutcome
    pid = str(uuid.uuid4())
    img_path = tmp_path / "slide_1.jpg"
    img_path.write_bytes(_jpeg())
    await _seed(sessionmaker, id=pid, topic="t", format="single", status="preview", platform="x")
    async with sessionmaker() as s:
        s.add(SlideModel(post_id=pid, slide_number=1, image_source="stock",
                         image_path=str(img_path)))
        await s.commit()

    fake = _FakePublisher(PublishOutcome(media_id="tw9", permalink="https://x.com/i/web/status/tw9"))
    seen = {}

    def factory(platform, settings, name_prefix="slide"):
        seen["platform"] = platform
        return fake

    monkeypatch.setattr(pf, "get_settings", _fake_settings)
    monkeypatch.setattr(pf, "make_publisher_for", factory)

    media_id = await pf.publish_now(sessionmaker, pid)

    assert seen["platform"] == "x"          # routed by post.platform
    assert media_id == "tw9"
    assert fake.called_with[0] == [_jpeg()]  # slide bytes handed over
    async with sessionmaker() as s:
        post = await s.get(PostModel, pid)
        assert post.status == "published"
        assert post.instagram_media_id == "tw9"
        assert post.published_url == "https://x.com/i/web/status/tw9"


async def test_publish_now_marks_failed_on_publisher_error(sessionmaker, monkeypatch, tmp_path):
    """Any publisher failure marks the post failed, not left stuck 'scheduled'."""
    pid = str(uuid.uuid4())
    img_path = tmp_path / "slide_1.jpg"
    img_path.write_bytes(_jpeg())
    await _seed(sessionmaker, id=pid, topic="t", format="single", status="scheduled")
    async with sessionmaker() as s:
        s.add(SlideModel(post_id=pid, slide_number=1, image_source="stock",
                         image_path=str(img_path)))
        await s.commit()

    fake = _FakePublisher(error=RuntimeError("platform exploded"))
    monkeypatch.setattr(pf, "get_settings", _fake_settings)
    monkeypatch.setattr(pf, "make_publisher_for", lambda *a, **k: fake)

    with pytest.raises(pf.PublishError):
        await pf.publish_now(sessionmaker, pid)

    async with sessionmaker() as s:
        post = await s.get(PostModel, pid)
        assert post.status == "failed"
        assert post.schedule_error


# ── manual publish cancels the pending scheduled job ────────────────────────

def test_publish_endpoint_cancels_scheduled_job(sessionmaker, monkeypatch):
    """A manual publish must drop the pending APScheduler job, or the job fires
    later and double-publishes (the idempotency guard is the backstop, not the
    only line of defence)."""
    from fastapi.testclient import TestClient

    import services.scheduler as sched
    from api.deps import get_settings
    from main import app

    cancelled = {}
    monkeypatch.setattr(sched, "cancel_publish", lambda pid: cancelled.setdefault("pid", pid))
    monkeypatch.setattr(pf, "publish_now", AsyncMock(return_value="media-9"))

    # Publish now verifies ownership first, so the post must exist.
    pid = str(uuid.uuid4())
    asyncio.run(_seed(sessionmaker, id=pid, topic="t", format="single", status="preview"))

    app.state.sessionmaker = sessionmaker
    app.dependency_overrides[get_settings] = lambda: Settings(api_token="")
    try:
        tc = TestClient(app)
        res = tc.post(f"/api/posts/{pid}/publish")
        assert res.status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert "pid" in cancelled   # cancel_publish was called before publishing


# ── publish_reel_now: symmetry with publish_now (X.5) ───────────────────────

async def test_publish_reel_idempotent_when_already_published(sessionmaker, monkeypatch):
    pid = str(uuid.uuid4())
    await _seed(sessionmaker, id=pid, topic="t", format="reel", status="published",
                instagram_media_id="reel-existing")
    monkeypatch.setattr(pf, "get_settings", _fake_settings)

    def boom(*a, **k):
        raise AssertionError("should not re-publish a published reel")

    monkeypatch.setattr(pf, "InstagramPublisher", boom)

    media_id = await pf.publish_reel_now(sessionmaker, pid, "https://x/v.mp4")
    assert media_id == "reel-existing"


async def test_publish_reel_marks_failed_on_non_ig_error(sessionmaker, monkeypatch):
    """A non-InstagramError (network/timeout) during the reel publish must mark
    the post failed, not leave it hanging in its current status."""
    pid = str(uuid.uuid4())
    await _seed(sessionmaker, id=pid, topic="t", format="reel", status="scheduled")
    monkeypatch.setattr(pf, "get_settings", _fake_settings)

    class FakePublisher:
        def __init__(self, *a, **k): ...
        async def publish_reel(self, *a, **k):
            raise RuntimeError("network reset")   # NOT an InstagramError
        async def close(self): ...

    monkeypatch.setattr(pf, "InstagramPublisher", FakePublisher)

    with pytest.raises(pf.PublishError):
        await pf.publish_reel_now(sessionmaker, pid, "https://x/v.mp4")

    async with sessionmaker() as s:
        post = await s.get(PostModel, pid)
        assert post.status == "failed"
        assert post.schedule_error


async def test_publish_reel_rejects_non_instagram(sessionmaker, monkeypatch):
    """Reels are Instagram-only; an X post must not go down the reel path."""
    pid = str(uuid.uuid4())
    await _seed(sessionmaker, id=pid, topic="t", format="reel", status="preview", platform="x")
    monkeypatch.setattr(pf, "get_settings", _fake_settings)
    with pytest.raises(pf.PublishError, match="Instagram only"):
        await pf.publish_reel_now(sessionmaker, pid, "https://x/v.mp4")
