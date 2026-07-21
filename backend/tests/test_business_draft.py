"""Business Phase 3: lead → draft + digest. The lead_id binding is the mutation guard."""
import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.business as business_routes
from api.deps import get_content_engine, get_db, get_settings
from config import Settings
from main import app
from models.database import Base, Lead, Post
from models.schemas import PostFormat, Platform
from services.content_engine import GeneratedPost


class _FakeEngine:
    async def generate_post(self, **kw):
        prog = kw.get("progress")
        if prog:
            await prog("generating")
        return GeneratedPost(
            id=str(uuid.uuid4()), topic=kw["topic"], format=PostFormat.SINGLE,
            caption="Draft caption", hashtags=["#x"], cta="Go", hook="Hook",
            alt_text="", slides=[], text_model_used="model-x", image_model_used=None,
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
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'draft.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="cloud")
    app.dependency_overrides[get_content_engine] = lambda: _FakeEngine()
    app.state.sessionmaker = SM
    monkeypatch.setattr(business_routes, "poll_source", _fake_poll_source)
    monkeypatch.setattr(business_routes, "resolve_ai_choice",
                        lambda user, settings, kind: ("openrouter", "model-x", "key"))
    yield TestClient(app), SM
    for dep in (get_db, get_settings, get_content_engine):
        app.dependency_overrides.pop(dep, None)
    asyncio.run(eng.dispose())


def _register(client, email):
    r = client.post("/api/auth/register",
                    json={"email": email, "password": "password123", "account_type": "business"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _complete(resp):
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            if ev["type"] == "complete":
                return ev["post"]
    return None


def _lead_ids(client, h):
    return [x["id"] for x in client.get("/api/business/leads", headers=h).json()]


def test_lead_to_draft_creates_linked_post(client):
    c, SM = client
    h = _register(c, "a@ex.com")
    c.post("/api/business/sources", headers=h, json={"url": "https://github.com/o/r"})
    lead_id = _lead_ids(c, h)[0]

    resp = c.post(f"/api/business/leads/{lead_id}/draft", headers=h)
    assert resp.status_code == 200
    preview = _complete(resp)
    assert preview and preview["caption"] == "Draft caption"

    async def _check():
        async with SM() as db:
            post = (await db.execute(select(Post).where(Post.lead_id == lead_id))).scalar_one()
            lead = await db.get(Lead, lead_id)
            return post, lead
    post, lead = asyncio.run(_check())
    assert post.lead_id == lead_id          # mutation guard: binding must be set
    assert post.workspace_id is not None
    assert post.source_kind == "lead"
    assert lead.status == "drafted"

    drafts = c.get("/api/business/drafts", headers=h).json()
    assert len(drafts) == 1 and drafts[0]["source_kind"] == "lead"


def test_draft_isolation(client):
    c, _ = client
    ha = _register(c, "a2@ex.com")
    hb = _register(c, "b2@ex.com")
    c.post("/api/business/sources", headers=ha, json={"url": "https://github.com/o/r"})
    lead_id = _lead_ids(c, ha)[0]
    # B can't draft A's lead, and sees no drafts.
    assert c.post(f"/api/business/leads/{lead_id}/draft", headers=hb).status_code == 404
    assert c.get("/api/business/drafts", headers=hb).json() == []


def test_digest_marks_all_leads(client):
    c, SM = client
    h = _register(c, "c@ex.com")
    src = c.post("/api/business/sources", headers=h,
                 json={"url": "https://github.com/o/r"}).json()["source"]
    c.post(f"/api/business/sources/{src['id']}/refresh", headers=h)   # a 2nd lead
    ids = _lead_ids(c, h)
    assert len(ids) == 2

    resp = c.post("/api/business/digest", headers=h, json={"lead_ids": ids})
    assert resp.status_code == 200
    assert _complete(resp) is not None

    async def _statuses():
        async with SM() as db:
            return sorted((await db.execute(select(Lead.status))).scalars().all())
    assert asyncio.run(_statuses()) == ["digested", "digested"]

    drafts = c.get("/api/business/drafts", headers=h).json()
    assert any(d["source_kind"] == "digest" for d in drafts)
