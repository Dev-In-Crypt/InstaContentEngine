from unittest.mock import AsyncMock, patch

import pytest
import httpx
from pytest_httpx import HTTPXMock
from services.openrouter import OpenRouterClient, OpenRouterError, TEXT_MODELS, IMAGE_MODELS

BASE = "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_generate_text_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": "Generated caption text"}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    content, citations = await client.generate_text(
        model="anthropic/claude-sonnet-4",
        system_prompt="You are a content strategist.",
        user_prompt="Create a post about AI trends.",
    )
    assert content == "Generated caption text"
    assert citations == []
    await client.close()


@pytest.mark.asyncio
async def test_generate_text_extracts_url_citations(httpx_mock: HTTPXMock):
    """OpenRouter :online responses include annotations[].url_citation — we flatten them."""
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {
            "content": "Body",
            "annotations": [
                {"type": "url_citation", "url_citation": {"url": "https://a.example/1", "title": "Article 1"}},
                {"type": "url_citation", "url_citation": {"url": "https://b.example/2", "title": "Article 2"}},
                {"type": "other", "url_citation": {"url": "ignored"}},
            ],
        }}]},
    )
    client = OpenRouterClient(api_key="key")
    content, citations = await client.generate_text(
        model="m", system_prompt="s", user_prompt="u",
    )
    assert content == "Body"
    assert citations == [
        {"title": "Article 1", "url": "https://a.example/1"},
        {"title": "Article 2", "url": "https://b.example/2"},
    ]
    await client.close()


@pytest.mark.asyncio
async def test_generate_text_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        status_code=401,
        json={"error": "Unauthorized"},
    )
    client = OpenRouterClient(api_key="bad-key")
    with pytest.raises(OpenRouterError, match="401"):
        await client.generate_text("model", "sys", "user")
    await client.close()


# Images go through /chat/completions (Gemini-style) and return raw bytes —
# not the legacy /images/generations URL contract.

@pytest.mark.asyncio
async def test_generate_image_from_data_url(httpx_mock: HTTPXMock):
    import base64
    raw = b"\xff\xd8\xff\xe0-jpeg-bytes"
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {
            "images": [{"type": "image_url", "image_url": {"url": data_url}}],
        }}]},
    )
    client = OpenRouterClient(api_key="test-key")
    out = await client.generate_image(model="google/gemini-2.5-flash", prompt="A city")
    assert out == raw
    await client.close()


@pytest.mark.asyncio
async def test_generate_image_downloads_http_url(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {
            "images": [{"type": "image_url",
                        "image_url": {"url": "https://cdn.example.com/img.png"}}],
        }}]},
    )
    httpx_mock.add_response(url="https://cdn.example.com/img.png", content=b"png-bytes")
    client = OpenRouterClient(api_key="test-key")
    out = await client.generate_image(model="m", prompt="p")
    assert out == b"png-bytes"
    await client.close()


@pytest.mark.asyncio
async def test_generate_image_no_image_raises(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": "sorry, text only"}}]},
    )
    client = OpenRouterClient(api_key="test-key")
    with pytest.raises(OpenRouterError, match="No image found"):
        await client.generate_image("m", "p")
    await client.close()


@pytest.mark.asyncio
async def test_generate_image_http_error(httpx_mock: HTTPXMock):
    # 429 is retried (retries=2) → the same response must serve all attempts,
    # and the backoff sleep is stubbed so the test stays fast.
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        status_code=429,
        json={"error": "Rate limited"},
        is_reusable=True,
    )
    client = OpenRouterClient(api_key="test-key")
    with patch("services.openrouter.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(OpenRouterError, match="429"):
            await client.generate_image("m", "p")
    await client.close()


@pytest.mark.asyncio
async def test_generate_text_sends_correct_payload(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenRouterClient(api_key="key", app_title="MyApp")
    await client.generate_text("gpt-4o", "sys", "user", max_tokens=500)

    request = httpx_mock.get_request()
    import json
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"
    assert body["max_tokens"] == 500
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    await client.close()


@pytest.mark.asyncio
async def test_context_manager():
    async with OpenRouterClient(api_key="key") as client:
        assert client is not None


def test_text_models_not_empty():
    assert len(TEXT_MODELS) > 0
    assert "claude-sonnet" in TEXT_MODELS


def test_image_models_not_empty():
    assert len(IMAGE_MODELS) > 0
    assert "dall-e-3" in IMAGE_MODELS


@pytest.mark.asyncio
async def test_client_close_idempotent():
    c = OpenRouterClient(api_key="key")
    await c.close()
    await c.close()  # Should not raise
