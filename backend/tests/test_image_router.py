import pytest
from unittest.mock import AsyncMock, MagicMock
from pytest_httpx import HTTPXMock
from models.schemas import ImageSource
from services.image_router import ImageRouter, SlideImageConfig, ImageFetchError


def make_config(source: ImageSource, **kwargs) -> SlideImageConfig:
    return SlideImageConfig(slide_number=1, image_source=source, **kwargs)


def _fake_picked(source: str = "unsplash"):
    """A StockPhotoResult-like object whose .as_attribution() returns a dict."""
    p = MagicMock()
    p.as_attribution.return_value = {
        "source": source, "author_name": "Jane Doe",
        "author_profile_url": "https://example.com/u", "source_link": "https://example.com/p",
    }
    return p


@pytest.mark.asyncio
async def test_stock_fetch_delegates_to_stock_client():
    stock = AsyncMock()
    stock.search_and_download.return_value = (b"stock-image", _fake_picked())
    router = ImageRouter(stock_client=stock)
    img, attrib = await router.fetch_image(make_config(ImageSource.STOCK, search_query="cats"))
    assert img == b"stock-image"
    assert attrib["author_name"] == "Jane Doe"
    assert attrib["source"] == "unsplash"


@pytest.mark.asyncio
async def test_stock_fetch_uses_default_query_if_none():
    stock = AsyncMock()
    stock.search_and_download.return_value = (b"img", _fake_picked())
    router = ImageRouter(stock_client=stock)
    await router.fetch_image(make_config(ImageSource.STOCK, search_query=None))
    call_kwargs = stock.search_and_download.call_args.kwargs
    assert call_kwargs["query"] == "abstract background"


@pytest.mark.asyncio
async def test_stock_no_client_raises():
    router = ImageRouter()
    with pytest.raises(ImageFetchError, match="No stock client"):
        await router.fetch_image(make_config(ImageSource.STOCK))


# OpenRouterClient.generate_image returns raw bytes (it resolves data-URLs and
# downloads http URLs internally), so the router just passes them through.

@pytest.mark.asyncio
async def test_ai_gen_fetch():
    openrouter = AsyncMock()
    openrouter.generate_image.return_value = b"ai-image-bytes"
    router = ImageRouter(image_provider=openrouter)
    img, attrib = await router.fetch_image(make_config(
        ImageSource.AI_GEN, gen_prompt="a robot", gen_model="openai/dall-e-3"
    ))
    assert img == b"ai-image-bytes"
    assert attrib is None          # AI images carry no stock attribution
    openrouter.generate_image.assert_awaited_once_with(
        model="openai/dall-e-3", prompt="a robot"
    )


@pytest.mark.asyncio
async def test_ai_gen_without_model_raises():
    """The route supplies DEFAULT_IMAGE_MODEL; the router itself refuses to guess."""
    openrouter = AsyncMock()
    router = ImageRouter(image_provider=openrouter)
    with pytest.raises(ImageFetchError, match="No image model selected"):
        await router.fetch_image(make_config(ImageSource.AI_GEN, gen_prompt="abstract"))


@pytest.mark.asyncio
async def test_ai_gen_no_openrouter_raises():
    router = ImageRouter()
    with pytest.raises(ImageFetchError, match="No image provider configured"):
        await router.fetch_image(make_config(ImageSource.AI_GEN, gen_prompt="test"))


@pytest.mark.asyncio
async def test_canva_fetch():
    canva = AsyncMock()
    canva.export_design.return_value = b"canva-image"
    router = ImageRouter(canva_client=canva)
    img, attrib = await router.fetch_image(make_config(ImageSource.CANVA, canva_template_id="tmpl-123"))
    assert img == b"canva-image"
    assert attrib is None    # AI / Canva don't carry stock attribution
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
