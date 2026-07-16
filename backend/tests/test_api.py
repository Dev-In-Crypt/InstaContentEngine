"""Smoke tests for the endpoints that aren't covered elsewhere.

This file keeps liveness, the model catalogue, request validation, and
stock-search query validation. Post CRUD / generate / export / slide images live
in test_posts_crud_api.py; scheduling, insights and reels in
test_publishing_api.py; slide replacement in test_slide_replace.py.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db
from main import app
from models.database import Base


@pytest.fixture
def client(tmp_path):
    """TestClient with a throwaway sqlite DB (no lifespan side effects)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    eng = create_async_engine(db_url)

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())

    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.state.sessionmaker = SM
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    asyncio.run(eng.dispose())


# ── liveness ────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── model catalogue ─────────────────────────────────────────────────────────

def test_list_text_models(client):
    resp = client.get("/api/models/text")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert all("id" in m and "name" in m and "provider" in m for m in data)


def test_list_image_models(client):
    resp = client.get("/api/models/image")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert any(m["name"] == "dall-e-3" for m in data)


def test_model_defaults(client):
    resp = client.get("/api/models/defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert "text_model" in body and "image_model" in body


# ── request validation ──────────────────────────────────────────────────────

def test_generate_post_rejects_short_topic(client):
    resp = client.post("/api/posts/generate", json={"topic": "AI", "format": "single"})
    assert resp.status_code == 422      # topic min_length=3


def test_generate_post_rejects_bad_format(client):
    resp = client.post("/api/posts/generate", json={"topic": "AI trends", "format": "nope"})
    assert resp.status_code == 422


def test_generate_post_rejects_bad_niche_color(client):
    resp = client.post("/api/posts/generate", json={
        "topic": "AI trends", "format": "single", "niche_box_color": "#abcdef",
    })
    assert resp.status_code == 422      # not in NICHE_BOX_PALETTE


# ── stock search validation ─────────────────────────────────────────────────

def test_stock_search_missing_query(client):
    resp = client.get("/api/stock/search")
    assert resp.status_code == 422


def test_stock_search_short_query(client):
    resp = client.get("/api/stock/search", params={"query": "a"})
    assert resp.status_code == 422      # min_length=2


def test_stock_search_bad_source(client):
    resp = client.get("/api/stock/search", params={"query": "running", "source": "flickr"})
    assert resp.status_code == 422      # pattern ^(unsplash|pexels)$


# ── 404s ────────────────────────────────────────────────────────────────────

def test_get_post_not_found(client):
    resp = client.get("/api/posts/does-not-exist")
    assert resp.status_code == 404


# ── C1: stock/models must require the token when one is set ─────────────────

import pytest as _pytest  # noqa: E402
from api.deps import get_settings as _get_settings  # noqa: E402
from config import Settings as _Settings  # noqa: E402


@_pytest.fixture
def token_client(client):
    app.dependency_overrides[_get_settings] = lambda: _Settings(api_token="secret")
    yield client
    app.dependency_overrides.pop(_get_settings, None)


@_pytest.mark.parametrize("path", [
    "/api/stock/search?query=run",
    "/api/models/text",
    "/api/models/image",
    "/api/models/defaults",
])
def test_protected_routes_401_without_token(token_client, path):
    assert token_client.get(path).status_code == 401


def test_protected_route_passes_with_token(token_client):
    # With the right bearer, require_token lets it through (not a 401).
    res = token_client.get("/api/models/text", headers={"Authorization": "Bearer secret"})
    assert res.status_code != 401


# ── C2: unknown /api/* must 404, not fall through to the SPA ─────────────────

def test_unknown_api_path_returns_404(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404
    assert "text/html" not in res.headers.get("content-type", "")


# ── C3: GET /api/usage must not write when nothing is buffered ──────────────

def test_usage_flush_noop_when_buffer_empty():
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, patch
    from api.routes.admin import _flush_usage

    db = AsyncMock()
    with patch("api.routes.admin.drain_usage", return_value=[]):
        _asyncio.get_event_loop().run_until_complete(_flush_usage(db)) if False else _asyncio.run(_flush_usage(db))
    db.commit.assert_not_awaited()


def test_usage_flush_commits_when_records_present():
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, patch
    from api.routes.admin import _flush_usage

    db = AsyncMock()
    rec = {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.01}
    with patch("api.routes.admin.drain_usage", return_value=[rec]):
        _asyncio.run(_flush_usage(db))
    db.commit.assert_awaited_once()
