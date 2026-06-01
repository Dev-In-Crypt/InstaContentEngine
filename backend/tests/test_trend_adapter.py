import json
import pytest
from pytest_httpx import HTTPXMock

from models.schemas import LengthTier, Platform
from services.openrouter import OpenRouterClient
from services.trend_adapter import AdaptedIdea, TrendAdapter, TrendAdaptError


BASE = "https://openrouter.ai/api/v1"

GOOD_PAYLOAD = {
    "hook": "Run two continents in one morning.",
    "short_script": "Line 1\nLine 2\nLine 3",
    "shot_list": ["wide bridge shot", "feet on tarmac", "finish line"],
    "caption": "Body text about the Asia-Europe run with running tips and motivation.",
    "cta": "Save this if you're training for your next marathon.",
    "hashtags": ["#running", "#marathon", "#mylifemygame"],
    "seo_keywords": ["marathon training", "running tips"],
}


def _mock_text(httpx_mock: HTTPXMock, payload: dict):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


@pytest.mark.asyncio
async def test_adapt_returns_full_idea(httpx_mock: HTTPXMock):
    _mock_text(httpx_mock, GOOD_PAYLOAD)
    client = OpenRouterClient(api_key="k")
    adapter = TrendAdapter(client)
    out = await adapter.adapt(
        source_handle="nikerunning",
        media_type="reel",
        permalink="https://instagram.com/p/X",
        caption="Original caption with hook.",
        source_hook="Original caption with hook.",
        likes=1000, comments=50, views=20000,
        platform=Platform.INSTAGRAM, length_tier=LengthTier.SWEET_SPOT,
    )
    assert isinstance(out, AdaptedIdea)
    assert out.hook.startswith("Run two")
    assert len(out.shot_list) == 3
    assert out.seo_keywords == GOOD_PAYLOAD["seo_keywords"]
    await client.close()


@pytest.mark.asyncio
async def test_adapt_system_prompt_mentions_brand(httpx_mock: HTTPXMock):
    _mock_text(httpx_mock, GOOD_PAYLOAD)
    client = OpenRouterClient(api_key="k")
    await TrendAdapter(client).adapt(
        source_handle="x", media_type="reel", permalink=None,
        caption="c", source_hook="h",
        platform=Platform.LINKEDIN, length_tier=LengthTier.DEEP_DIVE,
    )
    request = httpx_mock.get_requests()[-1]
    body = json.loads(request.content)
    sysprompt = body["messages"][0]["content"]
    assert "My Life My Game" in sysprompt
    # deep_dive should bump max_tokens
    assert body["max_tokens"] == 3000
    await client.close()


def test_parse_tolerates_string_shot_list():
    raw = json.dumps({
        **GOOD_PAYLOAD,
        "shot_list": "- shot one\n- shot two\n- shot three",
    })
    out = TrendAdapter._parse(raw)
    assert out.shot_list == ["shot one", "shot two", "shot three"]


def test_parse_tolerates_missing_seo_keywords():
    payload = dict(GOOD_PAYLOAD)
    del payload["seo_keywords"]
    out = TrendAdapter._parse(json.dumps(payload))
    assert out.seo_keywords == []


def test_parse_raises_on_missing_required():
    payload = dict(GOOD_PAYLOAD)
    del payload["hook"]
    with pytest.raises(TrendAdaptError, match="hook"):
        TrendAdapter._parse(json.dumps(payload))


def test_parse_strips_code_fences():
    raw = "```json\n" + json.dumps(GOOD_PAYLOAD) + "\n```"
    out = TrendAdapter._parse(raw)
    assert out.cta == GOOD_PAYLOAD["cta"]
