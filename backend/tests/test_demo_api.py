"""Public no-auth demo endpoint (Phase 1).

The route detects → fetches → selects → drafts and streams leads over SSE. Fetcher
and lead builder are stubbed (their own units test the internals); here we pin the
endpoint contract: 3–5 leads streamed, 503 with no app key, 429 over the limit,
and zero database writes (the demo is ephemeral).
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.demo as demo_routes
from api.deps import get_demo_text_provider, get_settings
from config import Settings
from main import app
from models.database import Base, Post
from services.sources.base import FetchedItem


class _FakeFetcher:
    def __init__(self, items):
        self._items = items

    async def fetch(self, url, since=None):
        return self._items


def _worthy_items(n):
    # Distinct titles (so the dedup rule doesn't collapse them) that read as worthy.
    return [FetchedItem(external_id=str(i), kind="rss", title=f"New pricing update {i}",
                        url=f"https://ex.com/{i}", published_at=None, body="We changed prices.")
            for i in range(n)]


async def _fake_build_lead(provider, item, **kw):
    return {
        "title": item.title, "source_url": item.url, "published_at": None,
        "what_happened": item.title, "why_interesting": "because", "missing": [],
        "drafts": [{"platform": "instagram", "hook": "h", "caption": "c", "cta": "go",
                    "hashtags": ["#x"], "unverified": False}],
    }


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}"
    eng = create_async_engine(db_url)

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    SM = async_sessionmaker(eng, expire_on_commit=False)
    app.state.sessionmaker = SM

    # App has a demo key → provider is non-None; stub fetcher + lead builder.
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="cloud")
    app.dependency_overrides[get_demo_text_provider] = lambda: object()
    monkeypatch.setattr(demo_routes, "get_source_fetcher",
                        lambda kind, ssl_verify=True: _FakeFetcher(_worthy_items(3)))
    monkeypatch.setattr(demo_routes, "build_lead", _fake_build_lead)

    yield TestClient(app), SM
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_demo_text_provider, None)
    asyncio.run(eng.dispose())


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_demo_streams_leads(demo_client):
    client, _ = demo_client
    resp = client.post("/api/demo/from-url", json={"url": "https://github.com/o/r"})
    assert resp.status_code == 200
    events = _events(resp)
    leads = [e for e in events if e["type"] == "lead"]
    assert len(leads) == 3                         # one per worthy item, 3–5 range
    assert any(e["type"] == "complete" for e in events)
    assert leads[0]["lead"]["drafts"][0]["hook"] == "h"


def test_demo_writes_nothing_to_db(demo_client):
    client, SM = demo_client
    client.post("/api/demo/from-url", json={"url": "https://github.com/o/r"})

    async def _count():
        async with SM() as s:
            return (await s.execute(select(func.count()).select_from(Post))).scalar()
    assert asyncio.run(_count()) == 0              # demo is ephemeral


def test_demo_503_without_app_key(demo_client):
    client, _ = demo_client
    app.dependency_overrides[get_demo_text_provider] = lambda: None
    try:
        resp = client.post("/api/demo/from-url", json={"url": "https://github.com/o/r"})
    finally:
        app.dependency_overrides[get_demo_text_provider] = lambda: object()
    assert resp.status_code == 503


def test_demo_rejects_non_http_url(demo_client):
    client, _ = demo_client
    assert client.post("/api/demo/from-url", json={"url": "ftp://x"}).status_code == 422


def test_demo_rate_limited_returns_429(demo_client):
    """Re-enable the limiter (disabled globally in tests) and exceed 3/hour."""
    client, _ = demo_client
    from api.ratelimit import limiter
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.post("/api/demo/from-url", json={"url": "https://github.com/o/r"}).status_code
                 for _ in range(5)]
    finally:
        limiter.enabled = False
    assert 429 in codes
    assert codes.count(200) <= 3
