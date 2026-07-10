from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent          # backend/
_ROOT = _HERE.parent                   # project root/

# Search order: root .env first, then backend/.env (backend wins on conflict).
# This way the app works whether someone puts .env in the root or in backend/.
_ENV_FILES = [
    str(_ROOT / ".env"),   # project root  (lower priority)
    str(_HERE / ".env"),   # backend/      (higher priority)
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    # Core
    database_url: str = "sqlite+aiosqlite:///./insta.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "change-me-in-production"
    api_token: str = ""  # if set, all API calls require Bearer <token>

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_referer: str = "https://localhost"
    openrouter_app_title: str = "InstaContentEngine"

    # Default models (any OpenRouter model ID works here)
    default_text_model: str = "anthropic/claude-sonnet-4"
    default_image_model: str = "openai/dall-e-3"

    # Stock Photos
    unsplash_access_key: str = ""
    pexels_api_key: str = ""

    # Canva
    canva_client_id: str = ""
    canva_client_secret: str = ""
    canva_redirect_uri: str = "http://localhost:3000/auth/canva/callback"

    # Instagram / Meta
    instagram_access_token: str = ""
    instagram_user_id: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""

    # Storage
    storage_type: str = "local"  # "local" or "s3"
    storage_path: str = "./uploads"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket: str = "insta-engine-assets"
    aws_region: str = "us-east-1"

    # App
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # Trend Finder
    trend_provider: str = "business_discovery"   # business_discovery | scraper

    # Image hosting (public URLs for Instagram publishing)
    imgbb_api_key: str = ""

    # Deployment mode: "local" (desktop, scheduler runs only while app open) or
    # "cloud" (24/7 backend, scheduled posts publish even when user's PC is off)
    app_mode: str = "local"
    public_base_url: str = ""   # e.g. https://myengine.up.railway.app (cloud mode)

    # Video (Reels) generation provider: "kenburns" (local ffmpeg slideshow) or
    # "ai" (Runway/Kling/Luma — not implemented yet, stub raises).
    video_provider: str = "kenburns"


@lru_cache
def get_settings() -> Settings:
    return Settings()
