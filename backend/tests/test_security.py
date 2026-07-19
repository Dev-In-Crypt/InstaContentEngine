"""PART XX Bucket B — JWT revocation + cloud-gating of local-only FS endpoints."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base, User as UserModel


@pytest.fixture
def cloud_client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sec.db'}")

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
    client = TestClient(app)
    client.SM = SM
    yield client
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


def _register(client, email="u@example.com"):
    r = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()["access_token"]


def _bump_version(client, email="u@example.com"):
    async def _go():
        async with client.SM() as s:
            u = (await s.execute(select(UserModel).where(UserModel.email == email))).scalar_one()
            u.token_version = (u.token_version or 0) + 1
            await s.commit()
    asyncio.run(_go())


# ── B1: JWT revocation ───────────────────────────────────────────────────────

def test_token_valid_before_bump(cloud_client):
    tok = _register(cloud_client)
    assert cloud_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_token_revoked_after_version_bump(cloud_client):
    tok = _register(cloud_client)
    _bump_version(cloud_client)                       # e.g. password reset / logout-all elsewhere
    r = cloud_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401                       # stale token no longer authenticates


def test_logout_all_revokes_current_token(cloud_client):
    tok = _register(cloud_client)
    h = {"Authorization": f"Bearer {tok}"}
    assert cloud_client.post("/api/auth/logout-all", headers=h).status_code == 200
    assert cloud_client.get("/api/auth/me", headers=h).status_code == 401   # own token dies too


def test_relogin_after_bump_works(cloud_client):
    _register(cloud_client)
    # bump, then log in fresh → new token carries the new version → valid
    _bump_version(cloud_client)
    lg = cloud_client.post("/api/auth/login",
                           json={"email": "u@example.com", "password": "password123"})
    assert lg.status_code == 200
    new = lg.json()["access_token"]
    assert cloud_client.get("/api/auth/me", headers={"Authorization": f"Bearer {new}"}).status_code == 200


# ── B3: local-only FS endpoints are hidden in cloud ─────────────────────────

def test_export_to_disk_hidden_in_cloud(cloud_client):
    tok = _register(cloud_client)
    r = cloud_client.post("/api/posts/whatever/export-to-disk",
                          headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"          # from require_local, not owned_post


def test_open_folder_hidden_in_cloud(cloud_client):
    tok = _register(cloud_client)
    r = cloud_client.post("/api/posts/open-folder", json={"path": "/tmp/x"},
                          headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"
