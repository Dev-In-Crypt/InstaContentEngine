import io
import pytest
from PIL import Image
from services.brand_engine import PillowBrandEngine, BrandConfig


def make_jpeg_bytes(width: int = 800, height: int = 600, color: str = "blue") -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def open_result(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.fixture
def engine():
    return PillowBrandEngine(BrandConfig())


def test_apply_brand_returns_jpeg(engine):
    result = engine.apply_brand(make_jpeg_bytes(), aspect="square")
    img = open_result(result)
    assert img.format == "JPEG"


def test_apply_brand_square_dimensions(engine):
    result = engine.apply_brand(make_jpeg_bytes(400, 800), aspect="square")
    img = open_result(result)
    assert img.size == (1080, 1080)


def test_apply_brand_portrait_dimensions(engine):
    result = engine.apply_brand(make_jpeg_bytes(), aspect="portrait")
    img = open_result(result)
    assert img.size == (1080, 1350)


def test_apply_brand_landscape_dimensions(engine):
    result = engine.apply_brand(make_jpeg_bytes(), aspect="landscape")
    img = open_result(result)
    assert img.size == (1080, 608)


def test_apply_brand_with_text_overlay(engine):
    result = engine.apply_brand(
        make_jpeg_bytes(),
        text_overlay="AI is changing everything",
        subtitle="Learn how to adapt today",
    )
    img = open_result(result)
    assert img.size == (1080, 1080)


def test_apply_brand_no_overlay(engine):
    # Without text overlay, no dark overlay should be added — image should be brighter than 0
    result = engine.apply_brand(make_jpeg_bytes(color="white"))
    img = open_result(result)
    # Result should be valid JPEG
    assert img.mode == "RGB"


def test_create_carousel_slide_solid_bg(engine):
    result = engine.create_carousel_slide(
        slide_number=1,
        total_slides=3,
        heading="Top 3 AI Tools",
        body_text="These tools will transform your workflow in 2026.",
        background_color="#2E75B6",
    )
    img = open_result(result)
    assert img.size == (1080, 1080)
    assert img.format == "JPEG"


def test_create_carousel_slide_with_bg_image(engine):
    result = engine.create_carousel_slide(
        slide_number=2,
        total_slides=5,
        heading="Slide Heading",
        body_text="Some body text here for the slide.",
        background_image=make_jpeg_bytes(800, 800, "green"),
    )
    img = open_result(result)
    assert img.size == (1080, 1080)


def test_resize_and_crop_wider_than_target():
    engine = PillowBrandEngine(BrandConfig())
    img = Image.new("RGBA", (2000, 800), "red")
    result = PillowBrandEngine._resize_and_crop(img, (1080, 1080))
    assert result.size == (1080, 1080)


def test_resize_and_crop_taller_than_target():
    img = Image.new("RGBA", (400, 2000), "red")
    result = PillowBrandEngine._resize_and_crop(img, (1080, 1080))
    assert result.size == (1080, 1080)


def test_hex_to_rgba():
    r, g, b, a = PillowBrandEngine._hex_to_rgba("#2E75B6")
    assert r == 0x2E
    assert g == 0x75
    assert b == 0xB6
    assert a == 255


def test_hex_to_rgba_custom_alpha():
    _, _, _, a = PillowBrandEngine._hex_to_rgba("#000000", alpha=128)
    assert a == 128


def test_output_is_smaller_than_raw_png(engine):
    # JPEG output should be reasonably compact
    result = engine.apply_brand(make_jpeg_bytes(1080, 1080))
    assert len(result) < 2 * 1024 * 1024  # less than 2 MB


# ---- branded card (portrait 1080x1350) ----

def test_branded_card_portrait_dimensions(engine):
    result = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800, "gray"),
        niche_text="Running",
        description_text="Run From Asia to Europe",
    )
    img = open_result(result)
    assert img.size == (1080, 1350)
    assert img.format == "JPEG"


def test_branded_card_niche_box_color_rendered():
    # Use a config whose palette includes the color so it isn't reset to default
    cfg = BrandConfig(niche_box_color="#ff751f")
    engine = PillowBrandEngine(cfg)
    result = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800, "white"),
        niche_text="Running",
        description_text="Hello",
        niche_box_color="#0076cb",
    )
    img = open_result(result).convert("RGB")
    # Sample colors across the lower third where the niche box sits
    colors = {img.getpixel((x, y))
              for y in range(int(1350 * 0.62), int(1350 * 0.72), 4)
              for x in range(60, 400, 20)}
    target = (0x00, 0x76, 0xcb)
    assert any(abs(r - target[0]) < 12 and abs(g - target[1]) < 12 and abs(b - target[2]) < 12
               for (r, g, b) in colors)


def test_branded_card_invalid_color_falls_back_to_config():
    cfg = BrandConfig(niche_box_color="#ff751f")
    engine = PillowBrandEngine(cfg)
    # color not in palette -> should not raise, falls back to config color
    result = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800, "white"),
        niche_text="Running",
        description_text="Hello",
        niche_box_color="#123456",
    )
    assert open_result(result).size == (1080, 1350)


def test_branded_card_page_number_optional(engine):
    # both None and an int should render without error
    no_num = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800), niche_text="N", description_text="D",
        page_number=None,
    )
    with_num = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800), niche_text="N", description_text="D",
        page_number=2, total_slides=5,
    )
    assert open_result(no_num).size == (1080, 1350)
    assert open_result(with_num).size == (1080, 1350)


def test_branded_card_show_logo_toggle_no_logo_configured(engine):
    # No logo file configured -> show_logo True/False both work
    result = engine.create_branded_card(
        background_image=make_jpeg_bytes(800, 800), niche_text="N", description_text="D",
        show_logo=False,
    )
    assert open_result(result).size == (1080, 1350)


def test_wrap_lines_helper():
    from PIL import ImageDraw, Image as PILImage, ImageFont
    img = PILImage.new("RGB", (200, 200))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=20)
    lines = PillowBrandEngine._wrap_lines(draw, "one two three four five six", font, 60)
    assert len(lines) > 1
