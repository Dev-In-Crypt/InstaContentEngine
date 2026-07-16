"""The scheduled-publish job must be a coroutine run on the app's event loop.

It used to be a sync function that called asyncio.run() from an APScheduler
worker thread, creating a fresh event loop per job while reusing the app's async
DB engine — which is bound to the app loop. That produced intermittent
"attached to a different loop" / "Event loop is closed" failures on SQLite and
broke outright on asyncpg. As a coroutine, AsyncIOScheduler runs it on its own
(the app's) loop.
"""
import inspect
from unittest.mock import AsyncMock

import services.scheduler as scheduler


def test_run_publish_job_is_a_coroutine():
    # If this reverts to a sync def, AsyncIOScheduler runs it in a worker thread
    # with no loop and the asyncio.run() bridge comes back.
    assert inspect.iscoroutinefunction(scheduler._run_publish_job)


async def test_run_publish_job_awaits_publish_now(monkeypatch):
    called = {}

    async def fake_publish_now(sessionmaker, post_id):
        called["post_id"] = post_id
        return "media-123"

    monkeypatch.setattr("services.publisher_flow.publish_now", fake_publish_now)
    monkeypatch.setattr(scheduler, "_sessionmaker", object())

    await scheduler._run_publish_job("post-abc")

    assert called["post_id"] == "post-abc"


async def test_run_publish_job_swallows_failure(monkeypatch):
    """publish_now already marks the post failed; the job must not raise (that
    would escape into APScheduler and leave the outcome invisible)."""
    failing = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("services.publisher_flow.publish_now", failing)
    monkeypatch.setattr(scheduler, "_sessionmaker", object())

    await scheduler._run_publish_job("post-abc")   # must not raise
