import pytest
from unittest.mock import AsyncMock, MagicMock
from pytest_httpx import HTTPXMock
from models.schemas import ImageSource
from services.image_router import ImageRouter, SlideImageConfig, ImageFetchError


def make_config(source: ImageSource, **kwargs) -> SlideImageConfig:
    return SlideImageConfig(slide_number=1, image_source=source, **kwargs)


@pytest.mark.asyncio
async def test_stock_fetch_delegates_to_stock_client():
    stock = AsyncMock()
    stock.search_and_download.return_value = b"stock-image"
    router = ImageRouter(stock_client=stock)
    result = await router.fetch_image(make_config(ImageSource.STOCK, search_query="cats"))
    assert result == b"stock-image"
    stock.search_and_download.assert_awaited_once_with(query="cats", orientation="squarish")


@pytest.mark.asyncio
async def test_stock_fetch_uses_default_query_if_none():
    stock = AsyncMock()
    stock.search_and_download.return_value = b"img"
    router = ImageRouter(stock_client=stock)
    await router.fetch_image(make_config(ImageSource.STOCK, search_query=None))
    call_kwargs = stock.search_and_download.call_args.kwargs
    assert call_kwargs["query"] == "abstract background"


@pytest.mark.asyncio
async def test_stock_no_client_raises():
    router = ImageRouter()
    with pytest.raises(ImageFetchError, match="No stock client"):
        await router.fetch_image(make_config(ImageSource.STOCK))


@pytest.mark.asyncio
async def test_ai_gen_fetch(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://cdn.example.com/generated.png",
        content=b"ai-image-bytes",
    )
    openrouter = AsyncMock()
    openrouter.generate_image.return_value = "https://cdn.example.com/generated.png"
    router = ImageRouter(openrouter=openrouter)
    result = await router.fetch_image(make_config(
        ImageSource.AI_GEN, gen_prompt="a robot", gen_model="openai/dall-e-3"
    ))
    assert result == b"ai-image-bytes"
    openrouter.generate_image.assert_awaited_once_with(
        model="openai/dall-e-3", prompt="a robot", size="1024x1024"
    )


@pytest.mark.asyncio
async def test_ai_gen_uses_default_model(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://img.example.com/x.png", content=b"img")
    openrouter = AsyncMock()
    openrouter.generate_image.return_value = "https://img.example.com/x.png"
    router = ImageRouter(openrouter=openrouter)
    await router.fetch_image(make_config(ImageSource.AI_GEN, gen_prompt="abstract"))
    call_kwargs = openrouter.generate_image.call_args.kwargs
    assert call_kwargs["model"] == "openai/dall-e-3"


@pytest.mark.asyncio
async def test_ai_gen_no_openrouter_raises():
    router = ImageRouter()
    with pytest.raises(ImageFetchError, match="No OpenRouter"):
        await router.fetch_image(make_config(ImageSource.AI_GEN, gen_prompt="test"))


@pytest.mark.asyncio
async def test_canva_fetch():
    canva = AsyncMock()
    canva.export_design.return_value = b"canva-image"
    router = ImageRouter(canva_client=canva)
    result = await router.fetch_image(make_config(ImageSource.CANVA, canva_template_id="tmpl-123"))
    assert result == b"canva-image"
    canva.export_design.assert_awaited_once_with("tmpl-123", format="png")


@pytest.mark.asyncio
async def test_canva_no_client_raises():
    router = ImageRouter()
    with pytest.raises(ImageFetchError, match="No Canva"):
        await router.fetch_image(make_config(ImageSource.CANVA, canva_template_id="tmpl"))


@pytest.mark.asyncio
async def test_canva_no_template_id_raises():
    canva = AsyncMock()
    router = ImageRouter(canva_client=canva)
    with pytest.raises(ImageFetchError, match="canva_template_id"):
        await router.fetch_image(make_config(ImageSource.CANVA))
