"""Admin: LLM cost tracking + backup/restore."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_settings, require_token
from config import Settings
from models.database import LLMUsage
from services.openrouter import drain_usage

router = APIRouter(prefix="/api", tags=["admin"])

_BACKEND_DIR = Path(__file__).resolve().parents[2]   # backend/
_UPLOADS_DIR = _BACKEND_DIR / "uploads"


# ─────────────────────────────────────────────────────────────────────────────
# Cost tracking
# ─────────────────────────────────────────────────────────────────────────────

async def _flush_usage(db: AsyncSession) -> None:
    for rec in drain_usage():
        db.add(LLMUsage(
            id=str(uuid.uuid4()),
            model=rec.get("model"),
            prompt_tokens=rec.get("prompt_tokens"),
            completion_tokens=rec.get("completion_tokens"),
            total_tokens=rec.get("total_tokens"),
            cost=rec.get("cost") or 0.0,
            created_at=rec.get("at") or datetime.now(timezone.utc),
        ))
    await db.commit()


@router.get("/usage", dependencies=[Depends(require_token)])
async def get_usage(db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """Flush buffered LLM usage and return today/month aggregates + by-model."""
    await _flush_usage(db)
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def _agg(since):
        r = await db.execute(
            select(func.coalesce(func.sum(LLMUsage.cost), 0.0),
                   func.coalesce(func.sum(LLMUsage.total_tokens), 0),
                   func.count(LLMUsage.id))
            .where(LLMUsage.created_at >= since)
        )
        cost, tokens, calls = r.one()
        return {"cost": round(float(cost or 0), 4), "tokens": int(tokens or 0), "calls": int(calls or 0)}

    by_model_rows = await db.execute(
        select(LLMUsage.model, func.coalesce(func.sum(LLMUsage.cost), 0.0), func.count(LLMUsage.id))
        .where(LLMUsage.created_at >= month_start)
        .group_by(LLMUsage.model).order_by(func.sum(LLMUsage.cost).desc())
    )
    by_model = [
        {"model": m, "cost": round(float(c or 0), 4), "calls": int(n)}
        for (m, c, n) in by_model_rows.all()
    ]
    return {
        "today": await _agg(day_start),
        "month": await _agg(month_start),
        "by_model": by_model,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Backup / restore
# ─────────────────────────────────────────────────────────────────────────────

def _is_sqlite(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def _sqlite_file(database_url: str) -> Path:
    # sqlite+aiosqlite:///./insta.db  →  backend/insta.db
    tail = database_url.split(":///", 1)[-1]
    p = Path(tail)
    return p if p.is_absolute() else (_BACKEND_DIR / p).resolve()


@router.get("/admin/backup", dependencies=[Depends(require_token)])
async def backup(settings: Annotated[Settings, Depends(get_settings)]) -> StreamingResponse:
    """Download a backup ZIP.

    Local (sqlite): the db file + uploads/ tree.
    Cloud (Postgres): a pg_dump .sql + uploads/ if present.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if _is_sqlite(settings.database_url):
            dbfile = _sqlite_file(settings.database_url)
            if dbfile.exists():
                zf.write(dbfile, "insta.db")
        else:
            # Postgres dump via pg_dump (needs postgresql-client in the image).
            dump_url = settings.database_url.replace("+asyncpg", "").replace(
                "postgres://", "postgresql://")
            try:
                res = subprocess.run(
                    ["pg_dump", "--no-owner", "--no-privileges", dump_url],
                    capture_output=True, text=True, timeout=120,
                )
                if res.returncode != 0:
                    raise HTTPException(status_code=500, detail=f"pg_dump failed: {res.stderr[:300]}")
                zf.writestr("dump.sql", res.stdout)
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail="pg_dump not installed in this environment")

        # uploads/ (slides, raw, reels) — best-effort, may be large.
        if _UPLOADS_DIR.exists():
            for path in _UPLOADS_DIR.rglob("*"):
                if path.is_file():
                    zf.write(path, str(path.relative_to(_BACKEND_DIR)))

    buf.seek(0)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="insta_backup_{ts}.zip"'},
    )


@router.post("/admin/restore", dependencies=[Depends(require_token)])
async def restore(
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(...),
) -> dict:
    """Restore from a backup ZIP. Local: swaps insta.db (old kept as .bak) and
    uploads/. Cloud: replays dump.sql via psql. Requires an app restart to pick
    up a swapped sqlite file."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Not a valid ZIP")

    names = set(zf.namelist())

    if _is_sqlite(settings.database_url):
        if "insta.db" not in names:
            raise HTTPException(status_code=400, detail="Backup has no insta.db")
        # Write the new db beside the live one; swap requires a restart because
        # the engine holds the current file open (esp. on Windows).
        target = _sqlite_file(settings.database_url)
        restored = target.with_suffix(".restored.db")
        restored.write_bytes(zf.read("insta.db"))
        _extract_uploads(zf)
        return {
            "ok": True,
            "restart_required": True,
            "detail": f"Restored DB written to {restored.name}. "
                      f"Stop the app, replace {target.name} with it, and relaunch.",
        }
    else:
        if "dump.sql" not in names:
            raise HTTPException(status_code=400, detail="Backup has no dump.sql")
        dump_url = settings.database_url.replace("+asyncpg", "").replace(
            "postgres://", "postgresql://")
        sql = zf.read("dump.sql").decode("utf-8", "ignore")
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as tmp:
            tmp.write(sql)
            tmp_path = tmp.name
        try:
            res = subprocess.run(["psql", dump_url, "-f", tmp_path],
                                 capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                raise HTTPException(status_code=500, detail=f"psql restore failed: {res.stderr[:300]}")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="psql not installed in this environment")
        finally:
            os.unlink(tmp_path)
        _extract_uploads(zf)
        return {"ok": True, "restart_required": False, "detail": "Database restored."}


def _extract_uploads(zf: zipfile.ZipFile) -> None:
    for name in zf.namelist():
        if name.startswith("uploads/") and not name.endswith("/"):
            dest = (_BACKEND_DIR / name).resolve()
            # guard against zip-slip
            if _BACKEND_DIR.resolve() not in dest.parents and dest != _BACKEND_DIR:
                if not str(dest).startswith(str((_BACKEND_DIR / "uploads").resolve())):
                    continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
