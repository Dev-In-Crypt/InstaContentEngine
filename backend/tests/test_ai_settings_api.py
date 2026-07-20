"""Per-tenant AI provider/model settings: storage, API, resolution and DI wiring."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base, User as UserModel
from services.user_settings import resolve_ai_choice


# ── resolution (pure) ────────────────────────────────────────────────────────

def test_local_user_falls_back_to_env():
    """The desktop app keeps using .env — this must not regress."""
    s = Settings(default_text_provider="openrouter", default_text_model="env/model",
                 openrouter_api_key="env-key")
    provider, model, key = resolve_ai_choice(SimpleNamespace(is_local=True), s, "text")
    assert (provider, model, key) == ("openrouter", "env/model", "env-key")


def test_cloud_user_uses_own_choice():
    s = Settings(openai_api_key="sk-user")
    user = SimpleNamespace(is_local=False, text_provider="openai", text_model="gpt-5-mini",
                           image_provider=None, image_model=None)
    assert resolve_ai_choice(user, s, "text") == ("openai", "gpt-5-mini", "sk-user")


def test_unconfigured_cloud_user_returns_no_model():
    """No platform default in cloud: the caller must raise a clear error instead."""
    user = SimpleNamespace(is_local=False, text_provider=None, text_model=None,
                           image_provider=None, image_model=None)
    provider, model, key = resolve_ai_choice(user, Settings(), "text")
    assert model is None and key == ""


def test_key_comes_from_the_chosen_provider():
    """Picking Google must not silently use the OpenRouter key."""
    s = Settings(openrouter_api_key="or-key", google_api_key="g-key")
    user = SimpleNamespace(is_local=False, image_provider="google",
                           image_model="gemini-2.5-flash-image",
                           text_provider=None, text_model=None)
    assert resolve_ai_choice(user, s, "image")[2] == "g-key"


# ── DI: text and image providers are independent ─────────────────────────────

def test_content_engine_uses_separate_providers():
    from api.deps import get_content_engine

    text, image = object(), object()
    engine = get_content_engine(text_provider=text, image_provider=image,
                                stock=None, brand_engine=None)
    assert engine.caption_gen.text_provider is text
    assert engine.image_router.image_provider is image
    assert engine.caption_gen.text_provider is not engine.image_router.image_provider


# ── API round-trip (cloud) ───────────────────────────────────────────────────

@pytest.fixture
def cloud_client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ai.db'}")

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
    c = TestClient(app)
    c.SM = SM
    yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


def _auth(c, email="ai@example.com"):
    tok = c.post("/api/auth/register",
                 json={"email": email, "password": "password123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_ai_settings_default_empty_then_saves(cloud_client):
    h = _auth(cloud_client)
    body = cloud_client.get("/api/settings/ai", headers=h).json()
    assert body["text_provider"] == "" and body["text_model"] == ""
    assert body["keys"]["openrouter"]["set"] is False      # nothing configured yet

    cloud_client.put("/api/settings/ai", headers=h, json={
        "text_provider": "openai", "text_model": "gpt-5-mini",
        "image_provider": "google", "image_model": "gemini-2.5-flash-image",
    })
    saved = cloud_client.get("/api/settings/ai", headers=h).json()
    assert saved["text_provider"] == "openai" and saved["text_model"] == "gpt-5-mini"
    assert saved["image_provider"] == "google"


def test_ai_settings_rejects_unknown_provider(cloud_client):
    h = _auth(cloud_client, "ai2@example.com")
    assert cloud_client.put("/api/settings/ai", headers=h,
                            json={"text_provider": "skynet"}).status_code == 422


def test_ai_settings_rejects_anthropic_for_images(cloud_client):
    """Anthropic has no image API — the server must refuse the impossible pairing."""
    h = _auth(cloud_client, "ai3@example.com")
    r = cloud_client.put("/api/settings/ai", headers=h, json={"image_provider": "anthropic"})
    assert r.status_code == 422
    assert "image" in r.json()["detail"].lower()


def test_ai_settings_accepts_custom_model_id(cloud_client):
    """The catalogue is a shortlist, not a whitelist — a brand-new model must work."""
    h = _auth(cloud_client, "ai4@example.com")
    cloud_client.put("/api/settings/ai", headers=h,
                     json={"text_provider": "openrouter", "text_model": "vendor/brand-new-99"})
    assert cloud_client.get("/api/settings/ai",
                            headers=h).json()["text_model"] == "vendor/brand-new-99"


def test_ai_settings_persist_on_user_row(cloud_client):
    h = _auth(cloud_client, "ai5@example.com")
    cloud_client.put("/api/settings/ai", headers=h,
                     json={"text_provider": "anthropic", "text_model": "claude-sonnet-5"})

    async def _read():
        async with cloud_client.SM() as s:
            u = (await s.execute(
                select(UserModel).where(UserModel.email == "ai5@example.com"))).scalar_one()
            return u.text_provider, u.text_model
    assert asyncio.run(_read()) == ("anthropic", "claude-sonnet-5")


def test_keys_report_set_without_leaking(cloud_client):
    h = _auth(cloud_client, "ai6@example.com")
    cloud_client.put("/api/settings/credentials", headers=h,
                     json={"anthropic_api_key": "sk-ant-supersecret"})
    keys = cloud_client.get("/api/settings/ai", headers=h).json()["keys"]
    assert keys["anthropic"]["set"] is True
    assert "supersecret" not in str(keys)          # masked only
    assert keys["anthropic"]["masked"].endswith("cret")


# ── test-connection endpoint ─────────────────────────────────────────────────

def test_ai_test_requires_configuration(cloud_client):
    h = _auth(cloud_client, "ai7@example.com")
    body = cloud_client.post("/api/settings/ai/test", headers=h, json={"kind": "text"}).json()
    assert body["ok"] is False and "provider and model" in body["message"]


def test_ai_test_reports_missing_key(cloud_client):
    h = _auth(cloud_client, "ai8@example.com")
    cloud_client.put("/api/settings/ai", headers=h,
                     json={"text_provider": "openai", "text_model": "gpt-5-mini"})
    body = cloud_client.post("/api/settings/ai/test", headers=h, json={"kind": "text"}).json()
    assert body["ok"] is False and "API key" in body["message"]


def test_ai_test_succeeds_with_working_provider(cloud_client):
    h = _auth(cloud_client, "ai9@example.com")
    cloud_client.put("/api/settings/credentials", headers=h, json={"openai_api_key": "sk-x"})
    cloud_client.put("/api/settings/ai", headers=h,
                     json={"text_provider": "openai", "text_model": "gpt-5-mini"})

    fake = AsyncMock()
    fake.generate_text = AsyncMock(return_value=("OK", []))
    fake.close = AsyncMock()
    with patch("services.ai.factory.make_text_provider", return_value=fake):
        body = cloud_client.post("/api/settings/ai/test", headers=h,
                                 json={"kind": "text"}).json()
    assert body["ok"] is True and "gpt-5-mini" in body["message"]


def test_ai_test_surfaces_provider_error(cloud_client):
    """A retired model id must be reported here, not mid-generation."""
    from services.ai.base import AIError

    h = _auth(cloud_client, "ai10@example.com")
    cloud_client.put("/api/settings/credentials", headers=h, json={"openai_api_key": "sk-x"})
    cloud_client.put("/api/settings/ai", headers=h,
                     json={"text_provider": "openai", "text_model": "dall-e-3"})

    fake = AsyncMock()
    fake.generate_text = AsyncMock(side_effect=AIError("OpenAI failed: 404 model not found"))
    fake.close = AsyncMock()
    with patch("services.ai.factory.make_text_provider", return_value=fake):
        body = cloud_client.post("/api/settings/ai/test", headers=h,
                                 json={"kind": "text"}).json()
    assert body["ok"] is False and "404" in body["message"]
