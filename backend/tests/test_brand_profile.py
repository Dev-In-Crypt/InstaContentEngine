"""Brand profile: per-user niche/audience/brand storage + API + resolver."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base, User as UserModel
from services.user_settings import resolve_user_profile


# ── resolver (pure) ──────────────────────────────────────────────────────────

def test_resolve_profile_none_user():
    assert resolve_user_profile(None) == {
        "niche": None, "target_audience": None, "brand_name": None,
    }


def test_resolve_profile_reads_user():
    u = SimpleNamespace(niche="Bakery", target_audience="Home bakers", brand_name="Crumb")
    assert resolve_user_profile(u) == {
        "niche": "Bakery", "target_audience": "Home bakers", "brand_name": "Crumb",
    }


# ── API round-trip (cloud) ───────────────────────────────────────────────────

@pytest.fixture
def cloud_client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bp.db'}")

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


def _reg(c):
    return c.post("/api/auth/register",
                  json={"email": "p@example.com", "password": "password123"}).json()["access_token"]


def test_profile_defaults_empty_then_saves(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    g = cloud_client.get("/api/settings/profile", headers=h)
    assert g.status_code == 200
    assert g.json() == {"niche": "", "target_audience": "", "brand_name": ""}

    cloud_client.put("/api/settings/profile", headers=h,
                     json={"niche": "Artisan bakery", "target_audience": "Home bakers",
                           "brand_name": "Crumb & Co"})
    body = cloud_client.get("/api/settings/profile", headers=h).json()
    assert body["niche"] == "Artisan bakery"
    assert body["target_audience"] == "Home bakers"
    assert body["brand_name"] == "Crumb & Co"


def test_profile_blank_clears(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    cloud_client.put("/api/settings/profile", headers=h, json={"niche": "Bakery"})
    cloud_client.put("/api/settings/profile", headers=h, json={"niche": ""})
    assert cloud_client.get("/api/settings/profile", headers=h).json()["niche"] == ""


def test_slide_style_defaults_then_saves(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    g = cloud_client.get("/api/settings/slide-style", headers=h)
    assert g.status_code == 200
    body = g.json()
    assert body["accent_color"] == "" and body["text_box_color"] == ""
    assert body["default_accent_color"].startswith("#")
    assert len(body["palette"]) >= 1                 # suggested swatches for the UI

    cloud_client.put("/api/settings/slide-style", headers=h,
                     json={"accent_color": "#123456", "text_box_color": "#abcdef"})
    saved = cloud_client.get("/api/settings/slide-style", headers=h).json()
    assert saved["accent_color"] == "#123456"
    assert saved["text_box_color"] == "#abcdef"


def test_slide_style_accepts_off_palette_and_rejects_malformed(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    # any valid hex is fine — colours are per-tenant, not a fixed palette
    assert cloud_client.put("/api/settings/slide-style", headers=h,
                            json={"accent_color": "#0f9d58"}).status_code == 200
    assert cloud_client.put("/api/settings/slide-style", headers=h,
                            json={"accent_color": "royalblue"}).status_code == 422


def test_slide_style_blank_resets_to_default(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    cloud_client.put("/api/settings/slide-style", headers=h, json={"accent_color": "#123456"})
    cloud_client.put("/api/settings/slide-style", headers=h, json={"accent_color": ""})
    assert cloud_client.get("/api/settings/slide-style", headers=h).json()["accent_color"] == ""


def test_apply_user_slide_style_overlays_colors():
    from services.brand_engine import BrandConfig
    from services.user_settings import apply_user_slide_style

    cfg = apply_user_slide_style(BrandConfig(), None)          # no user → untouched
    assert cfg.niche_box_color == BrandConfig().niche_box_color

    u = SimpleNamespace(slide_accent_color="#123456", slide_text_box_color="#abcdef")
    cfg = apply_user_slide_style(BrandConfig(), u)
    assert cfg.niche_box_color == "#123456"
    assert cfg.desc_box_color == "#abcdef"

    unset = SimpleNamespace(slide_accent_color=None, slide_text_box_color=None)
    cfg = apply_user_slide_style(BrandConfig(), unset)          # unset → platform default
    assert cfg.niche_box_color == BrandConfig().niche_box_color


def test_profile_persists_on_user_row(cloud_client):
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    cloud_client.put("/api/settings/profile", headers=h, json={"niche": "Coffee roasting"})

    async def _read():
        async with cloud_client.SM() as s:
            u = (await s.execute(
                select(UserModel).where(UserModel.email == "p@example.com"))).scalar_one()
            return u.niche
    assert asyncio.run(_read()) == "Coffee roasting"
