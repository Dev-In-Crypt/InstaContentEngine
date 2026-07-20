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


# ── X plan (PART XXIV) ──────────────────────────────────────────────────────

def test_x_settings_default_off_then_saves(cloud_client):
    """The premium flag is per-tenant state; the composer gates 'Long post' on it."""
    h = {"Authorization": f"Bearer {_reg(cloud_client)}"}
    body = cloud_client.get("/api/settings/x", headers=h).json()
    assert body["x_premium"] is False
    assert body["tweet_char_limit"] == 250     # UI shows the same budget it enforces
    assert body["max_thread_tweets"] == 15

    assert cloud_client.put("/api/settings/x", headers=h,
                            json={"x_premium": True}).status_code == 200
    assert cloud_client.get("/api/settings/x", headers=h).json()["x_premium"] is True


def test_x_settings_is_per_user(cloud_client):
    """One tenant turning Premium on must not unlock it for anyone else."""
    def token(email):
        return cloud_client.post("/api/auth/register",
                                 json={"email": email, "password": "password123"}
                                 ).json()["access_token"]
    h1 = {"Authorization": f"Bearer {token('x1@example.com')}"}
    h2 = {"Authorization": f"Bearer {token('x2@example.com')}"}
    cloud_client.put("/api/settings/x", headers=h1, json={"x_premium": True})
    assert cloud_client.get("/api/settings/x", headers=h2).json()["x_premium"] is False


def test_x_settings_requires_auth(cloud_client):
    assert cloud_client.get("/api/settings/x").status_code in (401, 403)


# ── brand logo resolver (PART XXX) ──────────────────────────────────────────

def test_apply_user_slide_style_sets_logo_for_a_cloud_tenant():
    from services.brand_engine import BrandConfig
    from services.user_settings import apply_user_slide_style

    u = SimpleNamespace(slide_accent_color=None, slide_text_box_color=None,
                        is_local=False, logo_path="/data/logos/u1.png")
    from pathlib import Path
    cfg = apply_user_slide_style(BrandConfig(), u)
    assert cfg.logo_path == Path("/data/logos/u1.png")


def test_cloud_tenant_without_a_logo_gets_no_platform_logo():
    """A tenant's own logo, or none — the platform default must never leak."""
    from pathlib import Path
    from services.brand_engine import BrandConfig
    from services.user_settings import apply_user_slide_style

    cfg = BrandConfig(logo_path=Path("/platform/default_logo.png"))   # inherited default
    u = SimpleNamespace(slide_accent_color=None, slide_text_box_color=None,
                        is_local=False, logo_path=None)
    cfg = apply_user_slide_style(cfg, u)
    assert cfg.logo_path is None


def test_local_user_keeps_the_config_logo():
    from pathlib import Path
    from services.brand_engine import BrandConfig
    from services.user_settings import apply_user_slide_style

    cfg = BrandConfig(logo_path=Path("/desktop/logo.png"))
    u = SimpleNamespace(slide_accent_color=None, slide_text_box_color=None,
                        is_local=True, logo_path=None)
    cfg = apply_user_slide_style(cfg, u)
    assert cfg.logo_path == Path("/desktop/logo.png")


# ── brand logo API round-trip ───────────────────────────────────────────────

@pytest.fixture
def logo_root(tmp_path, monkeypatch):
    from services import logo_store
    root = tmp_path / "logos"
    monkeypatch.setattr(logo_store, "LOGO_ROOT", root)
    return root


def _png() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _token(c, email):
    return c.post("/api/auth/register",
                  json={"email": email, "password": "password123"}).json()["access_token"]


def test_logo_upload_get_and_delete(cloud_client, logo_root):
    h = {"Authorization": f"Bearer {_token(cloud_client, 'logo@example.com')}"}
    assert cloud_client.get("/api/settings/logo", headers=h).json() == {"set": False, "url": None}

    up = cloud_client.post("/api/settings/logo", headers=h,
                           files={"file": ("logo.png", _png(), "image/png")})
    assert up.status_code == 200 and up.json()["set"] is True

    got = cloud_client.get("/api/settings/logo", headers=h).json()
    assert got["set"] is True and got["url"] == "/api/settings/logo/image"
    img = cloud_client.get("/api/settings/logo/image", headers=h)
    assert img.status_code == 200 and img.content == _png()

    cloud_client.delete("/api/settings/logo", headers=h)
    assert cloud_client.get("/api/settings/logo", headers=h).json()["set"] is False
    assert cloud_client.get("/api/settings/logo/image", headers=h).status_code == 404


def test_logo_rejects_non_image(cloud_client, logo_root):
    h = {"Authorization": f"Bearer {_token(cloud_client, 'logo2@example.com')}"}
    res = cloud_client.post("/api/settings/logo", headers=h,
                            files={"file": ("x.txt", b"hello", "text/plain")})
    assert res.status_code == 415


def test_one_tenant_cannot_see_anothers_logo(cloud_client, logo_root):
    ha = {"Authorization": f"Bearer {_token(cloud_client, 'a@example.com')}"}
    hb = {"Authorization": f"Bearer {_token(cloud_client, 'b@example.com')}"}
    cloud_client.post("/api/settings/logo", headers=ha,
                      files={"file": ("logo.png", _png(), "image/png")})
    # B has uploaded nothing — must not receive A's logo.
    assert cloud_client.get("/api/settings/logo", headers=hb).json()["set"] is False
    assert cloud_client.get("/api/settings/logo/image", headers=hb).status_code == 404
