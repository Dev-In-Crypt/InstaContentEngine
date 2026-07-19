"""Schema is managed by Alembic (main._run_migrations). A fresh DB gets the
baseline; a pre-existing DB (tables but no alembic_version) is auto-stamped so
the first deploy onto the live prod DB doesn't try to recreate existing tables."""
import sqlite3

import main


def test_sync_db_url_strips_async_drivers():
    assert main._sync_db_url("sqlite+aiosqlite:///./x.db") == "sqlite:///./x.db"
    assert main._sync_db_url("postgresql+asyncpg://u:p@h/d") == "postgresql://u:p@h/d"
    assert main._sync_db_url("postgres://u:p@h/d") == "postgresql://u:p@h/d"


def test_migrations_build_full_schema_on_fresh_db(tmp_path):
    db = tmp_path / "fresh.db"
    main._run_migrations(f"sqlite:///{db}")
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "select name from sqlite_master where type='table'")}
    # baseline created the core tables + alembic's bookkeeping
    assert {"users", "posts", "slides", "user_credentials", "alembic_version"} <= tables
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(users)")}
    assert "token_version" in cols          # the newest column is in the baseline


def test_migrations_autostamp_preexisting_db(tmp_path):
    """A DB that already has the schema but no alembic_version must be stamped,
    not rebuilt (upgrade would otherwise fail creating existing tables)."""
    db = tmp_path / "existing.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email TEXT)")
    con.commit()
    con.close()

    main._run_migrations(f"sqlite:///{db}")   # must not raise despite users existing

    tables = {r[0] for r in sqlite3.connect(db).execute(
        "select name from sqlite_master where type='table'")}
    assert "alembic_version" in tables        # adopted
    ver = sqlite3.connect(db).execute("select version_num from alembic_version").fetchone()
    assert ver is not None                    # stamped at head


def test_migrations_idempotent(tmp_path):
    db = tmp_path / "twice.db"
    main._run_migrations(f"sqlite:///{db}")
    main._run_migrations(f"sqlite:///{db}")   # second run is a no-op, not an error
