"""Anthropic adapter (direct account).

Shape differs from OpenAI/OpenRouter in three ways that matter here:
  * endpoint is /v1/messages, auth header is `x-api-key` (not Bearer) plus a
    required `anthropic-version`;
  * the system prompt is a top-level field, NOT a message with role="system";
  * the reply is a list of content blocks — text lives in blocks of type "text".

Text only: Anthropic has no image-generation API, so this class deliberately has no
generate_image. The catalogue marks it as such and the factory refuses to build it
as an image provider.
"""
from __future__ import annotations

import asyncio

import httpx

from services.ai.base import AIError, require_model
from services.ai.catalog import estimate_cost
from services.http_utils import describe_request_error
from services.openrouter import record_usage


class AnthropicProvider:
    BASE_URL = "https://api.anthropic.com/v1"
    API_VERSION = "2023-06-01"
    supports_grounding = False

    def __init__(self, api_key: str, ssl_verify: bool = True):
        self._api_key = api_key
        self._ssl_verify = ssl_verify
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={"x-api-key": self._api_key,
                         "anthropic-version": self.API_VERSION,
                         "Content-Type": "application/json"},
                timeout=120.0,
                verify=self._ssl_verify,
            )
        return self._client

    async def generate_text(self, model: str, system_prompt: str, user_prompt: str,
                            max_tokens: int = 2000,
                            web_grounded: bool = False) -> tuple[str, list[dict]]:
        model_id = require_model(model, "text")
        client = self._get_client()
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,          # required by Anthropic
            "system": system_prompt,           # top-level, not a message
            "messages": [{"role": "user", "content": user_prompt}],
        }
        for attempt in range(3):
            try:
                response = await client.post("/messages", json=payload)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 529, 503) and attempt < 2:
                    await asyncio.sleep(3.0 * (attempt + 1))
                    continue
                raise AIError(
                    f"Anthropic failed: {e.response.status_code} {e.response.text}") from e
            except httpx.RequestError as e:
                raise AIError(describe_request_error(e, "Anthropic")) from e
        else:                                   # pragma: no cover - loop always breaks/raises
            raise AIError("Anthropic: retries exhausted")

        data = response.json()
        usage = data.get("usage") or {}
        # Anthropic names them input/output; normalise to the shared usage shape.
        prompt_tokens = usage.get("input_tokens")
        completion_tokens = usage.get("output_tokens")
        record_usage(model_id, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
            "cost": estimate_cost("anthropic", model_id, prompt_tokens, completion_tokens),
        })
        text = "".join(
            block.get("text", "")
            for block in (data.get("content") or [])
            if block.get("type") == "text"
        )
        if not text:
            raise AIError(f"Anthropic: no text in response: {data!r}")
        return text, []

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
