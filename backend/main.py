import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import get_settings
from models.database import Base, BrandConfig as BrandConfigModel, User as UserModel
from models.schemas import NICHE_BOX_PALETTE
from services.http_utils import setup_logging, setup_tls
from api.deps import LOCAL_USER_EMAIL
from api.ratelimit import limiter
from api.routes import posts, models, stock, trends, admin, auth, settings as settings_routes

STATIC_DIR = Path(__file__).parent / "static"
UPLOADS_DIR = Path(__file__).parent / "uploads"

settings = get_settings()
log = logging.getLogger(__name__)

# New columns added after the initial schema. create_all does NOT alter existing
# tables, so on an already-created sqlite db these are added idempotently here.
_MIGRATIONS: dict[str, dict[str, str]] = {
    "posts": {
        "user_id": "VARCHAR(36)",
        "seo_keywords": "JSON",
        "platform": "VARCHAR(20) DEFAULT 'instagram'",
        "template_style": "VARCHAR(20) DEFAULT 'branded_card'",
        "trend_idea_id": "VARCHAR(36)",
        "sources": "JSON",
        "published_image_urls": "JSON",
        "schedule_error": "TEXT",
        "pillar": "VARCHAR(30)",
        "video_path": "TEXT",
        "published_url": "TEXT",
    },
    "slides": {
        "page_number": "INTEGER",
        "attribution": "JSON",
        "render_params": "JSON",
        "raw_image_path": "TEXT",
        "original_overlay_text": "TEXT",
        "original_niche_text": "TEXT",
    },
    "llm_usage": {
        "user_id": "VARCHAR(36)",
    },
    "users": {
        "is_admin": "BOOLEAN DEFAULT FALSE",
        "email_verified": "BOOLEAN DEFAULT FALSE",
    },
    "brand_configs": {
        "template_style": "VARCHAR(20) DEFAULT 'branded_card'",
        "niche_box_color": "VARCHAR(7) DEFAULT '#ff751f'",
        "niche_box_palette": "JSON",
        "description_box_alpha": "FLOAT DEFAULT 0.79",
        "show_logo": "BOOLEAN DEFAULT TRUE",   # TRUE, not 1 — 1 is a syntax error in Postgres
    },
}


def _async_db_url(url: str) -> str:
    """Normalize a database URL to an async driver for create_async_engine.

    Render/Heroku hand out `postgres://` or `postgresql://` (the psycopg2/sync
    driver), which create_async_engine rejects. Map both to asyncpg. sqlite and
    already-async URLs are left untouched.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


async def _apply_admin_emails(sessionmaker, emails_csv: str) -> None:
    """Grant is_admin to the configured emails (cloud has no local owner). Idempotent."""
    emails = [e.strip().lower() for e in emails_csv.split(",") if e.strip()]
    if not emails:
        return
    async with sessionmaker() as session:
        await session.execute(
            update(UserModel).where(UserModel.email.in_(emails))
            .values(is_admin=True, email_verified=True)
        )
        await session.commit()


async def _apply_migrations(conn) -> None:
    """Add any missing columns to existing tables (no migration tool).

    On sqlite we inspect via PRAGMA and ALTER. On Postgres (cloud), create_all
    already made every column on first boot, and ADD COLUMN IF NOT EXISTS is a
    safe no-op for pre-existing tables. Dialect is branched to keep both happy.
    """
    dialect = conn.dialect.name
    for table, columns in _MIGRATIONS.items():
        if dialect == "sqlite":
            existing = await conn.execute(text(f"PRAGMA table_info({table})"))
            present = {row[1] for row in existing.fetchall()}
            if not present:
                continue   # table doesn't exist yet (create_all makes it first in prod)
            for col, ddl in columns.items():
                if col not in present:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        else:
            # Postgres / others: JSON maps to JSONB is not needed here; use plain
            # types. IF NOT EXISTS keeps it idempotent without introspection.
            for col, ddl in columns.items():
                pg_ddl = ddl.replace("JSON", "JSONB")
                try:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_ddl}")
                    )
                except Exception as e:
                    # Don't hide a real failure (bad DDL, permissions) as success.
                    log.warning("Migration ADD COLUMN %s.%s failed: %s", table, col, e)


async def _seed_brand_preset(sessionmaker) -> None:
    """Insert the 'My Life My Game' brand preset if it does not exist yet."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(BrandConfigModel).where(BrandConfigModel.name == "My Life My Game")
        )
        if result.scalar_one_or_none():
            return
        session.add(BrandConfigModel(
            id=str(uuid.uuid4()),
            name="My Life My Game",
            is_default=True,
            primary_color="#0076cb",
            secondary_color="#1A4D8A",
            accent_color="#ff751f",
            logo_position="top_right",
            logo_scale=0.15,
            padding=40,
            template_style="branded_card",
            niche_box_color="#ff751f",
            niche_box_palette=NICHE_BOX_PALETTE,
            description_box_alpha=0.79,
            show_logo=True,
        ))
        await session.commit()


async def _seed_local_user(sessionmaker) -> None:
    """Local (desktop) mode owns everything under one implicit user, so the
    desktop needs no login. Insert it once; get_current_user returns it."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.is_local == True)  # noqa: E712
        )
        if result.scalar_one_or_none():
            return
        session.add(UserModel(email=LOCAL_USER_EMAIL, is_local=True, is_active=True,
                              email_verified=True))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    # Optional error monitoring.
    if settings.sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
            log.info("Sentry initialized")
        except Exception as exc:  # pragma: no cover
            log.warning("Sentry init failed: %s", exc)
    # Must precede every outbound connection, the database included.
    setup_tls()

    # In cloud mode the app is publicly reachable and multi-tenant: SECRET_KEY
    # signs every session JWT AND derives the key that encrypts users' stored API
    # keys. With the default value both are trivially forgeable/decryptable, so a
    # real one is mandatory. Refuse to start otherwise.
    if settings.app_mode == "cloud" and settings.secret_key == "change-me-in-production":
        raise RuntimeError(
            "SECRET_KEY must be set in cloud mode: it signs auth tokens and "
            "encrypts stored user credentials. Set a strong, stable SECRET_KEY "
            "in the environment (rotating it later logs everyone out and orphans "
            "all stored keys)."
        )

    engine = create_async_engine(_async_db_url(settings.database_url), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_brand_preset(app.state.sessionmaker)
    if settings.app_mode != "cloud":
        await _seed_local_user(app.state.sessionmaker)
    await _apply_admin_emails(app.state.sessionmaker, settings.admin_emails)
    UPLOADS_DIR.mkdir(exist_ok=True)

    # Scheduled publishing (APScheduler). In local mode this only fires while
    # the app is open; in cloud mode it runs 24/7. Failures here must not block
    # the app from starting.
    try:
        from services.scheduler import init_scheduler
        init_scheduler(settings.database_url, app.state.sessionmaker)
    except Exception as exc:  # pragma: no cover
        import logging
        logging.getLogger(__name__).warning("Scheduler init failed: %s", exc)

    yield

    try:
        from services.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass
    await engine.dispose()


def _docs_urls(app_mode: str) -> dict:
    """Hide Swagger/ReDoc/OpenAPI on a public deployment; keep them for local dev."""
    if app_mode == "cloud":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title="Instagram Content Engine",
    description="AI-powered Instagram post generation and publishing system",
    version="1.0.0",
    lifespan=lifespan,
    **_docs_urls(settings.app_mode),
)

# Rate limiting (per-IP). 429 on exceed via slowapi's handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(settings_routes.router)
app.include_router(posts.router)
app.include_router(models.router)
app.include_router(stock.router)
app.include_router(trends.router)
app.include_router(admin.router)

# Serve built frontend assets (images, fonts, etc.) at /static/*
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Standalone legal pages (linked from the landing footer + the sign-up screen).
# Served explicitly so they don't fall through to the SPA.
@app.get("/terms", include_in_schema=False)
async def terms_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/privacy", include_in_schema=False)
async def privacy_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "privacy.html")


# Catch-all: serve the single-page app for any non-API route. An unknown /api/*
# path must 404 rather than fall through to the SPA with a misleading 200.
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(STATIC_DIR / "index.html")
