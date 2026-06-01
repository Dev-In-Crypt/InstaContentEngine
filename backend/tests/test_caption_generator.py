import json
import pytest
from pytest_httpx import HTTPXMock
from unittest.mock import AsyncMock
from models.schemas import Platform, LengthTier
from services.caption_generator import CaptionGenerator, CaptionParseError, GeneratedCaption
from services.openrouter import OpenRouterClient

BASE = "https://openrouter.ai/api/v1"

GOOD_JSON = {
    "caption": "This is the full caption text about AI trends that is long enough.",
    "hashtags": ["#AI", "#Tech", "#Innovation"],
    "seo_keywords": ["ai trends", "tech tips", "productivity"],
    "cta": "Follow for more tips!",
    "hook": "AI is changing everything.",
    "image_search_queries": ["futuristic AI robot", "technology abstract"],
    "image_gen_prompts": ["A glowing neural network visualization"],
    "alt_text": "An abstract image representing artificial intelligence.",
}


@pytest.mark.asyncio
async def test_generate_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    result = await gen.generate(topic="AI trends", format="single", num_slides=1)

    assert isinstance(result, GeneratedCaption)
    assert result.caption == GOOD_JSON["caption"]
    assert result.hashtags == GOOD_JSON["hashtags"]
    assert result.cta == GOOD_JSON["cta"]
    assert result.hook == GOOD_JSON["hook"]
    assert len(result.image_search_queries) == 2
    assert result.alt_text == GOOD_JSON["alt_text"]
    await client.close()


@pytest.mark.asyncio
async def test_generate_strips_markdown_fences(httpx_mock: HTTPXMock):
    wrapped = f"```json\n{json.dumps(GOOD_JSON)}\n```"
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": wrapped}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    result = await gen.generate(topic="AI trends", format="single")
    assert result.caption == GOOD_JSON["caption"]
    await client.close()


@pytest.mark.asyncio
async def test_generate_invalid_json(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": "This is not JSON at all."}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    with pytest.raises(CaptionParseError, match="Could not parse JSON"):
        await gen.generate(topic="AI trends", format="single")
    await client.close()


@pytest.mark.asyncio
async def test_generate_missing_field(httpx_mock: HTTPXMock):
    bad = dict(GOOD_JSON)
    del bad["hashtags"]
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(bad)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    with pytest.raises(CaptionParseError, match="hashtags"):
        await gen.generate(topic="AI trends", format="single")
    await client.close()


def test_parse_raw_json():
    client = OpenRouterClient(api_key="key")
    gen = CaptionGenerator(client)
    result = gen._parse(json.dumps(GOOD_JSON))
    assert result.hook == GOOD_JSON["hook"]


def test_parse_code_fence():
    client = OpenRouterClient(api_key="key")
    gen = CaptionGenerator(client)
    result = gen._parse(f"```\n{json.dumps(GOOD_JSON)}\n```")
    assert result.cta == GOOD_JSON["cta"]


def test_parse_invalid_raises():
    gen = CaptionGenerator(OpenRouterClient(api_key="key"))
    with pytest.raises(CaptionParseError):
        gen._parse("not json")


def test_parse_extracts_seo_keywords():
    gen = CaptionGenerator(OpenRouterClient(api_key="key"))
    result = gen._parse(json.dumps(GOOD_JSON))
    assert result.seo_keywords == GOOD_JSON["seo_keywords"]


def test_parse_tolerates_missing_seo_keywords():
    gen = CaptionGenerator(OpenRouterClient(api_key="key"))
    payload = dict(GOOD_JSON)
    del payload["seo_keywords"]
    result = gen._parse(json.dumps(payload))
    assert result.seo_keywords == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [Platform.INSTAGRAM, Platform.LINKEDIN])
async def test_generate_per_platform(httpx_mock: HTTPXMock, platform):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    result = await gen.generate(topic="AI", format="single", platform=platform)
    assert result.seo_keywords == GOOD_JSON["seo_keywords"]
    # the system prompt sent must match the platform
    request = httpx_mock.get_requests()[-1]
    sent = json.loads(request.content)["messages"][0]["content"]
    if platform == Platform.LINKEDIN:
        assert "LinkedIn" in sent
    else:
        assert "Instagram" in sent
    await client.close()


@pytest.mark.asyncio
async def test_deep_dive_raises_max_tokens(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    await gen.generate(topic="AI", format="single", length_tier=LengthTier.DEEP_DIVE)
    request = httpx_mock.get_requests()[-1]
    assert json.loads(request.content)["max_tokens"] == 3000
    await client.close()
