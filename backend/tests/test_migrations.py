"""_apply_migrations must add missing columns and not swallow failures.

The brand_configs.show_logo column shipped as `BOOLEAN DEFAULT 1`, which is valid
in SQLite but a syntax error in Postgres (needs TRUE) — and the Postgres branch
swallowed the error with `except: pass`, so a cloud DB missing that column failed
the migration silently and 500'd later.
"""
import logging

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

import main
from models.database import Base


def test_no_boolean_default_1_in_migrations():
    """Regression guard: `DEFAULT 1` is invalid for a Postgres boolean column."""
    for table, cols in main._MIGRATIONS.items():
        for col, ddl in cols.items():
            assert "DEFAULT 1" not in ddl, f"{table}.{col} uses DEFAULT 1 (breaks Postgres)"


async def test_apply_migrations_adds_show_logo(tmp_path):
    """On a bare table (no ALTERs), the migration adds show_logo with a TRUE default."""
    from sqlalchemy import text
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with eng.begin() as conn:
        # Minimal brand_configs without the newer columns.
        await conn.execute(text("CREATE TABLE brand_configs (id VARCHAR(36) PRIMARY KEY, name TEXT)"))
        await conn.execute(text("CREATE TABLE posts (id VARCHAR(36) PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE slides (id VARCHAR(36) PRIMARY KEY)"))
        await main._apply_migrations(conn)
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(brand_configs)"))).fetchall()}
        assert "show_logo" in cols
        await conn.execute(text("INSERT INTO brand_configs (id, name) VALUES ('x', 'n')"))
        val = (await conn.execute(text("SELECT show_logo FROM brand_configs WHERE id='x'"))).scalar_one()
        assert val == 1   # DEFAULT TRUE
    await eng.dispose()


async def test_migration_failure_is_logged_not_swallowed(monkeypatch, caplog):
    """A failing ALTER on the non-sqlite branch must log, not vanish."""
    from sqlalchemy import text

    class FakeConn:
        class dialect:
            name = "postgresql"

        async def execute(self, *a, **k):
            raise RuntimeError("ALTER blew up")

    with caplog.at_level(logging.WARNING):
        await main._apply_migrations(FakeConn())   # must not raise
    assert any("failed" in r.message.lower() for r in caplog.records)


# silence unused-import lint for Base (kept for parity with other DB tests)
_ = Base
