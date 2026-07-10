import json
import pytest
from pytest_httpx import HTTPXMock

from models.schemas import Platform
from services.caption_generator import CaptionGenerator, CaptionParseError
from services.openrouter import OpenRouterClient

BASE = "https://openrouter.ai/api/v1"


def _mock(httpx_mock, content):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={"choices": [{"message": {"content": content}}]},
    )


@pytest.mark.asyncio
async def test_regenerate_text_field_returns_strings(httpx_mock: HTTPXMock):
    _mock(httpx_mock, json.dumps({"variants": ["Hook A.", "Hook B.", "Hook C."]}))
    gen = CaptionGenerator(OpenRouterClient(api_key="k"))
    out = await gen.regenerate_field(
        field="hook", topic="Running", current_value="Old hook.",
        platform=Platform.INSTAGRAM, text_model="m", count=3,
    )
    assert out == ["Hook A.", "Hook B.", "Hook C."]


@pytest.mark.asyncio
async def test_regenerate_list_field_returns_lists(httpx_mock: HTTPXMock):
    _mock(httpx_mock, json.dumps({"variants": [["#run", "#fit"], ["#health", "#gym"]]}))
    gen = CaptionGenerator(OpenRouterClient(api_key="k"))
    out = await gen.regenerate_field(
        field="hashtags", topic="Running", current_value=["#a"],
        text_model="m", count=2,
    )
    assert out == [["#run", "#fit"], ["#health", "#gym"]]


@pytest.mark.asyncio
async def test_regenerate_list_field_tolerates_string_variant(httpx_mock: HTTPXMock):
    _mock(httpx_mock, json.dumps({"variants": ["#run #fit #health"]}))
    gen = CaptionGenerator(OpenRouterClient(api_key="k"))
    out = await gen.regenerate_field(
        field="seo_keywords", topic="x", current_value=[], text_model="m",
    )
    assert out == [["#run", "#fit", "#health"]]


@pytest.mark.asyncio
async def test_regenerate_unsupported_field_raises():
    gen = CaptionGenerator(OpenRouterClient(api_key="k"))
    with pytest.raises(CaptionParseError):
        await gen.regenerate_field(field="banana", topic="x", current_value="y", text_model="m")
