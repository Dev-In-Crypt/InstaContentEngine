"""Integration tests for scheduling / insights / regenerate-field / grid."""
from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_content_engine, get_db
from main import app
from models.database import Base, Post as PostModel, Slide as SlideModel
from services.content_engine import ContentEngine

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads" / "posts"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'pub.db'}"


@pytest.fixture
def seeded(db_url):
    post_id = str(uuid.uuid4())

    async def _setup():
        eng = create_async_engine(db_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SM = async_sessionmaker(eng, expire_on_commit=False)
        async with SM() as s:
            s.add(PostModel(
                id=post_id, topic="Running tips", format="single", status="preview",
                caption="Run every day.", hashtags=["#run"], seo_keywords=["running"],
                cta="Follow!", hook="Run daily.", platform="instagram",
                template_style="branded_card",
            ))
            path = UPLOADS_DIR / post_id / "slide_1.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_jpeg())
            s.add(SlideModel(post_id=post_id, slide_number=1, image_source="stock",
                             image_path=str(path)))
            await s.commit()
        await eng.dispose()

    asyncio.run(_setup())
    yield post_id
    p = UPLOADS_DIR / post_id
    if (p / "slide_1.jpg").exists():
        (p / "slide_1.jpg").unlink()
    if p.exists():
        try: p.rmdir()
        except OSError: pass


@pytest.fixture
def client(db_url):
    eng = create_async_engine(db_url)
    asyncio.run(_ensure(eng))
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    fake_engine = AsyncMock(spec=ContentEngine)
    fake_engine.caption_gen = AsyncMock()
    fake_engine.caption_gen.regenerate_field.return_value = ["Variant 1.", "Variant 2."]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_content_engine] = lambda: fake_engine
    app.state.sessionmaker = SM   # for publish_now / scheduler paths

    tc = TestClient(app)
    tc.fake_engine = fake_engine
    yield tc

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_content_engine, None)
    asyncio.run(eng.dispose())


async def _ensure(eng):
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── regenerate-field ────────────────────────────────────────────────────────

def test_regenerate_field_returns_variants(client, seeded):
    res = client.post(f"/api/posts/{seeded}/regenerate-field", json={"field": "hook", "count": 2})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["field"] == "hook"
    assert body["variants"] == ["Variant 1.", "Variant 2."]


def test_regenerate_field_bad_field_422(client, seeded):
    res = client.post(f"/api/posts/{seeded}/regenerate-field", json={"field": "banana"})
    assert res.status_code == 422   # pattern validation


def test_regenerate_field_404(client):
    res = client.post(f"/api/posts/{uuid.uuid4()}/regenerate-field", json={"field": "hook"})
    assert res.status_code == 404


# ── insights ────────────────────────────────────────────────────────────────

def test_insights_refresh_409_when_not_published(client, seeded):
    res = client.post(f"/api/posts/{seeded}/insights/refresh")
    assert res.status_code == 409
    assert "not published" in res.json()["detail"].lower()


def test_insights_list_empty(client, seeded):
    res = client.get(f"/api/posts/{seeded}/insights")
    assert res.status_code == 200
    assert res.json() == []


# ── schedule ────────────────────────────────────────────────────────────────

def test_schedule_rejects_too_soon(client, seeded):
    from datetime import datetime, timezone, timedelta
    soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    res = client.post(f"/api/posts/{seeded}/schedule", json={"publish_at": soon})
    assert res.status_code == 400


def test_schedule_and_unschedule(client, seeded, monkeypatch):
    # Avoid real APScheduler jobstore — stub the scheduler helpers.
    import services.scheduler as sched
    monkeypatch.setattr(sched, "schedule_publish", lambda pid, when: None)
    monkeypatch.setattr(sched, "cancel_publish", lambda pid: True)

    from datetime import datetime, timezone, timedelta
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    res = client.post(f"/api/posts/{seeded}/schedule", json={"publish_at": when})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "scheduled"
    assert body["scheduled_at"] is not None

    res = client.delete(f"/api/posts/{seeded}/schedule")
    assert res.status_code == 200
    assert res.json()["scheduled_at"] is None


# ── grid / list ─────────────────────────────────────────────────────────────

def test_list_posts_has_thumb_and_status(client, seeded):
    res = client.get("/api/posts")
    assert res.status_code == 200
    posts = res.json()
    row = next(p for p in posts if p["id"] == seeded)
    assert row["thumb_url"].endswith("/slides/1/image")
    assert row["status"] == "preview"
