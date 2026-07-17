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


async def test_cloud_mode_without_secret_refuses_to_start(tmp_path, monkeypatch):
    # SECRET_KEY signs JWTs and derives the credential-encryption key; the default
    # value is forgeable, so cloud must refuse to boot with it.
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set in cloud mode"):
        await _run_lifespan(
            monkeypatch, tmp_path, app_mode="cloud",
            secret_key="change-me-in-production",
        )


async def test_cloud_mode_with_secret_starts(tmp_path, monkeypatch):
    await _run_lifespan(
        monkeypatch, tmp_path, app_mode="cloud",
        secret_key="a-real-and-stable-secret-value",
    )


async def test_local_mode_default_secret_starts(tmp_path, monkeypatch):
    # Local binds to 127.0.0.1 only — the default secret is fine and must not block boot.
    await _run_lifespan(
        monkeypatch, tmp_path, app_mode="local",
        secret_key="change-me-in-production",
    )


# ── /docs gating in cloud mode ──────────────────────────────────────────────

def test_docs_urls_hidden_in_cloud():
    assert main._docs_urls("cloud") == {
        "docs_url": None, "redoc_url": None, "openapi_url": None,
    }


def test_docs_urls_default_in_local():
    assert main._docs_urls("local") == {}


def test_openapi_available_locally():
    """The app is built in local mode for tests, so docs stay on."""
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        assert c.get("/openapi.json").status_code == 200
