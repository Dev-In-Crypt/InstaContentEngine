"""Public, no-auth demo for the Business product (doc §11 + §14).

Anyone pastes a public link → we detect the source type, pull the last ~90 days,
keep the newsworthy events (explainable rules), and stream a ready-to-edit draft
per event. This is marketing AND the first real-data run of the fetchers + selector,
so the hypothesis (good selection, grounded drafts) is tested before building the
full Business app.

Guardrails: no auth, but a HARD per-IP rate limit; runs on the app's OWN OpenRouter
key (anonymous visitors have none) so spend is bounded by the limit; text-only
drafts (no image cost); nothing is written to the database — the response is
ephemeral. Framed as "draft starters from your public data", never "a post from you".
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from api.deps import get_demo_text_provider, get_settings
from api.ratelimit import limiter
from config import Settings
from models.schemas import Platform
from services.event_selector import score_item
from services.lead_builder import build_lead
from services.sources import SourceFetchError, detect_source_type, get_source_fetcher

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])

_LOOKBACK_DAYS = 90
_MAX_LEADS = 5           # cap LLM calls per run — anonymous traffic on the app's key


class DemoRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        s = (v or "").strip()
        if not (s.startswith("http://") or s.startswith("https://")):
            raise ValueError("Enter a public http(s) URL")
        if len(s) > 500:
            raise ValueError("URL is too long")
        return s


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/from-url")
@limiter.limit("3/hour;10/day")
async def demo_from_url(
    request: Request,
    body: DemoRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    text_provider: Annotated[object, Depends(get_demo_text_provider)],
) -> StreamingResponse:
    # No app key configured → the demo can't generate. Say so cleanly, don't 500.
    if text_provider is None:
        raise HTTPException(status_code=503, detail="Demo is temporarily unavailable.")

    url = body.url
    text_model = settings.demo_text_model or settings.default_text_model
    ssl_verify = settings.ssl_verify

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def run() -> None:
            try:
                await queue.put({"type": "progress", "message": "Reading the source…"})
                kind = detect_source_type(url)
                fetcher = get_source_fetcher(kind, ssl_verify=ssl_verify)
                since = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
                items = await fetcher.fetch(url, since=since)

                # Keep the newsworthy events, de-duplicating against what we've seen.
                seen_titles: list[str] = []
                worthy: list[tuple] = []
                for it in items:
                    strength, reason = score_item(it, seen_titles)
                    seen_titles.append(it.title)
                    if strength == "worthy":
                        worthy.append((it, reason))
                    if len(worthy) >= _MAX_LEADS:
                        break

                if not worthy:
                    await queue.put({"type": "empty",
                                     "message": "No newsworthy updates found in the last 90 days."})
                    return

                await queue.put({"type": "progress",
                                 "message": f"Drafting {len(worthy)} post(s)…"})
                for it, reason in worthy:
                    lead = await build_lead(text_provider, it,
                                            text_model=text_model, platform=Platform.INSTAGRAM)
                    lead["strength"] = "worthy"
                    lead["reason"] = reason
                    await queue.put({"type": "lead", "lead": lead})
                await queue.put({"type": "complete"})
            except SourceFetchError as e:
                log.warning("Demo fetch failed for %s: %s", url, e)
                await queue.put({"type": "error",
                                 "message": "Couldn't read that source. Try a public page, "
                                            "RSS feed, or GitHub repository link."})
            except Exception:
                log.exception("Demo generation failed for %s", url)
                await queue.put({"type": "error",
                                 "message": "Something went wrong. Please try again."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse(event)
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")
