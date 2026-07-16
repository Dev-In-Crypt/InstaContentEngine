"""Stock image source defaults to auto, so Pexels is a real fallback.

SlideImageConfig.stock_source defaulted to "unsplash", and the engine never
overrode it, so search_and_download's `source in ("auto","pexels")` gate skipped
Pexels entirely. If Unsplash was down or rate-limited, generation failed even
with a working Pexels key.
"""
from unittest.mock import AsyncMock

import pytest

from services.image_router import SlideImageConfig
from services.stock import StockClient, StockError, StockPhotoResult


def test_slide_config_defaults_to_auto():
    assert SlideImageConfig(slide_number=1, image_source="stock").stock_source == "auto"


async def test_auto_falls_back_to_pexels_when_unsplash_fails():
    unsplash = AsyncMock()
    unsplash.search_photos.side_effect = StockError("unsplash down")

    pexels = AsyncMock()
    pick = StockPhotoResult(id="px1", url="u", thumb_url="t", alt=None, source="pexels",
                            author_name="Jane", author_profile_url="p", source_link="s")
    pexels.search_photos.return_value = [pick]
    pexels.download_photo.return_value = b"pexels-bytes"

    client = StockClient(unsplash=unsplash, pexels=pexels)
    data, picked = await client.search_and_download("running", source="auto")

    assert data == b"pexels-bytes"
    assert picked.source == "pexels"
