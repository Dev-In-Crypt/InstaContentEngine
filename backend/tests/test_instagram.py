import pytest
from unittest.mock import patch, AsyncMock
from pytest_httpx import HTTPXMock
from services.instagram import InstagramPublisher, InstagramError

IG_USER = "12345678"
TOKEN = "ig-access-token"
BASE = "https://graph.instagram.com/v25.0"
IMG_URL = "https://cdn.example.com/post.jpg"
CAPTION = "Great post! #AI"

# GET polling URL always has these exact query params (dict insertion order)
def _poll_url(container_id: str) -> str:
    return f"{BASE}/{container_id}?fields=status_code&access_token={TOKEN}"


def make_publisher() -> InstagramPublisher:
    return InstagramPublisher(access_token=TOKEN, ig_user_id=IG_USER)


# --- Single post ---

@pytest.mark.asyncio
async def test_publish_single_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "container-111"})
    httpx_mock.add_response(method="GET", url=_poll_url("container-111"), json={"status_code": "FINISHED"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media_publish", json={"id": "media-999"})

    pub = make_publisher()
    result = await pub.publish_single(IMG_URL, CAPTION)
    assert result == "media-999"
    await pub.close()


@pytest.mark.asyncio
async def test_publish_single_with_alt_text(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "c1"})
    httpx_mock.add_response(method="GET", url=_poll_url("c1"), json={"status_code": "FINISHED"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media_publish", json={"id": "m1"})

    pub = make_publisher()
    result = await pub.publish_single(IMG_URL, CAPTION, alt_text="AI image")
    assert result == "m1"
    await pub.close()


@pytest.mark.asyncio
async def test_publish_single_container_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", status_code=400, json={"error": "bad"})
    pub = make_publisher()
    with pytest.raises(InstagramError, match="400"):
        await pub.publish_single(IMG_URL, CAPTION)
    await pub.close()


@pytest.mark.asyncio
async def test_publish_single_container_processing_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "c1"})
    httpx_mock.add_response(method="GET", url=_poll_url("c1"), json={"status_code": "ERROR", "message": "fail"})
    pub = make_publisher()
    with pytest.raises(InstagramError, match="processing failed"):
        await pub.publish_single(IMG_URL, CAPTION)
    await pub.close()


# --- Carousel ---

@pytest.mark.asyncio
async def test_publish_carousel_success(httpx_mock: HTTPXMock):
    # Two child containers
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "child-1"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "child-2"})
    # Carousel container
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media", json={"id": "carousel-1"})
    # Poll carousel container
    httpx_mock.add_response(method="GET", url=_poll_url("carousel-1"), json={"status_code": "FINISHED"})
    # Publish
    httpx_mock.add_response(method="POST", url=f"{BASE}/{IG_USER}/media_publish", json={"id": "media-carousel"})

    pub = make_publisher()
    result = await pub.publish_carousel(
        image_urls=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        caption="Carousel caption",
    )
    assert result == "media-carousel"
    await pub.close()


@pytest.mark.asyncio
async def test_publish_carousel_too_few_images():
    pub = make_publisher()
    with pytest.raises(InstagramError, match="2–10"):
        await pub.publish_carousel(["https://cdn.example.com/1.jpg"], "caption")
    await pub.close()


@pytest.mark.asyncio
async def test_publish_carousel_too_many_images():
    pub = make_publisher()
    urls = [f"https://cdn.example.com/{i}.jpg" for i in range(11)]
    with pytest.raises(InstagramError, match="2–10"):
        await pub.publish_carousel(urls, "caption")
    await pub.close()


# --- Insights ---

@pytest.mark.asyncio
async def test_get_insights_flattens_metrics(httpx_mock: HTTPXMock):
    import re
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"{BASE}/media-123/insights.*"),
        json={"data": [
            {"name": "reach", "values": [{"value": 1200}]},
            {"name": "likes", "values": [{"value": 88}]},
            {"name": "saved", "values": [{"value": 14}]},
        ]},
    )
    pub = make_publisher()
    metrics = await pub.get_insights("media-123")
    assert metrics["reach"] == 1200
    assert metrics["likes"] == 88
    assert metrics["saved"] == 14
    assert "raw" in metrics
    await pub.close()


# --- Container polling ---

@pytest.mark.asyncio
async def test_wait_for_container_polls_until_finished(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="GET", url=_poll_url("c1"), json={"status_code": "IN_PROGRESS"})
    httpx_mock.add_response(method="GET", url=_poll_url("c1"), json={"status_code": "FINISHED"})

    pub = make_publisher()
    with patch("services.instagram.asyncio.sleep", new_callable=AsyncMock):
        await pub._wait_for_container("c1", max_retries=5, poll_interval=0)
    await pub.close()


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
async def test_wait_for_container_timeout(httpx_mock: HTTPXMock):
    # 3 retries all return IN_PROGRESS → TimeoutError raised after loop
    for _ in range(3):
        httpx_mock.add_response(method="GET", url=_poll_url("c1"), json={"status_code": "IN_PROGRESS"})

    pub = make_publisher()
    with patch("services.instagram.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TimeoutError):
            await pub._wait_for_container("c1", max_retries=3, poll_interval=0)
    await pub.close()
