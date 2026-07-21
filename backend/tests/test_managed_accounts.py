"""Managed accounts (Phase 7): CRUD + switch + owner isolation.

The owner_user_id filter is the mutation target — one agency must never see or
switch into another's client accounts.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base


@pytest.fixture
def client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'acct.db'}")

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
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


def _register(c, email):
    r = c.post("/api/auth/register", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_account_crud_and_switch(client):
    h = _register(client, "a@ex.com")
    assert client.get("/api/accounts", headers=h).json() == {"accounts": [], "active_account_id": None}

    aid = client.post("/api/accounts", headers=h, json={"name": "Client A"}).json()["id"]
    client.put(f"/api/accounts/{aid}", headers=h,
               json={"niche": "Fitness", "slide_accent_color": "#ff751f"})
    got = client.get(f"/api/accounts/{aid}", headers=h).json()
    assert got["name"] == "Client A" and got["niche"] == "Fitness"
    assert got["slide_accent_color"] == "#ff751f"

    lst = client.get("/api/accounts", headers=h).json()
    assert lst["accounts"] == [{"id": aid, "name": "Client A"}]

    # switch → /me reflects it
    client.post("/api/accounts/switch", headers=h, json={"account_id": aid})
    assert client.get("/api/auth/me", headers=h).json()["active_account_id"] == aid
    # switch back to Personal
    client.post("/api/accounts/switch", headers=h, json={"account_id": None})
    assert client.get("/api/auth/me", headers=h).json()["active_account_id"] is None


def test_delete_clears_active(client):
    h = _register(client, "d@ex.com")
    aid = client.post("/api/accounts", headers=h, json={"name": "X"}).json()["id"]
    client.post("/api/accounts/switch", headers=h, json={"account_id": aid})
    client.delete(f"/api/accounts/{aid}", headers=h)
    assert client.get("/api/auth/me", headers=h).json()["active_account_id"] is None
    assert client.get(f"/api/accounts/{aid}", headers=h).status_code == 404


def test_invalid_hex_rejected(client):
    h = _register(client, "hex@ex.com")
    aid = client.post("/api/accounts", headers=h, json={"name": "X"}).json()["id"]
    assert client.put(f"/api/accounts/{aid}", headers=h,
                      json={"slide_accent_color": "red"}).status_code == 422


def test_owner_isolation(client):
    ha = _register(client, "own-a@ex.com")
    hb = _register(client, "own-b@ex.com")
    aid = client.post("/api/accounts", headers=ha, json={"name": "A's client"}).json()["id"]
    # B sees none of A's accounts and can't fetch/switch/delete them
    assert client.get("/api/accounts", headers=hb).json()["accounts"] == []
    assert client.get(f"/api/accounts/{aid}", headers=hb).status_code == 404
    assert client.post("/api/accounts/switch", headers=hb, json={"account_id": aid}).status_code == 404
    assert client.delete(f"/api/accounts/{aid}", headers=hb).status_code == 404
