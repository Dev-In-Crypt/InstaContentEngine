import re
import pytest
from pytest_httpx import HTTPXMock

from services.trend_provider import (
    InstagramBusinessDiscoveryProvider, ScraperTrendProvider, TrendProviderError,
)


def _bd_url_re(ig_user_id: str) -> re.Pattern:
    return re.compile(rf"^https://graph\.instagram\.com/v25\.0/{ig_user_id}(\?|$)")


@pytest.mark.asyncio
async def test_business_discovery_parses_media(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=_bd_url_re("123"),
        json={
            "business_discovery": {
                "media": {
                    "data": [
                        {
                            "id": "M1",
                            "media_type": "VIDEO",
                            "media_product_type": "REELS",
                            "permalink": "https://instagram.com/p/M1",
                            "caption": "Run hard.\n#run",
                            "like_count": 100,
                            "comments_count": 7,
                            "thumbnail_url": "https://cdn/thumb.jpg",
                            "timestamp": "2026-05-01T12:00:00+0000",
                        },
                        {
                            "id": "M2",
                            "media_type": "IMAGE",
                            "media_product_type": "FEED",
                            "permalink": "https://instagram.com/p/M2",
                            "caption": "Recovery day.",
                            "like_count": 50,
                            "comments_count": 3,
                            "media_url": "https://cdn/m2.jpg",
                            "timestamp": "2026-04-30T08:00:00+0000",
                        },
                    ]
                }
            }
        },
    )
    p = InstagramBusinessDiscoveryProvider(access_token="tok", ig_user_id="123")
    out = await p.fetch_for_handles(["@nikerunning"], limit_per=5)
    assert len(out) == 2
    assert out[0].ig_media_id == "M1"
    assert out[0].media_type == "reel"
    assert out[0].source_handle == "nikerunning"   # @ stripped
    assert out[0].thumbnail_url == "https://cdn/thumb.jpg"
    assert out[1].media_type == "image"
    assert out[1].thumbnail_url == "https://cdn/m2.jpg"  # falls back to media_url
    assert out[0].posted_at is not None
    await p.close()


@pytest.mark.asyncio
async def test_business_discovery_per_handle_failure_is_isolated(httpx_mock: HTTPXMock):
    # First handle fails, second returns one item; total should be 1, not raised.
    httpx_mock.add_response(url=_bd_url_re("9"), status_code=400, text="bad")
    httpx_mock.add_response(
        url=_bd_url_re("9"),
        json={"business_discovery": {"media": {"data": [
            {"id": "OK", "media_type": "IMAGE", "media_product_type": "FEED",
             "like_count": 1, "comments_count": 0, "timestamp": "2026-05-01T00:00:00+0000"}
        ]}}},
    )
    p = InstagramBusinessDiscoveryProvider(access_token="t", ig_user_id="9")
    out = await p.fetch_for_handles(["fails", "works"], limit_per=5)
    assert len(out) == 1 and out[0].ig_media_id == "OK"
    await p.close()


@pytest.mark.asyncio
async def test_business_discovery_empty_handles_returns_empty():
    p = InstagramBusinessDiscoveryProvider(access_token="t", ig_user_id="9")
    assert await p.fetch_for_handles([], limit_per=5) == []
    await p.close()


def test_business_discovery_requires_credentials():
    with pytest.raises(TrendProviderError):
        InstagramBusinessDiscoveryProvider(access_token="", ig_user_id="")


@pytest.mark.asyncio
async def test_scraper_provider_raises_not_implemented():
    p = ScraperTrendProvider()
    with pytest.raises(TrendProviderError):
        await p.fetch_for_handles(["x"], limit_per=1)


@pytest.mark.asyncio
async def test_business_discovery_all_handles_fail_raises(httpx_mock: HTTPXMock, caplog):
    """An expired token fails every handle identically. Returning [] silently made
    that look like 'no trends found' — it must raise and log instead."""
    import logging
    httpx_mock.add_response(url=_bd_url_re("9"), status_code=400, text="bad token")
    httpx_mock.add_response(url=_bd_url_re("9"), status_code=400, text="bad token")

    p = InstagramBusinessDiscoveryProvider(access_token="t", ig_user_id="9")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(TrendProviderError):
            await p.fetch_for_handles(["a", "b"], limit_per=5)
    assert any("a" in r.message or "b" in r.message for r in caplog.records)
    await p.close()
