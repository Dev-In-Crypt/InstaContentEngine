"""Phase F deploy helpers: async DB-URL normalization + admin-email grants."""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import main
from models.database import Base, User


# ── _async_db_url ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    # Render/Heroku hand out sync-driver URLs → must become asyncpg.
    ("postgres://u:p@h:5432/db",   "postgresql+asyncpg://u:p@h:5432/db"),
    ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
    # Already-async and sqlite must be left untouched.
    ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
    ("sqlite+aiosqlite:///./insta.db", "sqlite+aiosqlite:///./insta.db"),
])
def test_async_db_url(raw, expected):
    assert main._async_db_url(raw) == expected


def test_async_db_url_is_actually_async_driver():
    # Mutation guard: a create_async_engine on the normalized URL must load asyncpg,
    # not the sync psycopg2 that create_async_engine rejects.
    eng = create_async_engine(main._async_db_url("postgresql://u:p@localhost:1/db"))
    assert eng.dialect.driver == "asyncpg"


# ── _apply_admin_emails ─────────────────────────────────────────────────────

@pytest.fixture
def sm(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'adm.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    return async_sessionmaker(eng, expire_on_commit=False)


def _is_admin(sm, email):
    async def _q():
        async with sm() as s:
            return (await s.execute(select(User.is_admin).where(User.email == email))).scalar_one()
    return asyncio.run(_q())


def test_apply_admin_emails_grants(sm):
    async def _seed():
        async with sm() as s:
            s.add(User(email="me@example.com"))
            s.add(User(email="other@example.com"))
            await s.commit()
    asyncio.run(_seed())

    asyncio.run(main._apply_admin_emails(sm, "Me@Example.com"))   # case-insensitive
    assert _is_admin(sm, "me@example.com") is True
    assert _is_admin(sm, "other@example.com") is False


def test_apply_admin_emails_empty_is_noop(sm):
    async def _seed():
        async with sm() as s:
            s.add(User(email="me@example.com"))
            await s.commit()
    asyncio.run(_seed())
    asyncio.run(main._apply_admin_emails(sm, ""))
    assert _is_admin(sm, "me@example.com") in (False, None)
