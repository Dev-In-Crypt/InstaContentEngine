import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.database import Base, TrendingMedia
from services.hashtag_intel import HashtagIntel, _badge


@pytest.fixture
async def db(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SM = async_sessionmaker(eng, expire_on_commit=False)
    async with SM() as session:
        # #running appears in 3 media (high freq), #ultratrail in 1 (niche)
        for i, tags, score in [
            (1, ["#running", "#fitness"], 1000),
            (2, ["#running", "#health"], 500),
            (3, ["#running", "#ultratrail"], 200),
        ]:
            session.add(TrendingMedia(
                id=str(uuid.uuid4()), source_handle="x", ig_media_id=f"m{i}",
                media_type="reel", hashtags=tags, engagement_score=score,
                fetched_at=datetime.now(timezone.utc),
            ))
        await session.commit()
        yield session
    await eng.dispose()


@pytest.mark.asyncio
async def test_rank_heuristic_only(db):
    intel = HashtagIntel()   # no IG token → heuristic only
    ranked = await intel.rank(db, ["#running", "#ultratrail", "#brandnew"])
    by = {r["tag"]: r for r in ranked}
    assert by["#running"]["frequency"] == 3
    assert by["#ultratrail"]["frequency"] == 1
    assert by["#brandnew"]["frequency"] == 0
    assert by["#brandnew"]["badge"] == "niche"
    assert all(r["source"] == "heuristic" for r in ranked)


@pytest.mark.asyncio
async def test_rank_empty(db):
    assert await HashtagIntel().rank(db, []) == []


def test_badge_thresholds():
    assert _badge(10, 100, 10) == "saturated"     # rel 1.0
    assert _badge(2, 800, 10) == "hot"            # rel 0.2 + high eng
    assert _badge(0, 0, 10) == "niche"
    assert _badge(3, 100, 10) == "good"


# ── B2: no blind swallow, negative caching, single commit ───────────────────

@pytest.mark.asyncio
async def test_ig_lookup_narrow_except_lets_bugs_propagate():
    """A network/HTTP error → None (legit 'couldn't reach IG'). A parsing bug
    (anything else) must propagate, not be masked as 'no data'."""
    import httpx
    intel = HashtagIntel("tok", "uid")

    class NetFail:
        async def get(self, *a, **k):
            raise httpx.ConnectError("network down")

    class BugClient:
        async def get(self, *a, **k):
            raise KeyError("unexpected shape")

    assert await intel._ig_lookup(NetFail(), "#x") is None
    with pytest.raises(KeyError):
        await intel._ig_lookup(BugClient(), "#x")


@pytest.mark.asyncio
async def test_failed_lookup_is_negatively_cached(db):
    """A failed IG lookup must be cached so repeated rank() calls don't re-hit the
    30-tags/7-days quota on every request."""
    from unittest.mock import AsyncMock, patch
    intel = HashtagIntel("tok", "uid")
    with patch.object(intel, "_ig_lookup", AsyncMock(return_value=None)) as lookup:
        await intel.rank(db, ["#foo"])
        await intel.rank(db, ["#foo"])
    assert lookup.await_count == 1   # second served from the negative cache


@pytest.mark.asyncio
async def test_enrich_commits_at_most_once(db):
    """_set_cache used to commit per tag, committing whatever else was pending in
    the caller's request session mid-loop."""
    from unittest.mock import AsyncMock, patch
    intel = HashtagIntel("tok", "uid")
    real_commit = db.commit
    spy = AsyncMock(side_effect=real_commit)
    with patch.object(db, "commit", spy), \
         patch.object(intel, "_ig_lookup", AsyncMock(return_value={"media_count": None, "avg_engagement": 5.0})):
        await intel._ig_enrich(db, ["#a", "#b", "#c"])
    assert spy.await_count <= 1
