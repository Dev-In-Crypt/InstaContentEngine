import pytest
from pytest_httpx import HTTPXMock

from services.image_host import ImgbbUploader, ImageHostError


@pytest.mark.asyncio
async def test_upload_returns_url(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.imgbb.com/1/upload?key=k",
        json={"success": True, "data": {"url": "https://i.ibb.co/abc/slide.jpg"}},
    )
    up = ImgbbUploader("k")
    url = await up.upload(b"\xff\xd8\xff", name="slide")
    assert url == "https://i.ibb.co/abc/slide.jpg"
    await up.close()


@pytest.mark.asyncio
async def test_upload_many(httpx_mock: HTTPXMock):
    for i in range(2):
        httpx_mock.add_response(
            url="https://api.imgbb.com/1/upload?key=k",
            json={"success": True, "data": {"url": f"https://i.ibb.co/{i}.jpg"}},
        )
    up = ImgbbUploader("k")
    urls = await up.upload_many([b"a", b"b"])
    assert urls == ["https://i.ibb.co/0.jpg", "https://i.ibb.co/1.jpg"]
    await up.close()


@pytest.mark.asyncio
async def test_upload_failure_raises(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.imgbb.com/1/upload?key=k",
        json={"success": False, "error": {"message": "bad"}},
    )
    up = ImgbbUploader("k")
    with pytest.raises(ImageHostError):
        await up.upload(b"x")
    await up.close()


def test_missing_key_raises():
    with pytest.raises(ImageHostError):
        ImgbbUploader("")
