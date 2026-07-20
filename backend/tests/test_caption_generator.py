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


def test_json_spec_reaches_model_with_single_braces():
    """Regression: `_JSON_FORMAT` is substituted as a .format() VALUE, so its braces
    are never unescaped. Doubling them shipped a literal '{{' to the model, which
    cheap models copied verbatim and emitted invalid JSON — measured ~50% failed
    generations on X with deepseek before this was fixed."""
    from models.schemas import LengthTier
    from services.caption_generator import (
        INSTAGRAM_SYSTEM_PROMPT, LINKEDIN_SYSTEM_PROMPT, X_SYSTEM_PROMPT,
        LENGTH_TIER_INSTRUCTIONS, _JSON_FORMAT, _frame_brand_voice,
    )
    for template in (INSTAGRAM_SYSTEM_PROMPT, LINKEDIN_SYSTEM_PROMPT, X_SYSTEM_PROMPT):
        rendered = template.format(
            brand_voice=_frame_brand_voice(None), tone="professional",
            length_instruction=LENGTH_TIER_INSTRUCTIONS[LengthTier.SWEET_SPOT],
            json_format=_JSON_FORMAT,
        )
        assert "{{" not in rendered and "}}" not in rendered
        # and the example the model is told to copy must be parseable JSON shape
        assert '\n{\n' in rendered


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
    """platform=X must send the X system prompt (250-char rule), not the IG one."""
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
    assert "250 characters" in system
    assert "X (Twitter)" in system


# ── X post modes: short / thread / long ─────────────────────────────────────

THREAD_JSON = {
    **GOOD_JSON,
    "thread": [
        "Blends hide what a farm actually tastes like.",
        "Filter brewing pulls those notes forward instead of compressing them.",
        "Start with a washed Kenyan if you want the difference to be obvious. #coffee",
    ],
}


@pytest.mark.asyncio
async def test_thread_mode_prompt_carries_the_range_and_the_no_split_rule(httpx_mock: HTTPXMock):
    from models.schemas import XPostMode
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(THREAD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    await gen.generate(topic="Single origin", format="single", platform=Platform.X,
                       x_mode=XPostMode.THREAD, thread_min=3, thread_max=6,
                       web_grounded=False)
    system, _user = _sys_user(httpx_mock)
    assert "between 3 and 6 tweets" in system
    assert "250 characters or fewer" in system
    assert "NEVER split a" in system          # the coherence rule the owner asked for
    assert '"thread"' in system               # the model is shown the field
    await client.close()


@pytest.mark.asyncio
async def test_thread_mode_returns_parts(httpx_mock: HTTPXMock):
    from models.schemas import XPostMode
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(THREAD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    res = await gen.generate(topic="t", format="single", platform=Platform.X,
                             x_mode=XPostMode.THREAD, web_grounded=False)
    assert res.thread_parts == THREAD_JSON["thread"]
    await client.close()


@pytest.mark.asyncio
async def test_thread_parts_are_capped_at_thread_max(httpx_mock: HTTPXMock):
    from models.schemas import XPostMode
    many = {**GOOD_JSON, "thread": [f"Tweet number {i}." for i in range(9)]}
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(many)}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    res = await gen.generate(topic="t", format="single", platform=Platform.X,
                             x_mode=XPostMode.THREAD, thread_min=2, thread_max=4,
                             web_grounded=False)
    assert len(res.thread_parts) == 4
    await client.close()


@pytest.mark.asyncio
async def test_over_long_tweet_is_brought_under_the_limit(httpx_mock: HTTPXMock):
    """End-to-end of the two-stage enforcement: the model overshoots, we fix it."""
    from models.schemas import TWEET_CHAR_LIMIT, XPostMode
    over = {**GOOD_JSON, "thread": ["word " * 200, "Short one."]}
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(over)}}]},
    )
    # the shortener call also hits the mocked endpoint and returns the same body,
    # which is still too long — so the deterministic cut must save it
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": "still " * 100}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    res = await gen.generate(topic="t", format="single", platform=Platform.X,
                             x_mode=XPostMode.THREAD, web_grounded=False)
    assert all(len(p) <= TWEET_CHAR_LIMIT for p in res.thread_parts)
    await client.close()


@pytest.mark.asyncio
async def test_long_mode_uses_the_long_prompt(httpx_mock: HTTPXMock):
    from models.schemas import XPostMode
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="k")
    gen = CaptionGenerator(client)
    res = await gen.generate(topic="t", format="single", platform=Platform.X,
                             x_mode=XPostMode.LONG, web_grounded=False)
    system, _user = _sys_user(httpx_mock)
    assert "long-form" in system.lower()
    # wording wraps across lines in the template, so check it without the newline
    assert "280-character cap" in system and "does not apply" in system
    assert res.thread_parts == []          # a long post is not a thread
    await client.close()


def test_parse_without_thread_is_unaffected():
    """Existing Instagram/LinkedIn responses have no 'thread' key — soft parse."""
    gen = CaptionGenerator(OpenRouterClient(api_key="k"))
    assert gen._parse(json.dumps(GOOD_JSON)).thread_parts == []


@pytest.mark.asyncio
async def test_short_x_caption_leaves_room_for_the_hashtags(httpx_mock: HTTPXMock):
    """"250 characters including everything": the hashtags are appended at publish,
    so caption + hashtags — not the caption alone — must fit the budget."""
    from models.schemas import TWEET_CHAR_LIMIT, XPostMode
    long_caption = dict(GOOD_JSON,
                        caption="word " * 80,                    # ~400 chars
                        hashtags=["#SleepBetter", "#HealthyHabits"])
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(long_caption)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    gen.shorten_text = AsyncMock(side_effect=AssertionError("offline: use the hard cut"))
    result = await gen.generate(topic="sleep", format="single", num_slides=1,
                                platform=Platform.X, x_mode=XPostMode.SHORT)
    await client.close()

    tags = " ".join(result.hashtags)
    assert len(f"{result.caption}\n\n{tags}") <= TWEET_CHAR_LIMIT


@pytest.mark.asyncio
async def test_short_x_caption_within_budget_is_untouched(httpx_mock: HTTPXMock):
    """A caption that already fits must not be reworded or ellipsised."""
    from models.schemas import XPostMode
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": json.dumps(GOOD_JSON)}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    gen = CaptionGenerator(client)
    result = await gen.generate(topic="AI", format="single", num_slides=1,
                                platform=Platform.X, x_mode=XPostMode.SHORT)
    await client.close()
    assert result.caption == GOOD_JSON["caption"]


def test_short_x_prompt_keeps_hashtags_out_of_the_caption():
    """publisher_flow appends the hashtags field to the caption. If the prompt also
    asks for them inline, every short X post ships its hashtags twice."""
    from services.caption_generator import X_SYSTEM_PROMPT
    assert "WITHOUT hashtags" in X_SYSTEM_PROMPT
    assert "hashtags included" not in X_SYSTEM_PROMPT
