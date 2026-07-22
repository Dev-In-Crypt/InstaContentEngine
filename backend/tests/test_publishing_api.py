"""Integration tests for scheduling / insights / regenerate-field / grid."""
from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_content_engine, get_db
from main import app
from models.database import Base, Post as PostModel, Slide as SlideModel
from services.content_engine import ContentEngine

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads" / "posts"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'pub.db'}"


@pytest.fixture
def seeded(db_url):
    post_id = str(uuid.uuid4())

    async def _setup():
        eng = create_async_engine(db_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SM = async_sessionmaker(eng, expire_on_commit=False)
        async with SM() as s:
            s.add(PostModel(
                id=post_id, topic="Running tips", format="single", status="preview",
                caption="Run every day.", hashtags=["#run"], seo_keywords=["running"],
                cta="Follow!", hook="Run daily.", platform="instagram",
                template_style="branded_card",
            ))
            path = UPLOADS_DIR / post_id / "slide_1.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_jpeg())
            s.add(SlideModel(post_id=post_id, slide_number=1, image_source="stock",
                             image_path=str(path)))
            await s.commit()
        await eng.dispose()

    asyncio.run(_setup())
    yield post_id
    p = UPLOADS_DIR / post_id
    if (p / "slide_1.jpg").exists():
        (p / "slide_1.jpg").unlink()
    if p.exists():
        try:
            p.rmdir()
        except OSError:
            pass


@pytest.fixture
def client(db_url):
    eng = create_async_engine(db_url)
    asyncio.run(_ensure(eng))
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    fake_engine = AsyncMock(spec=ContentEngine)
    fake_engine.caption_gen = AsyncMock()
    fake_engine.caption_gen.regenerate_field.return_value = ["Variant 1.", "Variant 2."]

    from api.deps import get_settings
    from config import Settings

    def override_settings():
        return Settings(database_url=db_url)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_content_engine] = lambda: fake_engine
    app.dependency_overrides[get_settings] = override_settings
    app.state.sessionmaker = SM   # for publish_now / scheduler paths

    tc = TestClient(app)
    tc.fake_engine = fake_engine
    yield tc

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_content_engine, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


async def _ensure(eng):
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── regenerate-field ────────────────────────────────────────────────────────

def test_regenerate_field_returns_variants(client, seeded):
    res = client.post(f"/api/posts/{seeded}/regenerate-field", json={"field": "hook", "count": 2})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["field"] == "hook"
    assert body["variants"] == ["Variant 1.", "Variant 2."]


def test_regenerate_field_bad_field_422(client, seeded):
    res = client.post(f"/api/posts/{seeded}/regenerate-field", json={"field": "banana"})
    assert res.status_code == 422   # pattern validation


def test_regenerate_field_404(client):
    res = client.post(f"/api/posts/{uuid.uuid4()}/regenerate-field", json={"field": "hook"})
    assert res.status_code == 404


# ── insights ────────────────────────────────────────────────────────────────

def test_insights_refresh_409_when_not_published(client, seeded):
    res = client.post(f"/api/posts/{seeded}/insights/refresh")
    assert res.status_code == 409
    assert "not published" in res.json()["detail"].lower()


def test_insights_list_empty(client, seeded):
    res = client.get(f"/api/posts/{seeded}/insights")
    assert res.status_code == 200
    assert res.json() == []


# ── schedule ────────────────────────────────────────────────────────────────

def test_schedule_rejects_too_soon(client, seeded):
    from datetime import datetime, timezone, timedelta
    soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    res = client.post(f"/api/posts/{seeded}/schedule", json={"publish_at": soon})
    assert res.status_code == 400


def test_schedule_and_unschedule(client, seeded, monkeypatch):
    # Avoid real APScheduler jobstore — stub the scheduler helpers.
    import services.scheduler as sched
    monkeypatch.setattr(sched, "schedule_publish", lambda pid, when: None)
    monkeypatch.setattr(sched, "cancel_publish", lambda pid: True)

    from datetime import datetime, timezone, timedelta
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    res = client.post(f"/api/posts/{seeded}/schedule", json={"publish_at": when})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "scheduled"
    assert body["scheduled_at"] is not None

    res = client.delete(f"/api/posts/{seeded}/schedule")
    assert res.status_code == 200
    assert res.json()["scheduled_at"] is None


# ── grid / list ─────────────────────────────────────────────────────────────

def test_list_posts_has_thumb_and_status(client, seeded):
    res = client.get("/api/posts")
    assert res.status_code == 200
    posts = res.json()
    row = next(p for p in posts if p["id"] == seeded)
    assert row["thumb_url"].endswith("/slides/1/image")
    assert row["status"] == "preview"


# ── pillars ─────────────────────────────────────────────────────────────────

def test_pillars_mix(client, seeded):
    res = client.get("/api/posts/pillars/mix")
    assert res.status_code == 200
    data = res.json()
    assert len(data["pillars"]) == 5
    assert "suggestion" in data
    assert data["total"] >= 1


# ── reel render ─────────────────────────────────────────────────────────────

def test_make_reel_and_fetch_video(client, seeded):
    res = client.post(f"/api/posts/{seeded}/reel")
    assert res.status_code == 200, res.text
    assert "?t=" in res.json()["video_url"]
    assert res.json()["size_bytes"] > 0
    # fetch the rendered mp4
    v = client.get(f"/api/posts/{seeded}/reel/video")
    assert v.status_code == 200
    assert v.headers["content-type"] == "video/mp4"


def test_reel_video_404_when_not_rendered(client, seeded):
    res = client.get(f"/api/posts/{seeded}/reel/video")
    assert res.status_code == 404


def test_publish_reel_needs_public_url(client, seeded):
    # render first
    client.post(f"/api/posts/{seeded}/reel")
    res = client.post(f"/api/posts/{seeded}/publish-reel")
    assert res.status_code == 409          # a failure is a 4xx now, not 200
    assert "public" in res.json()["detail"].lower()


# ── voiceover reel (R1) ─────────────────────────────────────────────────────

def _override_voiceover_deps(text_provider, elevenlabs_key, pexels_key=""):
    from api.deps import get_effective_settings, get_text_provider
    from config import Settings
    app.dependency_overrides[get_effective_settings] = (
        lambda: Settings(elevenlabs_api_key=elevenlabs_key,
                         pexels_api_key=pexels_key))
    app.dependency_overrides[get_text_provider] = lambda: text_provider
    return (get_effective_settings, get_text_provider)


def _clear(keys):
    for k in keys:
        app.dependency_overrides.pop(k, None)


def test_voiceover_needs_text_model(client, seeded):
    keys = _override_voiceover_deps(text_provider=None, elevenlabs_key="k")
    try:
        res = client.post(f"/api/posts/{seeded}/reel", json={"voiceover": True})
        assert res.status_code == 400
        assert "text model" in res.json()["detail"].lower()
    finally:
        _clear(keys)


def test_voiceover_needs_elevenlabs_key(client, seeded):
    keys = _override_voiceover_deps(text_provider=object(), elevenlabs_key="")
    try:
        res = client.post(f"/api/posts/{seeded}/reel", json={"voiceover": True})
        assert res.status_code == 400
        assert "elevenlabs" in res.json()["detail"].lower()
    finally:
        _clear(keys)


def test_voiceover_full_path(client, seeded, monkeypatch, tmp_path):
    """End-to-end with a fake LLM + fake TTS bytes; the wav/concat/render/ass/mux
    stages run for REAL (bundled ffmpeg). Mutation guard: the response reports
    voiceover=true and the mp4 on disk has an audio stream."""
    import json as _json
    import subprocess

    from services import tts as tts_mod

    class FakeText:
        async def generate_text(self, **kw):
            return (_json.dumps(["One short narration line."]), [])

    tone = tmp_path / "tone.mp3"
    subprocess.run([tts_mod.ffmpeg_exe(), "-hide_banner", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(tone)],
                   capture_output=True, check=True)
    tone_bytes = tone.read_bytes()

    async def fake_synth(self, text, *, voice_id, model_id="eleven_multilingual_v2"):
        assert voice_id  # a voice id is always resolved (default Rachel)
        return tone_bytes
    monkeypatch.setattr(tts_mod.ElevenLabsTTS, "synthesize", fake_synth)

    keys = _override_voiceover_deps(text_provider=FakeText(), elevenlabs_key="k")
    try:
        res = client.post(f"/api/posts/{seeded}/reel", json={"voiceover": True})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get("voiceover") is True
        assert body["duration_sec"] > 0
        # the served reel really carries an audio track
        from api.routes.posts import _reel_path
        info = subprocess.run([tts_mod.ffmpeg_exe(), "-hide_banner", "-i",
                               str(_reel_path(seeded))],
                              capture_output=True, text=True, errors="replace").stderr
        assert "Audio:" in info
    finally:
        _clear(keys)


# ── b-roll reel (R2) ────────────────────────────────────────────────────────

def _fake_tts(monkeypatch, tmp_path):
    import subprocess

    from services import tts as tts_mod
    tone = tmp_path / "tone.mp3"
    subprocess.run([tts_mod.ffmpeg_exe(), "-hide_banner", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(tone)],
                   capture_output=True, check=True)
    tone_bytes = tone.read_bytes()

    async def fake_synth(self, text, *, voice_id, model_id="eleven_multilingual_v2"):
        return tone_bytes
    monkeypatch.setattr(tts_mod.ElevenLabsTTS, "synthesize", fake_synth)


class _FakeTextOneLine:
    async def generate_text(self, **kw):
        import json as _json
        return (_json.dumps([{"text": "One short line.", "query": "city shot"}]), [])


def test_broll_needs_voiceover(client, seeded):
    res = client.post(f"/api/posts/{seeded}/reel",
                      json={"voiceover": False, "visuals": "broll"})
    assert res.status_code == 400
    assert "voiceover" in res.json()["detail"].lower()


def test_broll_needs_pexels_key(client, seeded):
    keys = _override_voiceover_deps(text_provider=_FakeTextOneLine(),
                                    elevenlabs_key="k", pexels_key="")
    try:
        res = client.post(f"/api/posts/{seeded}/reel",
                          json={"voiceover": True, "visuals": "broll"})
        assert res.status_code == 400
        assert "pexels" in res.json()["detail"].lower()
    finally:
        _clear(keys)


def test_broll_full_path(client, seeded, monkeypatch, tmp_path):
    """Search+download mocked (a real tiny testsrc clip); judge fails open (no
    vision_json on the fake provider); normalize/concat/ass/mux run for REAL.
    Mutation guard: broll_clips reported and the reel carries an audio track."""
    import subprocess

    from services import broll as broll_mod
    from services import tts as tts_mod

    _fake_tts(monkeypatch, tmp_path)
    src = tmp_path / "stock.mp4"
    subprocess.run([tts_mod.ffmpeg_exe(), "-hide_banner", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=1:size=320x180:rate=30",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset",
                    "ultrafast", str(src)], capture_output=True, check=True)

    cand = broll_mod.Candidate(video_id=42, url="https://dl/42.mp4", duration=10,
                               thumbnail_url="", picture_urls=[])

    async def fake_candidates(self, query, target_duration, max_results=8):
        return [cand]

    async def fake_download(self, url, dest):
        dest.write_bytes(src.read_bytes())

    monkeypatch.setattr(broll_mod.PexelsVideoSearch, "candidates", fake_candidates)
    monkeypatch.setattr(broll_mod.PexelsVideoSearch, "download", fake_download)

    keys = _override_voiceover_deps(text_provider=_FakeTextOneLine(),
                                    elevenlabs_key="k", pexels_key="px")
    try:
        res = client.post(f"/api/posts/{seeded}/reel",
                          json={"voiceover": True, "visuals": "broll"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get("voiceover") is True
        assert body.get("broll_clips") == 1
        from api.routes.posts import _reel_path
        info = subprocess.run([tts_mod.ffmpeg_exe(), "-hide_banner", "-i",
                               str(_reel_path(seeded))],
                              capture_output=True, text=True, errors="replace").stderr
        assert "Audio:" in info and "Video:" in info
    finally:
        _clear(keys)


def test_broll_segment_falls_back_to_slide(client, seeded, monkeypatch, tmp_path):
    """Empty search results must NOT fail the reel — the segment renders from
    its slide instead (mutation guard: drop the fallback → 502)."""
    from services import broll as broll_mod

    _fake_tts(monkeypatch, tmp_path)

    async def no_candidates(self, query, target_duration, max_results=8):
        return []
    monkeypatch.setattr(broll_mod.PexelsVideoSearch, "candidates", no_candidates)

    keys = _override_voiceover_deps(text_provider=_FakeTextOneLine(),
                                    elevenlabs_key="k", pexels_key="px")
    try:
        res = client.post(f"/api/posts/{seeded}/reel",
                          json={"voiceover": True, "visuals": "broll"})
        assert res.status_code == 200, res.text
        assert "broll_clips" not in res.json()      # nothing attributed
    finally:
        _clear(keys)


# ── usage + backup ──────────────────────────────────────────────────────────

def test_usage_aggregate(client, seeded):
    from services.openrouter import record_usage, drain_usage
    drain_usage()
    record_usage("anthropic/claude-sonnet-4", {"prompt_tokens": 10, "completion_tokens": 5,
                                                "total_tokens": 15, "cost": 0.01})
    res = client.get("/api/usage")
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["today"]["cost"] >= 0.01
    assert d["today"]["calls"] >= 1
    assert any(m["model"] == "anthropic/claude-sonnet-4" for m in d["by_model"])


def test_backup_returns_zip(client, seeded):
    import io
    import zipfile
    res = client.get("/api/admin/backup")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    # sqlite backup includes the db file; uploads present because seeded wrote a slide
    assert "insta.db" in names
    assert any(n.startswith("uploads/") for n in names)


def test_restore_rejects_non_zip(client, seeded):
    res = client.post("/api/admin/restore", files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert res.status_code == 400


# ── rate limiting ────────────────────────────────────────────────────────────

def test_regenerate_field_rate_limited_returns_429(client, seeded):
    """/regenerate-field is capped at 15/min. The autouse fixture disables the
    limiter, so re-enable it locally and confirm a burst trips 429 (the mocked
    engine returns 200 on the happy path, so the hits are actually recorded)."""
    from api.ratelimit import limiter
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.post(f"/api/posts/{seeded}/regenerate-field",
                             json={"field": "hook", "count": 2}).status_code
                 for _ in range(18)]
    finally:
        limiter.enabled = False
    assert 429 in codes                 # limit tripped within the burst
    assert codes.count(200) <= 15       # at most the allowed 15 succeeded
