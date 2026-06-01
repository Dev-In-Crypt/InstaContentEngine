import asyncio
import base64
import httpx
from typing import Optional


TEXT_MODELS: dict[str, str] = {
    "claude-sonnet": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-haiku-4",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gemini-pro": "google/gemini-2.5-pro",
    "gemini-flash": "google/gemini-2.5-flash",
    "llama-70b": "meta-llama/llama-3.3-70b-instruct",
}

IMAGE_MODELS: dict[str, str] = {
    "dall-e-3": "openai/dall-e-3",
    "flux-pro": "black-forest-labs/flux-1.1-pro",
    "sdxl": "stabilityai/stable-diffusion-xl",
}


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, referer: str = "https://localhost", app_title: str = "InstaContentEngine"):
        self.api_key = api_key
        self._referer = referer
        self._app_title = app_title
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self._referer,
                    "X-Title": self._app_title,
                },
                timeout=120.0,
                verify=False,
            )
        return self._client

    async def _post_with_retry(self, payload: dict, retries: int = 2, backoff: float = 3.0) -> httpx.Response:
        client = self._get_client()
        last_exc: Exception = RuntimeError("no attempts")
        for attempt in range(retries + 1):
            try:
                response = await client.post("/chat/completions", json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503) and attempt < retries:
                    await asyncio.sleep(backoff * (attempt + 1))
                    last_exc = e
                    continue
                raise OpenRouterError(
                    f"OpenRouter failed: {e.response.status_code} {e.response.text}"
                ) from e
        raise OpenRouterError(str(last_exc)) from last_exc

    async def generate_text(self, model: str, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        response = await self._post_with_retry({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        })
        return response.json()["choices"][0]["message"]["content"]

    async def generate_image(self, model: str, prompt: str, size: str = "1024x1024") -> bytes:
        """Generate image via chat/completions (Gemini-style). Returns raw image bytes."""
        response = await self._post_with_retry({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        })

        message = response.json()["choices"][0]["message"]

        # Check message.images list first (OpenRouter Gemini format)
        for source in [
            message.get("images") or [],
            message.get("content") if isinstance(message.get("content"), list) else [],
        ]:
            for part in source:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if url.startswith("data:"):
                        return self._decode_base64(url)
                    return await self._download_url(url)

        # Fallback: content is a plain data URL string
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith("data:"):
            return self._decode_base64(content)

        raise OpenRouterError(f"No image found in OpenRouter response. Message: {message!r}")

    @staticmethod
    def _decode_base64(data_url: str) -> bytes:
        _, b64 = data_url.split(",", 1)
        return base64.b64decode(b64)

    @staticmethod
    async def _download_url(url: str) -> bytes:
        async with httpx.AsyncClient(timeout=60.0, verify=False) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
