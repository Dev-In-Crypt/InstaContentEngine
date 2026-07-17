"""X (Twitter) publisher — OAuth 1.0a user context.

Unlike Instagram, X takes image bytes directly: upload each image to the v1.1
media endpoint, then post a v2 tweet referencing the returned media ids. A tweet
allows at most 4 images and 280 characters.

Signing: authlib's low-level ClientAuth computes the OAuth 1.0a Authorization
header; we attach it to a plain httpx request and send the body ourselves.
(authlib's httpx auth *flow* empties non-form bodies, which would drop the v2
tweet JSON — so we sign, but don't let it touch the request.) The JSON tweet body
and the multipart media upload are both outside the OAuth1 signature base string,
which is standard.

No live verification exists yet (posting needs a paid API tier); covered by unit
tests against mocked endpoints, same as the Instagram client was.
"""
from __future__ import annotations

from typing import Optional

import httpx

from services.publishing.base import PublishOutcome, PublisherError

MAX_CHARS = 280
MAX_IMAGES = 4
_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
_TWEET_URL = "https://api.twitter.com/2/tweets"


class XPublisher:
    def __init__(self, api_key: str, api_secret: str,
                 access_token: str, access_token_secret: str):
        from authlib.oauth1 import ClientAuth
        self._signer = ClientAuth(api_key, api_secret,
                                  token=access_token, token_secret=access_token_secret)
        self._client = httpx.AsyncClient(timeout=60.0)

    def _auth(self, method: str, url: str) -> str:
        # body=None: JSON/multipart bodies are not part of the OAuth1 signature.
        _, headers, _ = self._signer.sign(method, url, {}, None)
        return headers["Authorization"]

    async def publish(self, images: list[bytes], caption: str,
                      alt_text: Optional[str] = None) -> PublishOutcome:
        media_ids = [await self._upload_media(img) for img in images[:MAX_IMAGES]]

        payload: dict = {"text": (caption or "")[:MAX_CHARS]}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}
        try:
            r = await self._client.post(
                _TWEET_URL, json=payload,
                headers={"Authorization": self._auth("POST", _TWEET_URL)},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PublisherError(
                f"X tweet failed: {e.response.status_code} {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise PublisherError(f"X network error: {e}") from e

        tweet_id = (r.json().get("data") or {}).get("id")
        if not tweet_id:
            raise PublisherError(f"X response missing tweet id: {r.text[:200]}")
        return PublishOutcome(
            media_id=tweet_id,
            permalink=f"https://x.com/i/web/status/{tweet_id}",
        )

    async def _upload_media(self, image_bytes: bytes) -> str:
        try:
            r = await self._client.post(
                _UPLOAD_URL,
                files={"media": image_bytes},   # multipart; not part of the signature
                headers={"Authorization": self._auth("POST", _UPLOAD_URL)},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PublisherError(
                f"X media upload failed: {e.response.status_code} {e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise PublisherError(f"X media network error: {e}") from e

        mid = r.json().get("media_id_string")
        if not mid:
            raise PublisherError(f"X media response missing id: {r.text[:200]}")
        return mid

    async def close(self) -> None:
        await self._client.aclose()
