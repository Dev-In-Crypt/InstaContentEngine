"""Public image hosting so Instagram can fetch slide images by URL.

Instagram's Content Publishing API downloads images from a public URL when a
media container is created — it cannot reach 127.0.0.1 or a private host. We
upload each slide to imgbb (free, permanent) and hand the returned URLs to the
Graph API.
"""

from __future__ import annotations

import base64
from typing import Optional

import httpx


class ImageHostError(Exception):
    pass


class ImgbbUploader:
    BASE_URL = "https://api.imgbb.com/1/upload"

    def __init__(self, api_key: str):
        if not api_key:
            raise ImageHostError("IMGBB_API_KEY is not configured")
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def upload(self, image_bytes: bytes, name: str = "slide") -> str:
        """Upload raw image bytes, return the public display URL."""
        client = self._get_client()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        try:
            resp = await client.post(
                self.BASE_URL,
                params={"key": self._api_key},
                data={"image": b64, "name": name},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ImageHostError(
                f"imgbb upload failed: {e.response.status_code} {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise ImageHostError(f"imgbb network error: {e}") from e

        data = resp.json()
        if not data.get("success"):
            raise ImageHostError(f"imgbb rejected upload: {data}")
        url = (data.get("data") or {}).get("url")
        if not url:
            raise ImageHostError(f"imgbb response missing url: {data}")
        return url

    async def upload_many(self, images: list[bytes], name_prefix: str = "slide") -> list[str]:
        return [
            await self.upload(img, name=f"{name_prefix}_{i + 1}")
            for i, img in enumerate(images)
        ]

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
