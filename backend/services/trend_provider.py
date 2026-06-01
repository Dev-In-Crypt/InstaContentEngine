"""Trend discovery providers — abstract interface + concrete implementations.

`TrendProvider` is the Protocol every implementation conforms to. Today we ship:
- `InstagramBusinessDiscoveryProvider` — uses Meta Graph API's business_discovery.
- `ScraperTrendProvider` — placeholder for a future Apify/RapidAPI integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

import httpx


class TrendProviderError(Exception):
    pass


@dataclass
class FetchedMedia:
    """Provider-agnostic DTO returned by every TrendProvider."""
    ig_media_id: str
    source_handle: str
    media_type: str                     # reel | video | image | carousel
    permalink: Optional[str] = None
    thumbnail_url: Optional[str] = None
    caption: Optional[str] = None
    likes: int = 0
    comments: int = 0
    views: Optional[int] = None
    posted_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict)


class TrendProvider(Protocol):
    async def fetch_for_handles(
        self, handles: list[str], limit_per: int = 10
    ) -> list[FetchedMedia]: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MEDIA_TYPE_MAP = {
    # Meta returns media_type ("IMAGE"|"VIDEO"|"CAROUSEL_ALBUM") and media_product_type
    # ("FEED"|"REELS"|"STORY"). We normalize to lowercase strings.
    ("VIDEO", "REELS"): "reel",
    ("VIDEO", "FEED"): "video",
    ("IMAGE", "FEED"): "image",
    ("CAROUSEL_ALBUM", "FEED"): "carousel",
}


def _normalize_media_type(media_type: Optional[str], product_type: Optional[str]) -> str:
    if media_type is None:
        return "image"
    key = (media_type.upper(), (product_type or "FEED").upper())
    if key in _MEDIA_TYPE_MAP:
        return _MEDIA_TYPE_MAP[key]
    if media_type.upper() == "VIDEO":
        return "reel" if (product_type or "").upper() == "REELS" else "video"
    if media_type.upper() == "CAROUSEL_ALBUM":
        return "carousel"
    return "image"


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Graph API returns ISO 8601 with offset (e.g. 2024-01-15T12:34:56+0000)
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Instagram Business Discovery (Meta Graph API)
# ---------------------------------------------------------------------------

class InstagramBusinessDiscoveryProvider:
    """Fetch recent media of public IG Business / Creator accounts via Graph API.

    Requires an IG Business / Creator account of your own and a long-lived
    access token with `instagram_basic` + `instagram_manage_insights` permissions.
    """

    BASE_URL = "https://graph.instagram.com"
    API_VERSION = "v25.0"
    MEDIA_FIELDS = (
        "id,media_type,media_product_type,permalink,caption,"
        "like_count,comments_count,thumbnail_url,media_url,timestamp"
    )

    def __init__(self, access_token: str, ig_user_id: str):
        if not access_token or not ig_user_id:
            raise TrendProviderError(
                "InstagramBusinessDiscoveryProvider requires both access_token and ig_user_id."
            )
        self._token = access_token
        self._ig_user_id = ig_user_id
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def fetch_for_handles(
        self, handles: list[str], limit_per: int = 10
    ) -> list[FetchedMedia]:
        out: list[FetchedMedia] = []
        for handle in handles:
            try:
                items = await self._fetch_one(handle, limit_per)
                out.extend(items)
            except TrendProviderError:
                # Per-handle failure should not abort the whole refresh.
                continue
        return out

    async def _fetch_one(self, handle: str, limit_per: int) -> list[FetchedMedia]:
        handle = handle.lstrip("@")
        client = self._get_client()
        url = f"{self.BASE_URL}/{self.API_VERSION}/{self._ig_user_id}"
        fields = (
            f"business_discovery.username({handle})"
            f"{{media.limit({limit_per}){{{self.MEDIA_FIELDS}}}}}"
        )
        try:
            resp = await client.get(url, params={"fields": fields, "access_token": self._token})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TrendProviderError(
                f"business_discovery failed for @{handle}: "
                f"{e.response.status_code} {e.response.text[:300]}"
            ) from e
        except httpx.RequestError as e:
            raise TrendProviderError(f"network error fetching @{handle}: {e}") from e

        data = resp.json().get("business_discovery", {})
        media_data = (data.get("media") or {}).get("data", []) or []
        return [
            FetchedMedia(
                ig_media_id=str(item.get("id")),
                source_handle=handle,
                media_type=_normalize_media_type(
                    item.get("media_type"), item.get("media_product_type")
                ),
                permalink=item.get("permalink"),
                thumbnail_url=item.get("thumbnail_url") or item.get("media_url"),
                caption=item.get("caption"),
                likes=int(item.get("like_count") or 0),
                comments=int(item.get("comments_count") or 0),
                views=None,  # business_discovery doesn't expose play_count for others
                posted_at=_parse_ts(item.get("timestamp")),
                raw=item,
            )
            for item in media_data
            if item.get("id")
        ]

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Placeholder for a future 3rd-party scraper (Apify / RapidAPI / etc.)
# ---------------------------------------------------------------------------

class ScraperTrendProvider:
    async def fetch_for_handles(
        self, handles: list[str], limit_per: int = 10
    ) -> list[FetchedMedia]:
        raise TrendProviderError(
            "ScraperTrendProvider is not implemented yet. "
            "Use trend_provider='business_discovery' or wire an Apify/RapidAPI integration."
        )

    async def close(self) -> None:
        return None
