"""Business API (Phase 2): sources + leads with workspace isolation.

The workspace_id filter is the mutation guard — user B must never see or touch
user A's sources/leads.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.routes.business as business_routes
from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base
from services.sources.base import FetchedItem


class _FakeFetcher:
    def __init__(self, items):
        self._items = items

    async def fetch(self, url, since=None):
        return self._items


def _items():
    return [FetchedItem(external_id="1", kind="github_releases", title="New pricing tier",
                        url="https://ex.com/1", published_at=None, body="Prices changed.")]


@pytest.fixture
def client(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'biz.db'}")

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
    app.state.sessionmaker = SM
    monkeypatch.setattr(business_routes, "poll_source", _fake_poll_source)
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


async def _fake_poll_source(db, source, ssl_verify=True):
    """Stub the fetch/network: create one lead for the source, like a real poll."""
    from models.database import Lead
    db.add(Lead(workspace_id=source.workspace_id, source_id=source.id, external_id="1",
                what_happened="New pricing tier", source_url="https://ex.com/1",
                quote="Prices changed.", strength="worthy", reason="affects customers",
                status="new", raw={}))
    source.status = "ok"
    return 1


def _register(client, email, account_type="business"):
    r = client.post("/api/auth/register",
                    json={"email": email, "password": "password123", "account_type": account_type})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_creator_account_gets_403(client):
    h = _register(client, "creator@ex.com", account_type="creator")
    assert client.get("/api/business/sources", headers=h).status_code == 403


def test_add_source_primes_leads(client):
    h = _register(client, "a@ex.com")
    r = client.post("/api/business/sources", headers=h,
                    json={"url": "https://github.com/o/r"})
    assert r.status_code == 200
    body = r.json()
    assert body["leads_found"] == 1
    assert body["source"]["kind"] == "github_releases"
    assert len(client.get("/api/business/sources", headers=h).json()) == 1
    leads = client.get("/api/business/leads", headers=h).json()
    assert len(leads) == 1 and leads[0]["strength"] == "worthy"


def test_workspace_isolation(client):
    ha = _register(client, "a2@ex.com")
    hb = _register(client, "b2@ex.com")
    src = client.post("/api/business/sources", headers=ha,
                      json={"url": "https://github.com/o/r"}).json()["source"]
    lead_id = client.get("/api/business/leads", headers=ha).json()[0]["id"]

    # B sees none of A's data, and can't fetch/delete A's rows.
    assert client.get("/api/business/leads", headers=hb).json() == []
    assert client.get("/api/business/sources", headers=hb).json() == []
    assert client.get(f"/api/business/leads/{lead_id}", headers=hb).status_code == 404
    assert client.delete(f"/api/business/sources/{src['id']}", headers=hb).status_code == 404


def test_dismiss_and_snooze_change_status(client):
    h = _register(client, "c@ex.com")
    client.post("/api/business/sources", headers=h, json={"url": "https://github.com/o/r"})
    lead_id = client.get("/api/business/leads", headers=h).json()[0]["id"]

    assert client.post(f"/api/business/leads/{lead_id}/dismiss", headers=h).status_code == 200
    assert client.get(f"/api/business/leads/{lead_id}", headers=h).json()["status"] == "dismissed"

    assert client.post(f"/api/business/leads/{lead_id}/snooze-kind", headers=h).status_code == 200
    assert client.get(f"/api/business/leads/{lead_id}", headers=h).json()["status"] == "snoozed_kind"


def test_refresh_triggers_poll(client):
    h = _register(client, "d@ex.com")
    src = client.post("/api/business/sources", headers=h,
                      json={"url": "https://github.com/o/r"}).json()["source"]
    r = client.post(f"/api/business/sources/{src['id']}/refresh", headers=h)
    assert r.status_code == 200 and r.json()["leads_found"] == 1
