"""Google Gemini adapter (AI Studio key).

One endpoint for both modalities: POST /v1beta/models/{model}:generateContent with
the key as a query parameter. Text comes back in candidates[].content.parts[].text;
generated images come back in the same parts as inlineData.data (base64).
"""
from __future__ import annotations

import asyncio
import base64

import httpx

from services.ai.base import AIError, require_model
from services.ai.catalog import estimate_cost
from services.http_utils import describe_request_error
from services.openrouter import record_usage


class GoogleProvider:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    supports_grounding = False

    def __init__(self, api_key: str, ssl_verify: bool = True):
        self._api_key = api_key
        self._ssl_verify = ssl_verify
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={"Content-Type": "application/json"},
                timeout=120.0,
                verify=self._ssl_verify,
            )
        return self._client

    async def _generate(self, model_id: str, payload: dict) -> dict:
        client = self._get_client()
        path = f"/models/{model_id}:generateContent"
        for attempt in range(3):
            try:
                response = await client.post(path, json=payload,
                                             params={"key": self._api_key})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503) and attempt < 2:
                    await asyncio.sleep(3.0 * (attempt + 1))
                    continue
                raise AIError(
                    f"Google failed: {e.response.status_code} {e.response.text}") from e
            except httpx.RequestError as e:
                raise AIError(describe_request_error(e, "Google")) from e
        raise AIError("Google: retries exhausted")

    @staticmethod
    def _parts(data: dict) -> list[dict]:
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        return (candidates[0].get("content") or {}).get("parts") or []

    def _record(self, model_id: str, data: dict) -> None:
        usage = data.get("usageMetadata") or {}
        prompt_tokens = usage.get("promptTokenCount")
        completion_tokens = usage.get("candidatesTokenCount")
        record_usage(model_id, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": usage.get("totalTokenCount"),
            "cost": estimate_cost("google", model_id, prompt_tokens, completion_tokens),
        })

    async def generate_text(self, model: str, system_prompt: str, user_prompt: str,
                            max_tokens: int = 2000,
                            web_grounded: bool = False) -> tuple[str, list[dict]]:
        model_id = require_model(model, "text")
        data = await self._generate(model_id, {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        })
        self._record(model_id, data)
        text = "".join(p.get("text", "") for p in self._parts(data) if "text" in p)
        if not text:
            raise AIError(f"Google: no text in response: {data!r}")
        return text, []

    async def generate_image(self, model: str, prompt: str) -> bytes:
        model_id = require_model(model, "image")
        data = await self._generate(model_id, {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        })
        self._record(model_id, data)
        for part in self._parts(data):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        raise AIError(f"Google: no image in response: {data!r}")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
