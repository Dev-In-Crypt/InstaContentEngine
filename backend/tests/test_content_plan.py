"""The topic planner: balanced, distinct, on-brand — the gate before spending."""
import json
from unittest.mock import AsyncMock

import pytest

from services.caption_generator import CaptionParseError
from services.content_plan import plan_topics


def _provider(*replies):
    """A text provider that returns each reply's (content, citations) in turn."""
    p = AsyncMock()
    p.generate_text = AsyncMock(side_effect=[(r, []) for r in replies])
    return p


def _items(n):
    return json.dumps([
        {"topic": f"Specific topic number {i}", "pillar": "educational", "angle": "why"}
        for i in range(n)
    ])


@pytest.mark.asyncio
async def test_returns_the_requested_topics():
    out = await plan_topics(_provider(_items(3)), niche="Fitness",
                            target_audience="Beginners", theme=None,
                            platform="instagram", count=3)
    assert len(out) == 3
    assert all(set(it) == {"topic", "pillar", "angle"} for it in out)


@pytest.mark.asyncio
async def test_unknown_pillar_falls_back_to_classification():
    reply = json.dumps([{"topic": "How to start running without injury",
                         "pillar": "not-a-real-pillar", "angle": "x"}])
    out = await plan_topics(_provider(reply), niche="Fitness", target_audience="x",
                            theme=None, platform="instagram", count=1)
    # "how to" scores the educational pillar via classify_pillar.
    assert out[0]["pillar"] == "educational"


@pytest.mark.asyncio
async def test_restated_duplicates_are_dropped():
    reply = json.dumps([
        {"topic": "Morning routine for focus", "pillar": "personal", "angle": "a"},
        {"topic": "morning   routine for focus", "pillar": "educational", "angle": "b"},
        {"topic": "A different idea entirely", "pillar": "community", "angle": "c"},
    ])
    out = await plan_topics(_provider(reply), niche="x", target_audience="x",
                            theme=None, platform="instagram", count=5)
    assert len(out) == 2                      # the duplicate collapsed


@pytest.mark.asyncio
async def test_broken_json_is_repaired():
    """A trailing comma must not sink the plan — loads_lenient handles it."""
    reply = '[{"topic": "Real topic", "pillar": "educational", "angle": "x"},]'
    out = await plan_topics(_provider(reply), niche="x", target_audience="x",
                            theme=None, platform="instagram", count=1)
    assert out[0]["topic"] == "Real topic"


@pytest.mark.asyncio
async def test_empty_first_reply_retries_then_succeeds():
    p = _provider("sorry, no", _items(2))
    out = await plan_topics(p, niche="x", target_audience="x", theme=None,
                            platform="instagram", count=2)
    assert len(out) == 2
    assert p.generate_text.await_count == 2


@pytest.mark.asyncio
async def test_two_empty_replies_raise():
    p = _provider("nope", "still nope")
    with pytest.raises(CaptionParseError):
        await plan_topics(p, niche="x", target_audience="x", theme=None,
                          platform="instagram", count=2)
    assert p.generate_text.await_count == 2


@pytest.mark.asyncio
async def test_a_wrapped_array_is_unwrapped():
    reply = json.dumps({"topics": [
        {"topic": "Wrapped topic", "pillar": "educational", "angle": "x"}]})
    out = await plan_topics(_provider(reply), niche="x", target_audience="x",
                            theme=None, platform="instagram", count=1)
    assert out[0]["topic"] == "Wrapped topic"
