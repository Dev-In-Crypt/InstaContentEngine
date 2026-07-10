import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import get_settings
from models.database import Base, BrandConfig as BrandConfigModel
from models.schemas import NICHE_BOX_PALETTE
from api.routes import posts, models, stock, trends

STATIC_DIR = Path(__file__).parent / "static"
UPLOADS_DIR = Path(__file__).parent / "uploads"

settings = get_settings()

# New columns added after the initial schema. create_all does NOT alter existing
# tables, so on an already-created sqlite db these are added idempotently here.
_MIGRATIONS: dict[str, dict[str, str]] = {
    "posts": {
        "seo_keywords": "JSON",
        "platform": "VARCHAR(20) DEFAULT 'instagram'",
        "template_style": "VARCHAR(20) DEFAULT 'branded_card'",
        "trend_idea_id": "VARCHAR(36)",
        "sources": "JSON",
        "published_image_urls": "JSON",
        "schedule_error": "TEXT",
    },
    "slides": {
        "page_number": "INTEGER",
        "attribution": "JSON",
        "render_params": "JSON",
        "raw_image_path": "TEXT",
        "original_overlay_text": "TEXT",
        "original_niche_text": "TEXT",
    },
    "brand_configs": {
        "template_style": "VARCHAR(20) DEFAULT 'branded_card'",
        "niche_box_color": "VARCHAR(7) DEFAULT '#ff751f'",
        "niche_box_palette": "JSON",
        "description_box_alpha": "FLOAT DEFAULT 0.79",
        "show_logo": "BOOLEAN DEFAULT 1",
    },
}


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
                except Exception:
                    pass


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_brand_preset(app.state.sessionmaker)
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


app = FastAPI(
    title="Instagram Content Engine",
    description="AI-powered Instagram post generation and publishing system",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(posts.router)
app.include_router(models.router)
app.include_router(stock.router)
app.include_router(trends.router)

# Serve built frontend assets (images, fonts, etc.) at /static/*
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Catch-all: serve the single-page app for any non-API route
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
