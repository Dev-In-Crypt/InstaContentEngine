"""Source analytics (Phase 8): per-source pipeline funnel, workspace-scoped.

Ranks sources by engagement → published → approved → worthy → leads. Engagement is
real when a Business post has been published + its Instagram insights refreshed: the
LATEST PostInsight snapshot per post is summed (reach + likes/comments/saves/shares)
back to the source via post → lead → source. The workspace_id filter is the mutation
target: drop it and another workspace's leads/posts/insights leak into this one's.
"""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db, get_settings
from config import Settings
from main import app
from models.database import Base, Lead, Post, PostInsight, Source, Workspace


@pytest.fixture
def ctx(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sa.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(app_mode="cloud")
    app.state.sessionmaker = SM
    yield TestClient(app), SM
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


def _register(c, email, account_type="business"):
    r = c.post("/api/auth/register",
               json={"email": email, "password": "password123", "account_type": account_type})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(c, h):
    return c.get("/api/auth/me", headers=h).json()["id"]


def _seed_workspace(SM, uid):
    """Pre-create the workspace (the endpoint's get_or_create finds it) + return its id."""
    ws_id = str(uuid.uuid4())

    async def _s():
        async with SM() as db:
            db.add(Workspace(id=ws_id, owner_user_id=uid, name="W"))
            await db.commit()
    asyncio.run(_s())
    return ws_id


def _seed_source(SM, ws_id, url):
    sid = str(uuid.uuid4())

    async def _s():
        async with SM() as db:
            db.add(Source(id=sid, workspace_id=ws_id, url=url, kind="github_releases",
                          status="ok", active=True))
            await db.commit()
    asyncio.run(_s())
    return sid


def _seed_lead(SM, ws_id, source_id, strength="worthy", status="new"):
    lid = str(uuid.uuid4())

    async def _s():
        async with SM() as db:
            db.add(Lead(id=lid, workspace_id=ws_id, source_id=source_id,
                        external_id=lid[:8], what_happened="x", strength=strength,
                        status=status, raw={}))
            await db.commit()
    asyncio.run(_s())
    return lid


def _seed_post(SM, uid, ws_id, lead_id, status, source_kind="lead"):
    pid = str(uuid.uuid4())

    async def _s():
        async with SM() as db:
            db.add(Post(id=pid, user_id=uid, workspace_id=ws_id,
                        lead_id=lead_id, source_kind=source_kind, topic="t",
                        format="single", status=status))
            await db.commit()
    asyncio.run(_s())
    return pid


def _seed_insight(SM, post_id, *, snapshot_at, reach, likes, comments, saved, shares):
    async def _s():
        async with SM() as db:
            db.add(PostInsight(id=str(uuid.uuid4()), post_id=post_id, snapshot_at=snapshot_at,
                               reach=reach, likes=likes, comments=comments,
                               saved=saved, shares=shares))
            await db.commit()
    asyncio.run(_s())


def _seed_full_workspace(SM, uid):
    """One workspace, two sources. s1 has a rich funnel + a published post; s2 has
    one worthy lead and no posts. Plus a digest post (no single source)."""
    ws = _seed_workspace(SM, uid)
    s1 = _seed_source(SM, ws, "https://github.com/o/one")
    s2 = _seed_source(SM, ws, "https://github.com/o/two")
    la = _seed_lead(SM, ws, s1, "worthy", "drafted")
    _seed_lead(SM, ws, s1, "worthy", "new")
    _seed_lead(SM, ws, s1, "weak", "new")
    _seed_lead(SM, ws, s1, "worthy", "dismissed")   # 4 leads, 3 worthy, 1 dismissed
    _seed_lead(SM, ws, s2, "worthy", "new")
    # s1 posts: 2 draft, 1 approved, 1 published  (all traced via lead la)
    _seed_post(SM, uid, ws, la, "draft")
    _seed_post(SM, uid, ws, la, "draft")
    _seed_post(SM, uid, ws, la, "approved")
    _seed_post(SM, uid, ws, la, "published")
    # a digest post spans many leads → lead_id NULL, not attributable to a source
    _seed_post(SM, uid, ws, None, "draft", source_kind="digest")
    return ws, s1, s2


def test_funnel_counts_and_ranking(ctx):
    c, SM = ctx
    h = _register(c, "sa@ex.com")
    _seed_full_workspace(SM, _uid(c, h))

    body = c.get("/api/business/source-analytics", headers=h).json()
    assert body["digests"] == 1
    assert body["totals"] == {"sources": 2, "leads": 5, "worthy": 4,
                              "drafts": 4, "approved": 2, "published": 1,
                              "reach": 0, "engagement": 0, "measured_posts": 0}

    rows = body["sources"]
    assert len(rows) == 2
    # best first: s1 has a published post, s2 has none
    top = rows[0]
    assert top["url"].endswith("/one")
    assert top["leads_total"] == 4 and top["worthy"] == 3 and top["weak"] == 1
    assert top["dismissed"] == 1
    assert top["drafts"] == 4 and top["approved"] == 2 and top["published"] == 1
    assert top["worthy_rate"] == 0.75 and top["approve_rate"] == 0.5

    second = rows[1]
    assert second["url"].endswith("/two")
    assert second["leads_total"] == 1 and second["drafts"] == 0 and second["published"] == 0


def test_workspace_isolation(ctx):
    c, SM = ctx
    ha = _register(c, "a@ex.com")
    hb = _register(c, "b@ex.com")
    _seed_full_workspace(SM, _uid(c, ha))
    _seed_full_workspace(SM, _uid(c, hb))   # B's identical workspace must not leak

    a = c.get("/api/business/source-analytics", headers=ha).json()
    # mutation guard: without the workspace_id filter, totals would double
    assert a["totals"]["sources"] == 2 and a["totals"]["leads"] == 5
    assert a["digests"] == 1


def test_empty_workspace(ctx):
    c, SM = ctx
    h = _register(c, "empty@ex.com")
    body = c.get("/api/business/source-analytics", headers=h).json()
    assert body["sources"] == [] and body["digests"] == 0
    assert body["totals"]["sources"] == 0


def test_engagement_sums_latest_insight_per_source(ctx):
    """Reach/engagement come from the LATEST snapshot of each published post, summed
    back to the source. Mutation guards: an older snapshot must not count (latest
    wins), and reach must not double when a post has two snapshots."""
    import datetime as dt
    c, SM = ctx
    h = _register(c, "eng@ex.com")
    uid = _uid(c, h)
    ws = _seed_workspace(SM, uid)
    s1 = _seed_source(SM, ws, "https://github.com/o/one")
    s2 = _seed_source(SM, ws, "https://github.com/o/two")
    l1 = _seed_lead(SM, ws, s1, "worthy", "published")
    l2 = _seed_lead(SM, ws, s2, "worthy", "published")
    p1 = _seed_post(SM, uid, ws, l1, "published")
    p2 = _seed_post(SM, uid, ws, l2, "published")

    t0 = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    t1 = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
    # p1: two snapshots — only the newest (t1) must count
    _seed_insight(SM, p1, snapshot_at=t0, reach=50, likes=1, comments=0, saved=0, shares=0)
    _seed_insight(SM, p1, snapshot_at=t1, reach=1000, likes=10, comments=5, saved=3, shares=2)
    _seed_insight(SM, p2, snapshot_at=t1, reach=200, likes=4, comments=1, saved=0, shares=0)

    body = c.get("/api/business/source-analytics", headers=h).json()
    by_url = {r["url"]: r for r in body["sources"]}
    one = by_url["https://github.com/o/one"]
    two = by_url["https://github.com/o/two"]
    # latest snapshot only: 1000 reach, 10+5+3+2 = 20 engagement
    assert one["reach"] == 1000 and one["engagement"] == 20 and one["measured_posts"] == 1
    assert two["reach"] == 200 and two["engagement"] == 5 and two["measured_posts"] == 1
    assert body["totals"]["reach"] == 1200
    assert body["totals"]["engagement"] == 25
    assert body["totals"]["measured_posts"] == 2
    # ranking: highest engagement first
    assert body["sources"][0]["url"].endswith("/one")


def test_creator_gets_403(ctx):
    c, _ = ctx
    h = _register(c, "creator@ex.com", account_type="creator")
    assert c.get("/api/business/source-analytics", headers=h).status_code == 403
