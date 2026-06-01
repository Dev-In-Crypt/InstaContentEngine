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
    result = await client.generate_text(
        model="anthropic/claude-sonnet-4",
        system_prompt="You are a content strategist.",
        user_prompt="Create a post about AI trends.",
    )
    assert result == "Generated caption text"
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


@pytest.mark.asyncio
async def test_generate_image_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/images/generations",
        json={"data": [{"url": "https://cdn.example.com/img.png"}]},
    )
    client = OpenRouterClient(api_key="test-key")
    url = await client.generate_image(model="openai/dall-e-3", prompt="A futuristic city")
    assert url == "https://cdn.example.com/img.png"
    await client.close()


@pytest.mark.asyncio
async def test_generate_image_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/images/generations",
        status_code=429,
        json={"error": "Rate limited"},
    )
    client = OpenRouterClient(api_key="test-key")
    with pytest.raises(OpenRouterError, match="429"):
        await client.generate_image("openai/dall-e-3", "test")
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
