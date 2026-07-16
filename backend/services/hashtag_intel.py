"""Hashtag intelligence — ranks hashtags by how well they work in the niche.

Two signals, merged:
  1. Heuristic (always available): frequency of the tag across the competitor
     media we already collected (`trending_media.hashtags`) + the average
     engagement of the posts that used it.
  2. IG Hashtag Search API (when an IG token is configured): the average
     engagement of the tag's current top media as a popularity proxy.

Instagram's hashtag API is rate-limited to 30 unique tags / 7 days per user,
so results are cached in the `hashtag_stats` table.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import HashtagStat, TrendingMedia

log = logging.getLogger(__name__)

_CACHE_TTL = timedelta(days=3)


def _norm(tag: str) -> str:
    return "#" + tag.lstrip("#").strip().lower()


def _badge(frequency: int, avg_engagement: float, max_freq: int) -> str:
    """A human label for the tag based on how common + how engaging it is."""
    rel = (frequency / max_freq) if max_freq else 0
    if rel >= 0.6:
        return "saturated"           # everyone uses it → hard to rank
    if avg_engagement >= 500 and rel >= 0.15:
        return "hot"                 # common enough + strong engagement
    if frequency == 0:
        return "niche"               # not seen in competitor set
    return "good"


async def _heuristic(db: AsyncSession, tags: list[str]) -> dict:
    """Frequency + avg engagement of each tag across trending_media."""
    result = await db.execute(select(TrendingMedia.hashtags, TrendingMedia.engagement_score,
                                     TrendingMedia.fetched_at))
    rows = result.all()
    freq: Counter = Counter()
    eng_sum: dict[str, float] = {}
    recent_freq: Counter = Counter()
    old_freq: Counter = Counter()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    wanted = {_norm(t) for t in tags}
    for hashtags, score, fetched in rows:
        seen = {_norm(h) for h in (hashtags or [])}
        for t in seen & wanted:
            freq[t] += 1
            eng_sum[t] = eng_sum.get(t, 0.0) + float(score or 0)
            # naive recency split for a trend arrow
            when = fetched
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                (recent_freq if when >= cutoff else old_freq)[t] += 1
    out = {}
    for t in wanted:
        f = freq.get(t, 0)
        avg = (eng_sum.get(t, 0.0) / f) if f else 0.0
        if recent_freq.get(t, 0) > old_freq.get(t, 0):
            trend = "up"
        elif recent_freq.get(t, 0) < old_freq.get(t, 0):
            trend = "down"
        else:
            trend = "flat"
        out[t] = {"frequency": f, "avg_engagement": round(avg, 1), "trend": trend}
    return out


class HashtagIntel:
    """Combines cached stats, heuristic, and (optionally) the IG hashtag API."""

    def __init__(self, ig_access_token: str = "", ig_user_id: str = ""):
        self._token = ig_access_token
        self._ig_user_id = ig_user_id

    async def rank(self, db: AsyncSession, tags: list[str]) -> list[dict]:
        tags = [_norm(t) for t in tags if t.strip()]
        if not tags:
            return []
        heur = await _heuristic(db, tags)
        max_freq = max((h["frequency"] for h in heur.values()), default=0)

        # IG API enrichment (best-effort, cached).
        ig_data: dict = {}
        if self._token and self._ig_user_id:
            ig_data = await self._ig_enrich(db, tags)

        ranked = []
        for t in tags:
            h = heur.get(t, {"frequency": 0, "avg_engagement": 0.0, "trend": "flat"})
            ig = ig_data.get(t, {})
            media_count = ig.get("media_count")
            avg_eng = ig.get("avg_engagement") or h["avg_engagement"]
            source = "both" if ig else "heuristic"
            ranked.append({
                "tag": t,
                "frequency": h["frequency"],
                "avg_engagement": avg_eng,
                "media_count": media_count,
                "trend": h["trend"],
                "badge": _badge(h["frequency"], avg_eng, max_freq),
                "source": source,
            })
        # Sort: hot first, then by engagement, saturated last.
        order = {"hot": 0, "good": 1, "niche": 2, "saturated": 3}
        ranked.sort(key=lambda r: (order.get(r["badge"], 5), -r["avg_engagement"]))
        return ranked

    async def _ig_enrich(self, db: AsyncSession, tags: list[str]) -> dict:
        out: dict = {}
        wrote = False
        async with httpx.AsyncClient(timeout=30.0) as client:
            for t in tags:
                cached = await self._get_cache(db, t)
                if cached is not None:
                    if not cached.get("failed"):
                        out[t] = cached
                    continue   # positive or negative — either way, don't re-hit the API
                data = await self._ig_lookup(client, t)
                if data is not None:
                    out[t] = data
                    await self._stage_cache(db, t, data, ok=True)
                else:
                    # Negative-cache the failure so we don't re-hit the quota next time.
                    await self._stage_cache(db, t, {}, ok=False)
                wrote = True
        # One commit for the whole batch, not one per tag mid-request.
        if wrote:
            await db.commit()
        return out

    async def _get_cache(self, db: AsyncSession, tag: str) -> Optional[dict]:
        row = (await db.execute(select(HashtagStat).where(HashtagStat.tag == tag))).scalar_one_or_none()
        if not row or not row.checked_at:
            return None
        checked = row.checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - checked > _CACHE_TTL:
            return None
        if row.source == "ig_api_fail":
            return {"failed": True}
        return {"media_count": row.media_count, "avg_engagement": row.avg_engagement}

    async def _stage_cache(self, db: AsyncSession, tag: str, data: dict, ok: bool) -> None:
        """Upsert a cache row WITHOUT committing — caller commits once per batch."""
        import uuid
        row = (await db.execute(
            select(HashtagStat).where(HashtagStat.tag == tag)
        )).scalar_one_or_none()
        if row is None:
            row = HashtagStat(id=str(uuid.uuid4()), tag=tag)
            db.add(row)
        row.media_count = data.get("media_count")
        row.avg_engagement = data.get("avg_engagement")
        row.source = "ig_api" if ok else "ig_api_fail"
        row.checked_at = datetime.now(timezone.utc)

    async def _ig_lookup(self, client: httpx.AsyncClient, tag: str) -> Optional[dict]:
        """Resolve tag → hashtag id → top_media avg engagement.

        Returns None only for real IG failures (network, HTTP error, rate limit).
        A parsing bug (unexpected response shape) propagates instead of being
        masked as "no data", so we actually find out about it.
        """
        base = "https://graph.instagram.com/v25.0"
        q = tag.lstrip("#")
        try:
            r = await client.get(f"{base}/ig_hashtag_search",
                                  params={"user_id": self._ig_user_id, "q": q, "access_token": self._token})
            r.raise_for_status()
        except httpx.HTTPError as e:  # network / status / timeout — legitimately "couldn't reach IG"
            log.warning("IG hashtag lookup failed for %s: %s", tag, e)
            return None

        ids = r.json().get("data", [])
        if not ids:
            return None
        hid = ids[0]["id"]
        try:
            r2 = await client.get(f"{base}/{hid}/top_media", params={
                "user_id": self._ig_user_id,
                "fields": "like_count,comments_count",
                "access_token": self._token,
            })
            r2.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("IG top_media failed for %s: %s", tag, e)
            return None

        media = r2.json().get("data", [])
        if not media:
            return {"media_count": None, "avg_engagement": 0.0}
        eng = [int(m.get("like_count", 0)) + int(m.get("comments_count", 0)) for m in media]
        return {"media_count": None, "avg_engagement": round(sum(eng) / len(eng), 1)}
