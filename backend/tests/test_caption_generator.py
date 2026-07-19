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
    "slide_overlays": [
        "AI is changing everything.",
        "Robots write code now.",
        "Adapt or get left behind.",
    ],
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


def _sys_user(httpx_mock: HTTPXMock):
    """Return (system_prompt, user_prompt) from the captured OpenRouter request."""
    body = json.loads(httpx_mock.get_requests()[0].content)
    msgs = {m["role"]: m["content"] for m in body["messages"]}
    return msgs["system"], msgs["user"]


@pytest.mark.asyncio
async def test_prompts_are_niche_neutral(httpx_mock: HTTPXMock):
    """The system prompt must not hardcode the old fitness/self-dev niche."""
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    await gen.generate(topic="Sourdough baking", format="single",
                       platform=Platform.INSTAGRAM, web_grounded=False)
    system, _user = _sys_user(httpx_mock)
    low = system.lower()
    for banned in ("fitness", "running", "marathon", "personal development", "healthy habits"):
        assert banned not in low, f"system prompt still niche-locked on {banned!r}"
    await client.close()


@pytest.mark.asyncio
async def test_niche_and_brand_reach_user_prompt(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    await gen.generate(topic="Sourdough baking", format="single", niche="Artisan bakery",
                       target_audience="Home bakers", brand_name="Crumb & Co",
                       web_grounded=False)
    _system, user = _sys_user(httpx_mock)
    assert "Artisan bakery" in user
    assert "Home bakers" in user
    assert "Crumb & Co" in user
    await client.close()


@pytest.mark.asyncio
async def test_regenerate_field_uses_actual_platform(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps({"variants": ["a", "b"]})}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    await gen.regenerate_field(field="hook", topic="t", current_value="x",
                               platform=Platform.X, count=2)
    _system, user = _sys_user(httpx_mock)
    assert "valid for x" in user.lower()
    assert "valid for instagram" not in user.lower()
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


def test_parse_extracts_slide_overlays():
    gen = CaptionGenerator(OpenRouterClient(api_key="key"))
    result = gen._parse(json.dumps(GOOD_JSON))
    assert result.slide_overlays == GOOD_JSON["slide_overlays"]


def test_parse_missing_overlays_falls_back_to_hook():
    gen = CaptionGenerator(OpenRouterClient(api_key="key"))
    payload = dict(GOOD_JSON)
    del payload["slide_overlays"]
    result = gen._parse(json.dumps(payload))
    # When the model omits overlays we keep slide 1 working with the hook.
    assert result.slide_overlays == [payload["hook"]]


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
async def test_generate_appends_online_suffix_when_web_grounded(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {
            "content": json.dumps(GOOD_JSON),
            "annotations": [
                {"type": "url_citation", "url_citation": {"url": "https://x.example/a", "title": "Article A"}},
            ],
        }}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    result = await gen.generate(
        topic="t", format="single", text_model="anthropic/claude-sonnet-4",
        web_grounded=True,
    )
    # Outgoing model id had :online appended
    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert body["model"].endswith(":online")
    # Citations parsed into GeneratedCaption.sources
    assert result.sources == [{"title": "Article A", "url": "https://x.example/a"}]
    await client.close()


@pytest.mark.asyncio
async def test_generate_no_online_suffix_when_disabled(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    await gen.generate(
        topic="t", format="single", text_model="m", web_grounded=False,
    )
    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert body["model"] == "m"
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


@pytest.mark.asyncio
async def test_x_platform_uses_x_prompt(httpx_mock: HTTPXMock):
    """platform=X must send the X system prompt (280-char rule), not the IG one."""
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    await gen.generate(topic="Running", format="single", num_slides=1, platform=Platform.X)
    await client.close()

    body = json.loads(httpx_mock.get_requests()[0].content)
    system = body["messages"][0]["content"]
    assert "280 characters" in system
    assert "X (Twitter)" in system
