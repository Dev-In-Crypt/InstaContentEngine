"""Publisher abstraction — one implementation per social platform.

Mirrors the provider pattern used elsewhere (services/video/base.py):
a Protocol + concrete impls + a factory.

The contract is byte-based on purpose. Platforms differ in how they take media —
Instagram needs a public URL (so its adapter uploads to imgbb first), X uploads
the bytes directly — so the orchestrator (publisher_flow.publish_now) just hands
over the raw slide bytes and each publisher does its own thing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class PublisherError(Exception):
    pass


@dataclass
class PublishOutcome:
    media_id: str                          # the platform's id for the published post
    permalink: Optional[str] = None        # link to the post, for the UI
    image_urls: Optional[list[str]] = None # public URLs, if the platform used them (IG/imgbb)


class Publisher(Protocol):
    async def publish(self, images: list[bytes], caption: str,
                      alt_text: Optional[str] = None) -> PublishOutcome: ...

    async def close(self) -> None: ...
