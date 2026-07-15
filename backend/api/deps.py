from functools import lru_cache
from pathlib import Path
from typing import Annotated, Optional
from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from models.database import BrandConfig as BrandConfigModel
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

def get_openrouter(settings: Annotated[Settings, Depends(get_settings)]) -> OpenRouterClient:
    return _get_openrouter(
        settings.openrouter_api_key,
        settings.openrouter_referer,
        settings.openrouter_app_title,
        settings.ssl_verify,
    )


def get_stock(settings: Annotated[Settings, Depends(get_settings)]) -> StockClient:
    return _get_stock_client(
        settings.unsplash_access_key, settings.pexels_api_key, settings.ssl_verify,
    )


def get_brand_engine() -> PillowBrandEngine:
    return _get_brand_engine()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.sessionmaker() as session:
        yield session


def require_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: str = Header(default=""),
) -> None:
    if settings.api_token and authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


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
