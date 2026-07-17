"""Adapts the URL-based Instagram Graph publisher to the byte-based Publisher
protocol: upload slides to imgbb, then create a single or carousel IG media.

Wraps the existing services.image_host.ImgbbUploader and
services.instagram.InstagramPublisher unchanged — this is the seam that used to
live inline in publisher_flow.publish_now.
"""
from __future__ import annotations

from typing import Optional

from services.image_host import ImageHostError, ImgbbUploader
from services.instagram import InstagramError, InstagramPublisher
from services.publishing.base import PublishOutcome, PublisherError


class InstagramPlatformPublisher:
    def __init__(self, access_token: str, ig_user_id: str, imgbb_api_key: str,
                 name_prefix: str = "slide"):
        self._uploader = ImgbbUploader(imgbb_api_key)
        self._publisher = InstagramPublisher(access_token=access_token, ig_user_id=ig_user_id)
        self._name_prefix = name_prefix or "slide"

    async def publish(self, images: list[bytes], caption: str,
                      alt_text: Optional[str] = None) -> PublishOutcome:
        try:
            urls = await self._uploader.upload_many(images, name_prefix=self._name_prefix)
            if len(urls) == 1:
                media_id = await self._publisher.publish_single(
                    image_url=urls[0], caption=caption, alt_text=alt_text or "",
                )
            else:
                media_id = await self._publisher.publish_carousel(
                    image_urls=urls, caption=caption,
                )
        except (ImageHostError, InstagramError) as e:
            raise PublisherError(str(e)) from e
        return PublishOutcome(media_id=media_id, image_urls=urls)

    async def close(self) -> None:
        await self._uploader.close()
        await self._publisher.close()
