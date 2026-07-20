"""OpenRouter adapter.

Wraps the existing `services.openrouter.OpenRouterClient` rather than reimplementing
it — that client already handles retries, usage capture and the two image response
shapes. This adapter only adds the provider contract and owns web grounding: the
":online" suffix used to live in caption_generator, but it is an OpenRouter feature,
so it belongs here.
"""
from __future__ import annotations

from services.ai.base import AIError, require_model
from services.openrouter import OpenRouterClient, OpenRouterError


class OpenRouterProvider:
    """Text + images through one OpenRouter key."""

    supports_grounding = True

    def __init__(self, api_key: str, referer: str = "https://localhost",
                 app_title: str = "InstaContentEngine", ssl_verify: bool = True):
        self._client = OpenRouterClient(
            api_key=api_key, referer=referer, app_title=app_title, ssl_verify=ssl_verify,
        )

    async def generate_text(self, model: str, system_prompt: str, user_prompt: str,
                            max_tokens: int = 2000,
                            web_grounded: bool = False) -> tuple[str, list[dict]]:
        model_id = require_model(model, "text")
        try:
            # The ':online' grounding suffix is applied by the client itself.
            return await self._client.generate_text(
                model=model_id, system_prompt=system_prompt,
                user_prompt=user_prompt, max_tokens=max_tokens,
                web_grounded=web_grounded,
            )
        except OpenRouterError as exc:
            raise AIError(str(exc)) from exc

    async def generate_image(self, model: str, prompt: str) -> bytes:
        model_id = require_model(model, "image")
        try:
            return await self._client.generate_image(model=model_id, prompt=prompt)
        except OpenRouterError as exc:
            raise AIError(str(exc)) from exc

    async def close(self) -> None:
        await self._client.close()
