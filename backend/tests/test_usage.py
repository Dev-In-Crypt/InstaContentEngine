import json

import pytest
from pytest_httpx import HTTPXMock

from services.openrouter import OpenRouterClient, drain_usage, record_usage, _USAGE_BUFFER

BASE = "https://openrouter.ai/api/v1"


def setup_function():
    drain_usage()   # clear buffer between tests


@pytest.mark.asyncio
async def test_generate_text_records_usage(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE}/chat/completions",
        json={
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "total_tokens": 150, "cost": 0.0021},
        },
    )
    client = OpenRouterClient(api_key="k")
    await client.generate_text(model="m", system_prompt="s", user_prompt="u")
    await client.close()
    recs = drain_usage()
    assert len(recs) == 1
    assert recs[0]["cost"] == 0.0021
    assert recs[0]["total_tokens"] == 150
    assert recs[0]["model"] == "m"


def test_record_usage_ignores_none():
    drain_usage()
    record_usage("m", None)
    assert drain_usage() == []


def test_drain_clears_buffer():
    record_usage("m", {"cost": 1.0, "total_tokens": 10})
    assert len(drain_usage()) == 1
    assert drain_usage() == []
