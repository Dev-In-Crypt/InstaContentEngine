"""B-roll search + judge (Reels R2) — httpx.MockTransport, no network.

The progressive-fallback and portrait-first sorting are the search's precision
levers; the judge must be FAIL-OPEN (a worse clip beats a crashed reel).
"""
import json

import httpx
import pytest

from services.broll import Candidate, PexelsVideoSearch, pick_with_judge


def _video(vid, w, h, duration, n_pics=0, file_heights=(1080,)):
    return {
        "id": vid, "width": w, "height": h, "duration": duration,
        "image": f"https://img/{vid}.jpg",
        "video_files": [{"height": fh, "link": f"https://dl/{vid}_{fh}.mp4"}
                        for fh in file_heights],
        "video_pictures": [{"picture": f"https://pic/{vid}_{i}.jpg"}
                           for i in range(n_pics)],
    }


def _search_with(handler):
    s = PexelsVideoSearch("key")
    return s, httpx.MockTransport(handler)


def _patch_client(monkeypatch, transport):
    orig = httpx.AsyncClient

    def patched(*a, **kw):
        kw.pop("verify", None)
        return orig(transport=transport, timeout=kw.get("timeout"),
                    headers=kw.get("headers"))
    monkeypatch.setattr(httpx, "AsyncClient", patched)


@pytest.mark.asyncio
async def test_progressive_fallback_and_params(monkeypatch):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        # first attempt (size=large) empty; second (medium) has results
        if request.url.params.get("size") == "large":
            return httpx.Response(200, json={"videos": []})
        return httpx.Response(200, json={"videos": [_video(1, 1080, 1920, 12)]})

    search, transport = _search_with(handler)
    _patch_client(monkeypatch, transport)
    out = await search.candidates("city sunrise wide", 5.0)
    assert len(out) == 1 and out[0].video_id == 1
    assert calls[0]["orientation"] == "portrait"
    assert calls[0]["min_duration"] == "5"
    assert calls[0]["size"] == "large" and calls[1]["size"] == "medium"


@pytest.mark.asyncio
async def test_portrait_first_and_longer_first(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"videos": [
            _video(1, 1920, 1080, 30),    # landscape, long
            _video(2, 1080, 1920, 8),     # portrait, short
            _video(3, 1080, 1920, 20),    # portrait, long → must be first
        ]})
    search, transport = _search_with(handler)
    _patch_client(monkeypatch, transport)
    out = await search.candidates("q", 5.0)
    # mutation guard: drop the sort → landscape id=1 leads and this fails
    assert [c.video_id for c in out] == [3, 2, 1]


@pytest.mark.asyncio
async def test_file_pick_prefers_1080_closest_to_1920(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"videos": [
            _video(7, 1080, 1920, 10, file_heights=(720, 1080, 1920, 2160)),
        ]})
    search, transport = _search_with(handler)
    _patch_client(monkeypatch, transport)
    out = await search.candidates("q", 5.0)
    assert out[0].url.endswith("7_1920.mp4")


@pytest.mark.asyncio
async def test_three_frames_sampled_from_many(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"videos": [_video(9, 1080, 1920, 10,
                                                           n_pics=7)]})
    search, transport = _search_with(handler)
    _patch_client(monkeypatch, transport)
    out = await search.candidates("q", 5.0)
    assert len(out[0].picture_urls) == 3
    assert out[0].picture_urls[0].endswith("9_0.jpg")     # start
    assert out[0].picture_urls[-1].endswith("9_6.jpg")    # end


@pytest.mark.asyncio
async def test_no_results_returns_empty(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"videos": []})
    search, transport = _search_with(handler)
    _patch_client(monkeypatch, transport)
    assert await search.candidates("nothing", 3.0) == []


# ── judge ────────────────────────────────────────────────────────────────────

def _cand(vid):
    return Candidate(video_id=vid, url=f"https://dl/{vid}.mp4", duration=10,
                     thumbnail_url=f"https://img/{vid}.jpg",
                     picture_urls=[f"https://pic/{vid}_a.jpg"])


class VisionStub:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = 0

    async def vision_json(self, **kwargs):
        self.calls += 1
        return self.verdicts.pop(0)


@pytest.mark.asyncio
async def test_judge_accepts_first_passing():
    prov = VisionStub([
        {"meaning_match": 3, "mood_match": 8, "use": True},    # meaning too low
        {"meaning_match": 8, "mood_match": 7, "use": True},    # passes
    ])
    got = await pick_with_judge(prov, [_cand(1), _cand(2), _cand(3)],
                                segment_text="s", query="q", judge_model="m")
    assert got.video_id == 2 and prov.calls == 2


@pytest.mark.asyncio
async def test_judge_fail_open_without_vision_support():
    got = await pick_with_judge(object(), [_cand(1), _cand(2)],
                                segment_text="s", query="q", judge_model="m")
    assert got.video_id == 1        # provider has no vision_json → top hit


@pytest.mark.asyncio
async def test_judge_fail_open_on_exception():
    class Boom:
        async def vision_json(self, **kwargs):
            raise RuntimeError("api down")
    got = await pick_with_judge(Boom(), [_cand(1), _cand(2)],
                                segment_text="s", query="q", judge_model="m")
    assert got.video_id == 1        # mutation guard: judge error must not crash


@pytest.mark.asyncio
async def test_judge_nobody_passes_returns_top():
    prov = VisionStub([{"use": False}] * 3)
    got = await pick_with_judge(prov, [_cand(i) for i in (1, 2, 3, 4)],
                                segment_text="s", query="q", judge_model="m")
    assert got.video_id == 1 and prov.calls == 3      # capped at 3 judged


@pytest.mark.asyncio
async def test_judge_empty_candidates():
    assert await pick_with_judge(object(), [], segment_text="s", query="q",
                                 judge_model="m") is None


def test_candidate_frames_and_page_url():
    c = _cand(5)
    assert c.frames == ["https://pic/5_a.jpg", "https://img/5.jpg"]
    assert c.page_url == "https://www.pexels.com/video/5/"


def test_judge_prompt_mentions_scores():
    from services.broll import _judge_prompt
    p = _judge_prompt("line", "shot", 3)
    assert "meaning_match" in p and "mood_match" in p and json is not None
