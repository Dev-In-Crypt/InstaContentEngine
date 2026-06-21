"""Integration tests for per-slide replace/upload endpoints.

Uses FastAPI's TestClient with dependency_overrides + in-memory aiosqlite so
we don't depend on the broken `test_api.py:_post_store` fixture.
"""
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


def _make_jpeg(color: str = "red", size: tuple[int, int] = (200, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def db_engine_url(tmp_path):
    """Per-test sqlite file (aiosqlite). File-based survives across the
    multiple sessions FastAPI opens per request."""
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def seeded_post(db_engine_url, tmp_path):
    """Create a post with one branded_card slide on disk + in DB.
    Returns (post_id, slide_path)."""
    post_id = str(uuid.uuid4())

    async def _setup():
        eng = create_async_engine(db_engine_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SM = async_sessionmaker(eng, expire_on_commit=False)
        async with SM() as s:
            s.add(PostModel(
                id=post_id, topic="Test post", format="single", status="preview",
                caption="c", hashtags=[], cta="x", hook="h",
                platform="instagram", template_style="branded_card",
            ))
            s.add(SlideModel(
                post_id=post_id, slide_number=1, image_source="stock",
                image_path=str(UPLOADS_DIR / post_id / "slide_1.jpg"),
                search_query="running marathon",
                attribution={"source": "unsplash", "author_name": "Old Author"},
                render_params={
                    "template_style": "branded_card",
                    "niche_text": "Running", "overlay_text": "Run hard.",
                    "show_niche_box": True, "niche_box_color": "#ff751f",
                    "show_logo": False, "page_number": None, "total_slides": None,
                },
            ))
            await s.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(_setup()) if False else asyncio.run(_setup())

    # Drop a placeholder JPEG so the slide path exists on disk.
    path = UPLOADS_DIR / post_id / "slide_1.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_jpeg("red"))

    yield post_id, path

    # Cleanup
    if path.exists():
        path.unlink()
    if path.parent.exists():
        try: path.parent.rmdir()
        except OSError: pass


@pytest.fixture
def client(db_engine_url):
    """TestClient with FastAPI dependency_overrides:
       - get_db → per-request session against the in-memory file
       - get_content_engine → fake engine with mocked image_router.fetch_image"""
    eng = create_async_engine(db_engine_url)

    async def _ensure_tables():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_ensure_tables())

    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as session:
            yield session

    # Fake engine: only image_router.fetch_image is consumed by regenerate.
    fake_engine = AsyncMock(spec=ContentEngine)
    fake_engine.image_router = AsyncMock()
    # Default: stock path returns a tuple (bytes, attribution)
    fake_engine.image_router.fetch_image.return_value = (
        _make_jpeg("green"),
        {"source": "unsplash", "author_name": "Jane Doe",
         "author_profile_url": "https://example.com/jane",
         "source_link": "https://example.com/photo"},
    )

    def override_engine():
        return fake_engine

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_content_engine] = override_engine

    tc = TestClient(app)
    tc.fake_engine = fake_engine    # expose for assertions

    yield tc

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_content_engine, None)
    asyncio.run(eng.dispose())


# ─── Regenerate endpoint ────────────────────────────────────────────────────

def test_regenerate_success_replaces_image_and_attribution(client, seeded_post):
    post_id, path = seeded_post
    old_bytes = path.read_bytes()

    res = client.post(f"/api/posts/{post_id}/slides/1/regenerate", json={})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slide_number"] == 1
    assert body["image_source"] == "stock"
    assert body["attribution"]["author_name"] == "Jane Doe"
    assert "?t=" in body["image_url"], "cache-bust query missing"
    # File on disk must be rewritten (branded re-render of the fake jpeg)
    assert path.exists() and path.read_bytes() != old_bytes


def test_regenerate_passes_search_query_override(client, seeded_post):
    post_id, _ = seeded_post
    res = client.post(
        f"/api/posts/{post_id}/slides/1/regenerate",
        json={"search_query": "trail running sunset"},
    )
    assert res.status_code == 200
    cfg = client.fake_engine.image_router.fetch_image.call_args.args[0]
    assert cfg.search_query == "trail running sunset"
    assert cfg.image_source.value == "stock"


def test_regenerate_switches_to_ai_gen(client, seeded_post):
    post_id, _ = seeded_post
    # AI path: image_router returns raw bytes, no attribution
    client.fake_engine.image_router.fetch_image.return_value = _make_jpeg("blue")
    res = client.post(
        f"/api/posts/{post_id}/slides/1/regenerate",
        json={"image_source": "ai_gen", "gen_prompt": "futuristic running bot"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["image_source"] == "ai_gen"
    assert body["attribution"] is None     # AI = no stock credit
    cfg = client.fake_engine.image_router.fetch_image.call_args.args[0]
    assert cfg.gen_prompt == "futuristic running bot"


def test_regenerate_404_for_unknown_post(client):
    res = client.post(f"/api/posts/{uuid.uuid4()}/slides/1/regenerate", json={})
    assert res.status_code == 404


def test_regenerate_404_for_unknown_slide(client, seeded_post):
    post_id, _ = seeded_post
    res = client.post(f"/api/posts/{post_id}/slides/99/regenerate", json={})
    assert res.status_code == 404


def test_regenerate_502_when_router_fails(client, seeded_post):
    from services.stock import StockError
    post_id, _ = seeded_post
    client.fake_engine.image_router.fetch_image.side_effect = StockError("API down")
    res = client.post(f"/api/posts/{post_id}/slides/1/regenerate", json={})
    assert res.status_code == 502
    assert "API down" in res.json()["detail"]


# ─── Upload endpoint ────────────────────────────────────────────────────────

def test_upload_success_overwrites_and_clears_attribution(client, seeded_post):
    post_id, path = seeded_post
    old_bytes = path.read_bytes()

    files = {"file": ("custom.jpg", _make_jpeg("yellow"), "image/jpeg")}
    res = client.post(f"/api/posts/{post_id}/slides/1/upload", files=files)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attribution"] is None       # custom upload has no credit
    assert "?t=" in body["image_url"]
    assert path.exists() and path.read_bytes() != old_bytes


def test_upload_415_for_bad_content_type(client, seeded_post):
    post_id, _ = seeded_post
    files = {"file": ("doc.txt", b"hello", "text/plain")}
    res = client.post(f"/api/posts/{post_id}/slides/1/upload", files=files)
    assert res.status_code == 415


def test_upload_413_for_oversize_file(client, seeded_post):
    post_id, _ = seeded_post
    huge = b"\xff" * (21 * 1024 * 1024)   # 21 MB > 20 MB cap
    files = {"file": ("big.jpg", huge, "image/jpeg")}
    res = client.post(f"/api/posts/{post_id}/slides/1/upload", files=files)
    assert res.status_code == 413


def test_upload_400_for_empty_file(client, seeded_post):
    post_id, _ = seeded_post
    files = {"file": ("empty.jpg", b"", "image/jpeg")}
    res = client.post(f"/api/posts/{post_id}/slides/1/upload", files=files)
    assert res.status_code == 400


def test_upload_404_for_unknown_post(client):
    files = {"file": ("x.jpg", _make_jpeg(), "image/jpeg")}
    res = client.post(f"/api/posts/{uuid.uuid4()}/slides/1/upload", files=files)
    assert res.status_code == 404
