"""Publishing frequency cap (Phase 6). The cap check in publish is the mutation
target — removing it would let a Business post publish over the limit."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.business as business_routes
import services.publisher_flow as publisher_flow
from api.deps import get_content_engine, get_db, get_settings
from config import Settings
from main import app
from models.database import Base, Lead, Post, Workspace
from models.schemas import PostFormat, Platform
from services.content_engine import GeneratedPost
from services.workspace import within_frequency_cap


# ── helper unit test (pure-ish) ──────────────────────────────────────────────

def test_within_frequency_cap_helper(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cap.db'}")

    async def _run():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SM = async_sessionmaker(eng, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with SM() as db:
            ws = Workspace(owner_user_id="u", name="w", max_per_day=2)
            db.add(ws)
            await db.commit()
            await db.refresh(ws)
            # one published today → under the cap of 2
            db.add(Post(id=str(uuid.uuid4()), user_id="u", workspace_id=ws.id,
                        topic="t", format="single", status="published",
                        published_at=now - timedelta(hours=1)))
            await db.commit()
            under = await within_frequency_cap(db, ws, now)
            # add a second → now at the cap
            db.add(Post(id=str(uuid.uuid4()), user_id="u", workspace_id=ws.id,
                        topic="t", format="single", status="published",
                        published_at=now - timedelta(hours=2)))
            await db.commit()
            at_cap = await within_frequency_cap(db, ws, now)
            # NULL cap → always allowed
            ws.max_per_day = None
            unlimited = await within_frequency_cap(db, ws, now)
        await eng.dispose()
        return under, at_cap, unlimited

    under, at_cap, unlimited = asyncio.run(_run())
    assert under is None                 # 1 < 2
    assert at_cap is not None            # 2 >= 2 → reason
    assert unlimited is None             # no cap set


# ── endpoint test (the publish gate) ─────────────────────────────────────────

class _FakeEngine:
    async def generate_post(self, **kw):
        prog = kw.get("progress")
        if prog:
            await prog("g")
        return GeneratedPost(
            id=str(uuid.uuid4()), topic=kw["topic"], format=PostFormat.SINGLE,
            caption="Cap caption", hashtags=["#x"], cta="Go", hook="Hook",
            alt_text="", slides=[], text_model_used="m", image_model_used=None,
            platform=Platform.INSTAGRAM)


async def _fake_poll_source(db, source, ssl_verify=True):
    db.add(Lead(workspace_id=source.workspace_id, source_id=source.id,
                external_id=str(uuid.uuid4()), what_happened="New pricing tier",
                source_url="https://ex.com/1", quote="Prices changed.",
                strength="worthy", reason="affects customers", status="new", raw={}))
    source.status = "ok"
    return 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'capapi.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="cloud",
                                                              require_verified_email=False)
    app.dependency_overrides[get_content_engine] = lambda: _FakeEngine()
    app.state.sessionmaker = SM
    monkeypatch.setattr(business_routes, "poll_source", _fake_poll_source)
    monkeypatch.setattr(business_routes, "resolve_ai_choice",
                        lambda user, settings, kind: ("openrouter", "m", "k"))

    async def _fake_publish(sessionmaker, post_id):
        return "mid-1"
    monkeypatch.setattr(publisher_flow, "publish_now", _fake_publish)
    yield TestClient(app), SM
    for dep in (get_db, get_settings, get_content_engine):
        app.dependency_overrides.pop(dep, None)
    asyncio.run(eng.dispose())


def _register(c, email):
    r = c.post("/api/auth/register",
               json={"email": email, "password": "password123", "account_type": "business"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _approved_post(c, h):
    c.post("/api/business/sources", headers=h, json={"url": "https://github.com/o/r"})
    lead_id = c.get("/api/business/leads", headers=h).json()[0]["id"]
    c.post(f"/api/business/leads/{lead_id}/draft", headers=h)
    pid = c.get("/api/business/drafts", headers=h).json()[0]["id"]
    c.post(f"/api/business/posts/{pid}/submit", headers=h)
    c.post(f"/api/business/posts/{pid}/approve", headers=h)
    return pid


def test_publish_blocked_over_daily_cap(client):
    c, SM = client
    h = _register(c, "cap@ex.com")
    pid = _approved_post(c, h)

    async def _setup_cap():
        async with SM() as db:
            ws = (await db.execute(select(Workspace))).scalars().first()
            ws.max_per_day = 1
            db.add(Post(id=str(uuid.uuid4()), user_id=ws.owner_user_id, workspace_id=ws.id,
                        topic="t", format="single", status="published",
                        published_at=datetime.now(timezone.utc)))
            await db.commit()
    asyncio.run(_setup_cap())

    # already 1 published today, cap is 1 → over limit
    assert c.post(f"/api/posts/{pid}/publish", headers=h).status_code == 409


def test_publish_allowed_under_cap(client):
    c, SM = client
    h = _register(c, "cap2@ex.com")
    pid = _approved_post(c, h)

    async def _set_cap():
        async with SM() as db:
            ws = (await db.execute(select(Workspace))).scalars().first()
            ws.max_per_day = 5
            await db.commit()
    asyncio.run(_set_cap())

    # no published posts yet, cap 5 → publish passes the gate (publish_now stubbed)
    assert c.post(f"/api/posts/{pid}/publish", headers=h).status_code == 200
