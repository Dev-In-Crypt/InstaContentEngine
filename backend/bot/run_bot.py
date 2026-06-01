import asyncio
from config import get_settings
from services.openrouter import OpenRouterClient
from services.caption_generator import CaptionGenerator
from services.image_router import ImageRouter
from services.brand_engine import PillowBrandEngine, BrandConfig
from services.exporter import TemplateExporter
from services.stock import UnsplashClient, PexelsClient, StockClient
from services.content_engine import ContentEngine
from bot.telegram_bot import InstaBot


def build_engine(settings) -> ContentEngine:
    openrouter = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        referer=settings.openrouter_referer,
        app_title=settings.openrouter_app_title,
    )
    unsplash = UnsplashClient(settings.unsplash_access_key) if settings.unsplash_access_key else None
    pexels = PexelsClient(settings.pexels_api_key) if settings.pexels_api_key else None
    stock = StockClient(unsplash=unsplash, pexels=pexels)
    brand_engine = PillowBrandEngine(BrandConfig())
    caption_gen = CaptionGenerator(openrouter)
    image_router = ImageRouter(openrouter=openrouter, stock_client=stock)
    exporter = TemplateExporter()
    return ContentEngine(caption_gen, image_router, brand_engine, exporter)


def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    engine = build_engine(settings)
    bot = InstaBot(token=settings.telegram_bot_token, engine=engine)
    print("Bot is running. Press Ctrl+C to stop.")
    bot.run()


if __name__ == "__main__":
    main()
