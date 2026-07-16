"""APScheduler wrapper for scheduled Instagram publishing.

In cloud mode (24/7 backend) this makes scheduled posts publish even when the
user's PC is off. In local mode it only fires while the desktop app is open.

Jobs are persisted in the same database (SQLAlchemyJobStore) so they survive a
process restart — on startup APScheduler re-loads any pending jobs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

log = logging.getLogger(__name__)

# Module-level singleton; set by init_scheduler() during app startup.
_scheduler: Optional[AsyncIOScheduler] = None
_sessionmaker = None


def _sync_jobstore_url(database_url: str) -> str:
    """APScheduler's SQLAlchemyJobStore is synchronous — strip async drivers."""
    return (
        database_url
        .replace("+aiosqlite", "")
        .replace("+asyncpg", "")
        .replace("postgres://", "postgresql://")  # normalize Render/Heroku style
    )


def init_scheduler(database_url: str, sessionmaker) -> AsyncIOScheduler:
    global _scheduler, _sessionmaker
    _sessionmaker = sessionmaker
    jobstore = SQLAlchemyJobStore(url=_sync_jobstore_url(database_url))
    _scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone="UTC",
    )
    _scheduler.start()
    log.info("APScheduler started with %d pending job(s)", len(_scheduler.get_jobs()))
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def schedule_publish(post_id: str, run_at: datetime) -> None:
    """Schedule (or reschedule) a publish job for a post."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized")
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    _scheduler.add_job(
        _run_publish_job,
        trigger="date",
        run_date=run_at,
        args=[post_id],
        id=f"pub_{post_id}",
        replace_existing=True,
        misfire_grace_time=3600,   # if the app was down at the exact time, still fire within 1h
    )


def cancel_publish(post_id: str) -> bool:
    if _scheduler is None:
        return False
    try:
        _scheduler.remove_job(f"pub_{post_id}")
        return True
    except Exception:
        return False


def get_job(post_id: str):
    if _scheduler is None:
        return None
    return _scheduler.get_job(f"pub_{post_id}")


async def _run_publish_job(post_id: str) -> None:
    """The scheduled publish.

    A coroutine on purpose: AsyncIOScheduler runs coroutine jobs on its own event
    loop, which is the app loop (the scheduler is started in the FastAPI
    lifespan). That's the loop _sessionmaker's async engine belongs to. A sync job
    would instead run in a worker thread and have to spin up a throwaway loop,
    using the engine across loops — the source of "Event loop is closed" errors.
    """
    from services.publisher_flow import publish_now

    try:
        media_id = await publish_now(_sessionmaker, post_id)
        log.info("Scheduled publish OK: post=%s media=%s", post_id, media_id)
    except Exception as e:
        # publish_now already marked the post failed with the error; don't let the
        # exception escape into APScheduler, where the outcome would be invisible.
        log.error("Scheduled publish FAILED: post=%s error=%s", post_id, e)
