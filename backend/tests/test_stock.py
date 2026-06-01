import pytest
from pytest_httpx import HTTPXMock
from services.stock import UnsplashClient, PexelsClient, StockClient, StockError

UNSPLASH_BASE = "https://api.unsplash.com"
PEXELS_BASE = "https://api.pexels.com/v1"

UNSPLASH_SEARCH_RESP = {
    "results": [
        {
            "id": "abc123",
            "urls": {"regular": "https://images.unsplash.com/photo-abc?w=1080", "thumb": "https://images.unsplash.com/photo-abc?w=200"},
            "alt_description": "A tech photo",
        }
    ]
}

UNSPLASH_PHOTO_RESP = {
    "id": "abc123",
    "urls": {"regular": "https://images.unsplash.com/photo-abc?w=1080", "thumb": "..."},
}

PEXELS_SEARCH_RESP = {
    "photos": [
        {
            "id": 999,
            "src": {
                "large": "https://images.pexels.com/photos/999/large.jpg",
                "medium": "https://images.pexels.com/photos/999/medium.jpg",
                "original": "https://images.pexels.com/photos/999/orig.jpg",
            },
            "alt": "A pexels photo",
        }
    ]
}


@pytest.mark.asyncio
async def test_unsplash_search(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{UNSPLASH_BASE}/search/photos?query=technology&per_page=5&orientation=squarish",
        json=UNSPLASH_SEARCH_RESP,
    )
    client = UnsplashClient("test-key")
    results = await client.search_photos("technology")
    assert len(results) == 1
    assert results[0].id == "abc123"
    assert results[0].source == "unsplash"
    assert results[0].alt == "A tech photo"
    await client.close()


@pytest.mark.asyncio
async def test_unsplash_search_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{UNSPLASH_BASE}/search/photos?query=fail&per_page=5&orientation=squarish",
        status_code=403,
        json={"error": "Forbidden"},
    )
    client = UnsplashClient("bad-key")
    with pytest.raises(StockError, match="403"):
        await client.search_photos("fail")
    await client.close()


@pytest.mark.asyncio
async def test_unsplash_download(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{UNSPLASH_BASE}/photos/abc123",
        json=UNSPLASH_PHOTO_RESP,
    )
    httpx_mock.add_response(
        url="https://images.unsplash.com/photo-abc?w=1080",
        content=b"fake-image-bytes",
    )
    client = UnsplashClient("test-key")
    data = await client.download_photo("abc123")
    assert data == b"fake-image-bytes"
    await client.close()


@pytest.mark.asyncio
async def test_pexels_search(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{PEXELS_BASE}/search?query=nature&per_page=5&size=medium",
        json=PEXELS_SEARCH_RESP,
    )
    client = PexelsClient("pexels-key")
    results = await client.search_photos("nature")
    assert len(results) == 1
    assert results[0].id == "999"
    assert results[0].source == "pexels"
    await client.close()


@pytest.mark.asyncio
async def test_pexels_search_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{PEXELS_BASE}/search?query=fail&per_page=5&size=medium",
        status_code=429,
        json={"error": "Rate limit"},
    )
    client = PexelsClient("key")
    with pytest.raises(StockError, match="429"):
        await client.search_photos("fail")
    await client.close()


@pytest.mark.asyncio
async def test_stock_client_delegates_to_unsplash(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{UNSPLASH_BASE}/search/photos?query=tech&per_page=5&orientation=squarish",
        json=UNSPLASH_SEARCH_RESP,
    )
    unsplash = UnsplashClient("key")
    facade = StockClient(unsplash=unsplash)
    results = await facade.search("tech", source="unsplash")
    assert results[0].source == "unsplash"
    await facade.close()


@pytest.mark.asyncio
async def test_stock_client_delegates_to_pexels(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{PEXELS_BASE}/search?query=nature&per_page=5&size=medium",
        json=PEXELS_SEARCH_RESP,
    )
    pexels = PexelsClient("key")
    facade = StockClient(pexels=pexels)
    results = await facade.search("nature", source="pexels")
    assert results[0].source == "pexels"
    await facade.close()


@pytest.mark.asyncio
async def test_stock_client_unknown_source_raises():
    facade = StockClient()
    with pytest.raises(StockError, match="not configured"):
        await facade.search("anything", source="getty")
