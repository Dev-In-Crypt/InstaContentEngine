from functools import lru_cache
from pathlib import Path
from typing import Annotated, Optional
from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from models.database import (
    BrandConfig as BrandConfigModel,
    Post as PostModel,
    User as UserModel,
)
# The acting user's id for the current async task (set in get_current_user).
# Defined in services.openrouter to avoid a circular import; re-exported here.
from services.openrouter import current_user_id
# Per-user effective Settings live in the services layer; re-exported so
# api/routes/settings.py (imports _CRED_FIELDS) and get_effective_settings work.
from services.user_settings import _CRED_FIELDS, build_settings_for_user  # noqa: F401
from services.auth import decode_access_token
from services.openrouter import OpenRouterClient
from services.caption_generator import CaptionGenerator
from services.image_router import ImageRouter
from services.brand_engine import PillowBrandEngine, BrandConfig
from services.exporter import TemplateExporter
from services.stock import UnsplashClient, PexelsClient, StockClient
from services.content_engine import ContentEngine
from services.trend_provider import (
    InstagramBusinessDiscoveryProvider, ScraperTrendProvider, TrendProvider,
)
from services.trend_adapter import TrendAdapter


# ---- leaf service singletons (created once per process) ----

@lru_cache
def _get_openrouter(api_key: str, referer: str, title: str, ssl_verify: bool = True) -> OpenRouterClient:
    return OpenRouterClient(api_key=api_key, referer=referer, app_title=title,
                            ssl_verify=ssl_verify)


@lru_cache
def _get_stock_client(unsplash_key: str, pexels_key: str, ssl_verify: bool = True) -> StockClient:
    unsplash = UnsplashClient(unsplash_key, ssl_verify=ssl_verify) if unsplash_key else None
    pexels = PexelsClient(pexels_key, ssl_verify=ssl_verify) if pexels_key else None
    return StockClient(unsplash=unsplash, pexels=pexels)


@lru_cache
def _get_brand_engine() -> PillowBrandEngine:
    return PillowBrandEngine(BrandConfig())


def _to_path(value) -> Optional[Path]:
    return Path(value) if value else None


def _row_to_brand_config(row: BrandConfigModel) -> BrandConfig:
    return BrandConfig(
        logo_path=_to_path(row.logo_path),
        primary_color=row.primary_color or BrandConfig.primary_color,
        secondary_color=row.secondary_color or BrandConfig.secondary_color,
        accent_color=row.accent_color or BrandConfig.accent_color,
        heading_font_path=_to_path(row.heading_font_path),
        body_font_path=_to_path(row.body_font_path),
        logo_position=row.logo_position or "bottom_right",
        logo_scale=row.logo_scale if row.logo_scale is not None else 0.15,
        padding=row.padding if row.padding is not None else 40,
        template_style=row.template_style or "branded_card",
        niche_box_palette=row.niche_box_palette or BrandConfig().niche_box_palette,
        niche_box_color=row.niche_box_color or "#ff751f",
        description_box_alpha=row.description_box_alpha if row.description_box_alpha is not None else 0.79,
        show_logo=row.show_logo if row.show_logo is not None else True,
    )


async def load_brand_config(
    db: AsyncSession, brand_config_id: Optional[str] = None
) -> BrandConfig:
    """Load a brand preset from the DB; falls back to the default row, then defaults."""
    stmt = select(BrandConfigModel)
    if brand_config_id:
        stmt = stmt.where(BrandConfigModel.id == brand_config_id)
    else:
        stmt = stmt.where(BrandConfigModel.is_default == True).order_by(BrandConfigModel.created_at)  # noqa: E712
    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        return BrandConfig()
    return _row_to_brand_config(row)


# ---- FastAPI dependencies ----

def get_brand_engine() -> PillowBrandEngine:
    return _get_brand_engine()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.sessionmaker() as session:
        yield session


# ---- Authentication (multi-tenant) ----

LOCAL_USER_EMAIL = "local@localhost"


async def _get_or_create_local_user(db: AsyncSession) -> UserModel:
    """The single implicit owner used in local (desktop) mode — no login there."""
    row = (await db.execute(
        select(UserModel).where(UserModel.is_local == True)  # noqa: E712
    )).scalars().first()
    if row:
        return row
    user = UserModel(email=LOCAL_USER_EMAIL, is_local=True, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: str = Header(default=""),
) -> UserModel:
    """Resolve the acting user. Local mode → the seeded local owner (no token).
    Cloud mode → decode the Bearer JWT; 401 if missing/invalid/inactive."""
    if settings.app_mode != "cloud":
        user = await _get_or_create_local_user(db)
        current_user_id.set(user.id)
        return user

    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get(UserModel, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    current_user_id.set(user.id)
    return user


async def get_effective_settings(
    user: Annotated[UserModel, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Settings:
    return await build_settings_for_user(db, user)


def require_token(user: Annotated[UserModel, Depends(get_current_user)]) -> None:
    """Thin auth gate kept under its historical name so the 40+ existing
    `dependencies=[Depends(require_token)]` sites need no churn. Authentication now
    means 'a resolvable user' — always true in local mode, JWT-gated in cloud."""
    return None


def require_admin(user: Annotated[UserModel, Depends(get_current_user)]) -> None:
    """Gate whole-database operations (backup/restore). The local desktop owner is
    admin; in cloud only a user flagged is_admin may pull everyone's data."""
    if not (user.is_local or user.is_admin):
        raise HTTPException(status_code=403, detail="Admin only")
    return None


async def owned_post(db: AsyncSession, post_id: str, user: UserModel, *, options=()) -> PostModel:
    """Fetch a post the user is allowed to touch, else 404 (not 403 — don't reveal
    that another tenant's post exists). The local desktop user owns everything, so
    the ownership filter only applies to real cloud accounts."""
    stmt = select(PostModel).where(PostModel.id == post_id)
    if not user.is_local:
        stmt = stmt.where(PostModel.user_id == user.id)
    if options:
        stmt = stmt.options(*options)
    post = (await db.execute(stmt)).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def get_openrouter(
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> OpenRouterClient:
    return _get_openrouter(
        settings.openrouter_api_key,
        settings.openrouter_referer,
        settings.openrouter_app_title,
        settings.ssl_verify,
    )


def get_stock(settings: Annotated[Settings, Depends(get_effective_settings)]) -> StockClient:
    return _get_stock_client(
        settings.unsplash_access_key, settings.pexels_api_key, settings.ssl_verify,
    )


def get_content_engine(
    openrouter: Annotated[OpenRouterClient, Depends(get_openrouter)],
    stock: Annotated[StockClient, Depends(get_stock)],
    brand_engine: Annotated[PillowBrandEngine, Depends(get_brand_engine)],
) -> ContentEngine:
    caption_gen = CaptionGenerator(openrouter)
    image_router = ImageRouter(openrouter=openrouter, stock_client=stock)
    exporter = TemplateExporter()
    return ContentEngine(caption_gen, image_router, brand_engine, exporter)


# ---- Trend Finder DI ----

@lru_cache
def _get_business_discovery_provider(token: str, ig_user_id: str) -> InstagramBusinessDiscoveryProvider:
    return InstagramBusinessDiscoveryProvider(access_token=token, ig_user_id=ig_user_id)


@lru_cache
def _get_scraper_provider() -> ScraperTrendProvider:
    return ScraperTrendProvider()


def get_trend_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrendProvider:
    source = (settings.trend_provider or "business_discovery").lower()
    if source == "scraper":
        return _get_scraper_provider()
    # Default → business_discovery
    return _get_business_discovery_provider(
        settings.instagram_access_token, settings.instagram_user_id
    )


def make_trend_provider_for(source: str, settings: Settings) -> TrendProvider:
    """Provider builder used by the router when the caller specifies a source per-request."""
    s = (source or "").lower() or settings.trend_provider or "business_discovery"
    if s == "scraper":
        return _get_scraper_provider()
    return _get_business_discovery_provider(
        settings.instagram_access_token, settings.instagram_user_id
    )


def get_trend_adapter(
    openrouter: Annotated[OpenRouterClient, Depends(get_openrouter)],
) -> TrendAdapter:
    return TrendAdapter(openrouter)
