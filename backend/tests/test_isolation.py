"""Data isolation (Phase C): in cloud mode a user only sees their own posts +
usage; the local desktop user sees everything."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base, LLMUsage, Post as PostModel, User as UserModel


@pytest.fixture
def sm(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'iso.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    return async_sessionmaker(eng, expire_on_commit=False)


@pytest.fixture
def cloud_client(sm):
    async def override_db():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="cloud")
    app.state.sessionmaker = sm
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def local_client(sm):
    async def override_db():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="local")
    app.state.sessionmaker = sm
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)


def _register(client, email):
    return client.post("/api/auth/register",
                       json={"email": email, "password": "password123"}).json()["access_token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed_post(sm, user_id, pid, topic="t"):
    async with sm() as s:
        s.add(PostModel(id=pid, user_id=user_id, topic=topic, format="single", status="preview"))
        await s.commit()


def _user_id(sm, email):
    from sqlalchemy import select

    async def _q():
        async with sm() as s:
            return (await s.execute(select(UserModel.id).where(UserModel.email == email))).scalar_one()
    return asyncio.run(_q())


# ── cloud: cross-user access is blocked ─────────────────────────────────────

def test_user_cannot_read_another_users_post(cloud_client, sm):
    ta = _register(cloud_client, "a@ex.com")
    _register(cloud_client, "b@ex.com")
    a_id = _user_id(sm, "a@ex.com")
    pid = str(uuid.uuid4())
    asyncio.run(_seed_post(sm, a_id, pid))

    # A sees it; B gets 404 (not 403 — existence not revealed).
    assert cloud_client.get(f"/api/posts/{pid}", headers=_hdr(ta)).status_code == 200
    tb = cloud_client.post("/api/auth/login",
                           json={"email": "b@ex.com", "password": "password123"}).json()["access_token"]
    assert cloud_client.get(f"/api/posts/{pid}", headers=_hdr(tb)).status_code == 404


def test_list_excludes_other_users_posts(cloud_client, sm):
    ta = _register(cloud_client, "a2@ex.com")
    tb = _register(cloud_client, "b2@ex.com")
    a_id = _user_id(sm, "a2@ex.com")
    pid = str(uuid.uuid4())
    asyncio.run(_seed_post(sm, a_id, pid))

    assert any(p["id"] == pid for p in cloud_client.get("/api/posts", headers=_hdr(ta)).json())
    assert cloud_client.get("/api/posts", headers=_hdr(tb)).json() == []


def test_modify_other_users_post_is_404(cloud_client, sm):
    _register(cloud_client, "a3@ex.com")
    tb = _register(cloud_client, "b3@ex.com")
    a_id = _user_id(sm, "a3@ex.com")
    pid = str(uuid.uuid4())
    asyncio.run(_seed_post(sm, a_id, pid))

    assert cloud_client.put(f"/api/posts/{pid}/caption",
                            json={"caption": "hax"}, headers=_hdr(tb)).status_code == 404


def test_usage_is_scoped_per_user(cloud_client, sm):
    ta = _register(cloud_client, "a4@ex.com")
    tb = _register(cloud_client, "b4@ex.com")
    a_id = _user_id(sm, "a4@ex.com")

    async def _seed_usage():
        async with sm() as s:
            s.add(LLMUsage(id=str(uuid.uuid4()), user_id=a_id, model="m", cost=1.5, total_tokens=100))
            await s.commit()
    asyncio.run(_seed_usage())

    assert cloud_client.get("/api/usage", headers=_hdr(ta)).json()["month"]["cost"] == 1.5
    assert cloud_client.get("/api/usage", headers=_hdr(tb)).json()["month"]["cost"] == 0.0


# ── local: single owner sees everything (desktop regression guard) ──────────

def test_local_user_sees_posts_with_any_owner(local_client, sm):
    # A post owned by some cloud user (or NULL) is still visible to the local user.
    pid = str(uuid.uuid4())
    asyncio.run(_seed_post(sm, "some-other-user", pid))
    assert local_client.get(f"/api/posts/{pid}").status_code == 200
    assert any(p["id"] == pid for p in local_client.get("/api/posts").json())


# ── _persist records the owner (write side; mutation: drop user_id) ─────────

def test_persist_records_owner(sm):
    from api.routes.posts import _persist
    from tests.test_posts_crud_api import _generated

    pid = str(uuid.uuid4())

    async def _go():
        async with sm() as db:
            post = await _persist(_generated(pid), db, user_id="owner-42")
            return post.user_id
    assert asyncio.run(_go()) == "owner-42"
