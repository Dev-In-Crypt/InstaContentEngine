"""Audit journal (Phase 5): an AuditEntry is written on approve, with the AI draft
vs the human's edits. Writing the entry is the mutation target."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.business as business_routes
from api.deps import get_content_engine, get_db, get_settings
from config import Settings
from main import app
from models.database import Base, Lead
from models.schemas import PostFormat, Platform
from services.content_engine import GeneratedPost


class _FakeEngine:
    async def generate_post(self, **kw):
        prog = kw.get("progress")
        if prog:
            await prog("generating")
        return GeneratedPost(
            id=str(uuid.uuid4()), topic=kw["topic"], format=PostFormat.SINGLE,
            caption="Original AI caption", hashtags=["#x"], cta="Go", hook="Hook",
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
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jr.db'}")

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
                        lambda user, settings, kind: ("openrouter", "m", "k"))
    yield TestClient(app)
    for dep in (get_db, get_settings, get_content_engine):
        app.dependency_overrides.pop(dep, None)
    asyncio.run(eng.dispose())


def _register(c, email):
    r = c.post("/api/auth/register",
               json={"email": email, "password": "password123", "account_type": "business"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_draft(c, h):
    c.post("/api/business/sources", headers=h, json={"url": "https://github.com/o/r"})
    lead_id = c.get("/api/business/leads", headers=h).json()[0]["id"]
    c.post(f"/api/business/leads/{lead_id}/draft", headers=h)
    return c.get("/api/business/drafts", headers=h).json()[0]["id"]


def test_approve_writes_audit_entry(client):
    h = _register(client, "a@ex.com")
    pid = _make_draft(client, h)
    # human edits the caption before approving
    client.put(f"/api/business/drafts/{pid}", headers=h, json={"caption": "Human-edited caption"})
    client.post(f"/api/business/posts/{pid}/submit", headers=h)
    client.post(f"/api/business/posts/{pid}/approve", headers=h)

    journal = client.get("/api/business/journal", headers=h).json()
    assert len(journal) == 1                         # mutation guard: no entry → fails
    e = journal[0]
    assert e["ai_draft"] == "Original AI caption"
    assert e["human_edits"] == "Human-edited caption"
    assert e["source_url"] == "https://ex.com/1"
    assert e["approved_by"] and e["approved_at"]


def test_journal_isolation(client):
    ha = _register(client, "b@ex.com")
    hb = _register(client, "c@ex.com")
    pid = _make_draft(client, ha)
    client.post(f"/api/business/posts/{pid}/submit", headers=ha)
    client.post(f"/api/business/posts/{pid}/approve", headers=ha)
    assert client.get("/api/business/journal", headers=hb).json() == []


def test_journal_period_filter(client):
    h = _register(client, "d@ex.com")
    pid = _make_draft(client, h)
    client.post(f"/api/business/posts/{pid}/submit", headers=h)
    client.post(f"/api/business/posts/{pid}/approve", headers=h)
    assert len(client.get("/api/business/journal", headers=h).json()) == 1
    # a window far in the future excludes it
    assert client.get("/api/business/journal?from=2099-01-01", headers=h).json() == []


def test_journal_export_csv_and_json(client):
    h = _register(client, "e@ex.com")
    pid = _make_draft(client, h)
    client.post(f"/api/business/posts/{pid}/submit", headers=h)
    client.post(f"/api/business/posts/{pid}/approve", headers=h)

    csv_r = client.get("/api/business/journal/export?format=csv", headers=h)
    assert csv_r.status_code == 200
    assert "text/csv" in csv_r.headers["content-type"]
    assert "attachment" in csv_r.headers["content-disposition"]
    assert "Original AI caption" in csv_r.text

    json_r = client.get("/api/business/journal/export?format=json", headers=h)
    assert json_r.status_code == 200
    assert "application/json" in json_r.headers["content-type"]
    assert len(json_r.json()) == 1
