import asyncio
from typing import Optional

import httpx


class InstagramError(Exception):
    pass


class InstagramPublisher:
    BASE_URL = "https://graph.instagram.com"
    API_VERSION = "v25.0"

    def __init__(self, access_token: str, ig_user_id: str):
        self.token = access_token
        self.ig_user_id = ig_user_id
        self._client = httpx.AsyncClient(timeout=60.0)

    # ------------------------------------------------------------------
    # Public publishing methods
    # ------------------------------------------------------------------

    async def publish_single(
        self,
        image_url: str,
        caption: str,
        alt_text: Optional[str] = None,
    ) -> str:
        """Create + publish a single-image post. Returns media ID."""
        params: dict = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.token,
        }
        if alt_text:
            params["alt_text"] = alt_text

        container_id = await self._create_container(params)
        await self._wait_for_container(container_id)
        return await self._publish_container(container_id)

    async def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
    ) -> str:
        """Create + publish a carousel post (2–10 images). Returns media ID."""
        if not 2 <= len(image_urls) <= 10:
            raise InstagramError(f"Carousel requires 2–10 images, got {len(image_urls)}")

        child_ids: list[str] = []
        for url in image_urls:
            child_id = await self._create_container({
                "image_url": url,
                "is_carousel_item": True,
                "access_token": self.token,
            })
            child_ids.append(child_id)

        carousel_id = await self._create_container({
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": self.token,
        })
        await self._wait_for_container(carousel_id)
        return await self._publish_container(carousel_id)

    # NOTE: Instagram's Graph API has NO native scheduled publishing (the
    # `published:false` + `publish_time` params are a Facebook Pages feature,
    # not Instagram). Scheduling is handled by services/scheduler.py, which
    # calls publish_single / publish_carousel at the scheduled moment.

    async def get_insights(self, media_id: str, is_video: bool = False) -> dict:
        """Fetch metrics for a published media object. Returns a flat dict of
        {metric_name: value} plus 'raw' with the full Graph response."""
        metrics = ["reach", "likes", "comments", "saved", "shares", "total_interactions"]
        if is_video:
            metrics += ["views"]
        url = f"{self.BASE_URL}/{self.API_VERSION}/{media_id}/insights"
        try:
            resp = await self._client.get(
                url, params={"metric": ",".join(metrics), "access_token": self.token}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Some metrics are invalid for certain media types — retry with a
            # minimal safe set before giving up.
            if e.response.status_code == 400:
                try:
                    resp = await self._client.get(url, params={
                        "metric": "reach,likes,comments,saved,shares",
                        "access_token": self.token,
                    })
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e2:
                    raise InstagramError(
                        f"Insights fetch failed: {e2.response.status_code} {e2.response.text[:200]}"
                    ) from e2
            else:
                raise InstagramError(
                    f"Insights fetch failed: {e.response.status_code} {e.response.text[:200]}"
                ) from e

        payload = resp.json()
        flat: dict = {}
        for item in payload.get("data", []):
            name = item.get("name")
            values = item.get("values") or [{}]
            flat[name] = values[0].get("value")
        flat["raw"] = payload
        return flat

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_container(self, params: dict) -> str:
        url = f"{self.BASE_URL}/{self.API_VERSION}/{self.ig_user_id}/media"
        try:
            resp = await self._client.post(url, json=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise InstagramError(
                f"Failed to create media container: {e.response.status_code} {e.response.text}"
            ) from e
        return resp.json()["id"]

    async def _wait_for_container(
        self, container_id: str, max_retries: int = 30, poll_interval: float = 2.0
    ) -> None:
        url = f"{self.BASE_URL}/{self.API_VERSION}/{container_id}"
        for _ in range(max_retries):
            try:
                resp = await self._client.get(
                    url, params={"fields": "status_code", "access_token": self.token}
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise InstagramError(f"Container status check failed: {e.response.status_code}") from e

            status = resp.json().get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise InstagramError(f"Container processing failed: {resp.json()}")
            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Container {container_id} did not finish within {max_retries} retries")

    async def _publish_container(self, container_id: str) -> str:
        url = f"{self.BASE_URL}/{self.API_VERSION}/{self.ig_user_id}/media_publish"
        try:
            resp = await self._client.post(
                url,
                json={"creation_id": container_id, "access_token": self.token},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise InstagramError(
                f"Failed to publish container: {e.response.status_code} {e.response.text}"
            ) from e
        return resp.json()["id"]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
