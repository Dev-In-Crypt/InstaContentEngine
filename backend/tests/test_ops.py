"""PART XX Bucket A — backup/restore hardening + scheduled-post reconciliation."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.scheduler as sched
from api.routes.admin import _pg_command
from models.database import Base, Post as PostModel


# ── A3: DB password never in argv ────────────────────────────────────────────

def test_pg_command_moves_password_to_env():
    url, env = _pg_command("postgresql+asyncpg://insta:s3cret@db:5432/insta")
    assert "s3cret" not in url            # password stripped from the connection URL
    assert url == "postgresql://insta@db:5432/insta"
    assert env["PGPASSWORD"] == "s3cret"  # ...and passed via env instead


def test_pg_command_without_password_keeps_url():
    url, _env = _pg_command("postgresql://insta@db:5432/insta")
    assert url == "postgresql://insta@db:5432/insta"   # nothing to strip


# ── A2: dumps use --clean --if-exists so restore doesn't collide ─────────────

def test_admin_backup_pg_dump_uses_clean_flags():
    src = (Path(__file__).resolve().parents[1] / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
    assert '"--clean"' in src and '"--if-exists"' in src


def test_backup_script_uses_clean_flags():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "backup.sh").read_text(encoding="utf-8")
    assert "--clean --if-exists" in src
    assert "uploads_" in src               # also archives the uploads volume


# ── A4: reconcile stale scheduled posts on startup ──────────────────────────

@pytest.fixture
def sm(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    return async_sessionmaker(eng, expire_on_commit=False)


def _add_post(sm, **kw):
    async def _go():
        async with sm() as s:
            p = PostModel(topic="t", format="single", status="scheduled", **kw)
            s.add(p)
            await s.commit()
            return p.id
    return asyncio.run(_go())


def _status(sm, pid):
    async def _go():
        async with sm() as s:
            return (await s.get(PostModel, pid)).status
    return asyncio.run(_go())


def test_reconcile_marks_pastdue_failed(sm, monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    pid = _add_post(sm, scheduled_at=past)
    monkeypatch.setattr(sched, "get_job", lambda _id: None)   # no live job
    calls = []
    monkeypatch.setattr(sched, "schedule_publish", lambda i, w: calls.append((i, w)))
    asyncio.run(sched.reconcile_scheduled(sm))
    assert _status(sm, pid) == "failed"
    assert calls == []                                        # past-due is not re-armed


def test_reconcile_rearms_future(sm, monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    pid = _add_post(sm, scheduled_at=future)
    monkeypatch.setattr(sched, "get_job", lambda _id: None)
    calls = []
    monkeypatch.setattr(sched, "schedule_publish", lambda i, w: calls.append((i, w)))
    asyncio.run(sched.reconcile_scheduled(sm))
    assert _status(sm, pid) == "scheduled"                    # left scheduled
    assert len(calls) == 1 and calls[0][0] == pid             # ...and re-armed


def test_reconcile_skips_armed(sm, monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    pid = _add_post(sm, scheduled_at=past)
    monkeypatch.setattr(sched, "get_job", lambda _id: object())   # still has a live job
    monkeypatch.setattr(sched, "schedule_publish", lambda i, w: None)
    asyncio.run(sched.reconcile_scheduled(sm))
    assert _status(sm, pid) == "scheduled"                    # untouched
