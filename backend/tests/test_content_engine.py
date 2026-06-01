import io
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

from models.schemas import ImageSource, PostFormat, Platform, TemplateStyle
from services.caption_generator import GeneratedCaption
from services.content_engine import ContentEngine, GeneratedPost, _num_slides
from services.image_router import SlideImageConfig
from services.brand_engine import PillowBrandEngine, BrandConfig
from services.exporter import TemplateExporter


def make_jpeg(color="blue") -> bytes:
    img = Image.new("RGB", (800, 800), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


FAKE_CAPTION = GeneratedCaption(
    caption="Full caption about AI trends.",
    hashtags=["#AI", "#Tech"],
    cta="Follow for more!",
    hook="AI is changing everything.",
    image_search_queries=["futuristic AI", "technology abstract"],
    image_gen_prompts=["A neural network visualization"],
    alt_text="An abstract AI image.",
    seo_keywords=["ai trends", "tech tips"],
)


def make_engine(image_source=ImageSource.STOCK):
    caption_gen = AsyncMock()
    caption_gen.generate.return_value = FAKE_CAPTION

    image_router = AsyncMock()
    image_router.fetch_image.return_value = make_jpeg()

    brand_engine = PillowBrandEngine(BrandConfig())
    exporter = TemplateExporter()

    engine = ContentEngine(caption_gen, image_router, brand_engine, exporter)
    return engine, caption_gen, image_router


@pytest.mark.asyncio
async def test_generate_single_post():
    engine, cap_gen, img_router = make_engine()
    post = await engine.generate_post(topic="AI trends", format=PostFormat.SINGLE)

    assert isinstance(post, GeneratedPost)
    assert post.topic == "AI trends"
    assert post.format == PostFormat.SINGLE
    assert post.caption == FAKE_CAPTION.caption
    assert post.hashtags == FAKE_CAPTION.hashtags
    assert len(post.slides) == 1
    assert post.slides[0].image_source == ImageSource.STOCK
    assert len(post.id) > 0


@pytest.mark.asyncio
async def test_generate_carousel_3_slides():
    engine, cap_gen, img_router = make_engine()
    post = await engine.generate_post(topic="Top 5 tips", format=PostFormat.CAROUSEL_3)
    assert len(post.slides) == 3
    assert img_router.fetch_image.await_count == 3


@pytest.mark.asyncio
async def test_generate_carousel_5_slides():
    engine, _, img_router = make_engine()
    post = await engine.generate_post(topic="Trends", format=PostFormat.CAROUSEL_5)
    assert len(post.slides) == 5
    assert img_router.fetch_image.await_count == 5


@pytest.mark.asyncio
async def test_generate_no_branding_keeps_raw_image():
    engine, _, img_router = make_engine()
    img_router.fetch_image.return_value = b"raw-bytes"
    post = await engine.generate_post(
        topic="test", format=PostFormat.SINGLE, apply_branding=False
    )
    assert post.slides[0].image_bytes == b"raw-bytes"


@pytest.mark.asyncio
async def test_generate_with_branding_transforms_image():
    engine, _, img_router = make_engine()
    post = await engine.generate_post(
        topic="test", format=PostFormat.SINGLE, apply_branding=True
    )
    # Branded image should be JPEG bytes, not the raw mock
    assert post.slides[0].image_bytes != img_router.fetch_image.return_value


@pytest.mark.asyncio
async def test_generate_passes_custom_slide_configs():
    engine, _, img_router = make_engine()
    configs = [
        SlideImageConfig(slide_number=1, image_source=ImageSource.AI_GEN, gen_prompt="space"),
        SlideImageConfig(slide_number=2, image_source=ImageSource.STOCK, search_query="earth"),
    ]
    post = await engine.generate_post(
        topic="Space", format=PostFormat.CAROUSEL_3,
        slide_configs=configs,
        apply_branding=False,
    )
    # Only 2 custom configs provided for carousel_3 → engine takes min(configs, num)
    assert len(post.slides) == 2


@pytest.mark.asyncio
async def test_export_template_returns_zip():
    engine, _, _ = make_engine()
    post = await engine.generate_post(topic="AI", format=PostFormat.SINGLE)
    zip_bytes = await engine.export_template(post)
    import zipfile, io
    assert zipfile.is_zipfile(io.BytesIO(zip_bytes))


@pytest.mark.asyncio
async def test_caption_gen_called_with_correct_params():
    engine, cap_gen, _ = make_engine()
    await engine.generate_post(
        topic="Fitness tips",
        format=PostFormat.CAROUSEL_3,
        text_model="openai/gpt-4o",
        tone="casual",
        niche="Health",
        target_audience="Millennials",
    )
    cap_gen.generate.assert_awaited_once()
    call_kwargs = cap_gen.generate.call_args.kwargs
    assert call_kwargs["topic"] == "Fitness tips"
    assert call_kwargs["num_slides"] == 3
    assert call_kwargs["text_model"] == "openai/gpt-4o"
    assert call_kwargs["tone"] == "casual"
    assert call_kwargs["niche"] == "Health"


@pytest.mark.asyncio
async def test_seo_keywords_and_platform_propagate():
    engine, _, _ = make_engine()
    post = await engine.generate_post(
        topic="AI", format=PostFormat.SINGLE, platform=Platform.LINKEDIN,
    )
    assert post.seo_keywords == FAKE_CAPTION.seo_keywords
    assert post.platform == Platform.LINKEDIN


@pytest.mark.asyncio
async def test_branded_card_routes_to_create_branded_card():
    engine, _, _ = make_engine()
    engine.brand_engine = MagicMock()
    engine.brand_engine.create_branded_card.return_value = b"card"
    await engine.generate_post(
        topic="AI", format=PostFormat.SINGLE,
        template_style=TemplateStyle.BRANDED_CARD,
    )
    engine.brand_engine.create_branded_card.assert_called()


@pytest.mark.asyncio
async def test_square_routes_to_legacy_methods():
    engine, _, _ = make_engine()
    engine.brand_engine = MagicMock()
    engine.brand_engine.apply_brand.return_value = b"square"
    await engine.generate_post(
        topic="AI", format=PostFormat.SINGLE,
        template_style=TemplateStyle.SQUARE,
    )
    engine.brand_engine.apply_brand.assert_called()
    engine.brand_engine.create_branded_card.assert_not_called()


@pytest.mark.asyncio
async def test_progress_callback_invoked():
    engine, _, _ = make_engine()
    messages = []

    async def progress(msg):
        messages.append(msg)

    await engine.generate_post(topic="AI", format=PostFormat.SINGLE, progress=progress)
    assert len(messages) >= 2


def test_num_slides():
    assert _num_slides(PostFormat.SINGLE) == 1
    assert _num_slides(PostFormat.CAROUSEL_3) == 3
    assert _num_slides(PostFormat.CAROUSEL_5) == 5
    assert _num_slides(PostFormat.CAROUSEL_10) == 10
    assert _num_slides(PostFormat.INFOGRAPHIC) == 1


def test_build_default_slide_configs_stock():
    configs = ContentEngine._build_default_slide_configs(
        num=3,
        image_source=ImageSource.STOCK,
        search_queries=["AI", "tech", "future"],
        gen_prompts=[],
        image_model=None,
    )
    assert len(configs) == 3
    assert configs[0].slide_number == 1
    assert configs[0].search_query == "AI"
    assert configs[2].search_query == "future"


def test_build_default_slide_configs_fallback_query():
    configs = ContentEngine._build_default_slide_configs(
        num=3,
        image_source=ImageSource.STOCK,
        search_queries=["only-one"],
        gen_prompts=[],
        image_model=None,
    )
    assert configs[0].search_query == "only-one"
    assert configs[1].search_query == "slide 2"
    assert configs[2].search_query == "slide 3"
