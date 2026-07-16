"""Startup guards enforced in main.lifespan."""
import pytest

import main
from config import Settings


async def _run_lifespan(monkeypatch, tmp_path, **settings_kwargs):
    monkeypatch.setattr(
        main, "settings",
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'g.db'}", **settings_kwargs),
    )
    monkeypatch.setattr("services.scheduler.init_scheduler", lambda *a, **kw: None)
    monkeypatch.setattr("services.scheduler.shutdown_scheduler", lambda *a, **kw: None)
    async with main.lifespan(main.app):
        pass


async def test_cloud_mode_without_token_refuses_to_start(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="API_TOKEN is required in cloud mode"):
        await _run_lifespan(monkeypatch, tmp_path, app_mode="cloud", api_token="")


async def test_cloud_mode_with_token_starts(tmp_path, monkeypatch):
    await _run_lifespan(monkeypatch, tmp_path, app_mode="cloud", api_token="secret")


async def test_local_mode_without_token_starts(tmp_path, monkeypatch):
    # Local binds to 127.0.0.1 only — an open token is fine and must not block boot.
    await _run_lifespan(monkeypatch, tmp_path, app_mode="local", api_token="")
