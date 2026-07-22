"""OpenRouter vision-JSON call (Reels R2 b-roll judge) — mocked transport.

The message must carry content PARTS (text + image_url×N) at temperature 0 with
a json_object response_format; garbage replies must come back as {} (the judge
is fail-open, so this method must never raise on bad JSON).
"""
import json

import httpx
import pytest

from services.openrouter import OpenRouterClient


def _client_with(handler) -> OpenRouterClient:
    c = OpenRouterClient(api_key="k")
    transport = httpx.MockTransport(handler)
    c._client = httpx.AsyncClient(  # noqa: SLF001 — inject the mock transport
        transport=transport, base_url="https://openrouter.ai/api/v1")
    return c


@pytest.mark.asyncio
async def test_vision_json_message_shape():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"use": true, "meaning_match": 9}'}}],
        })

    c = _client_with(handler)
    out = await c.generate_vision_json(
        model="google/gemini-2.0-flash-001", prompt="judge this",
        image_urls=["https://a.jpg", "https://b.jpg"])
    assert out == {"use": True, "meaning_match": 9}

    content = seen["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "judge this"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://a.jpg"}}
    assert content[2] == {"type": "image_url", "image_url": {"url": "https://b.jpg"}}
    assert seen["temperature"] == 0.0
    assert seen["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_vision_json_garbage_returns_empty_dict():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "sorry, I cannot"}}],
        })
    c = _client_with(handler)
    # mutation guard: raising here would crash the fail-open judge
    assert await c.generate_vision_json(model="m", prompt="p",
                                        image_urls=["https://x.jpg"]) == {}
