import io
import json
import zipfile
import pytest
from services.exporter import TemplateExporter


@pytest.fixture
def exporter():
    return TemplateExporter()


FAKE_IMAGES = [b"img-bytes-1", b"img-bytes-2", b"img-bytes-3"]
CAPTION = "This is the post caption with enough words to be interesting."
HASHTAGS = ["#AI", "#Tech", "#Innovation", "#2026"]
POST_NAME = "test-post"


@pytest.mark.asyncio
async def test_export_returns_bytes(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    assert isinstance(result, bytes)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_export_is_valid_zip(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    assert zipfile.is_zipfile(io.BytesIO(result))


@pytest.mark.asyncio
async def test_export_contains_correct_slide_files(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        names = zf.namelist()
        assert "slides/slide_01.jpg" in names
        assert "slides/slide_02.jpg" in names
        assert "slides/slide_03.jpg" in names


@pytest.mark.asyncio
async def test_export_slide_content_matches(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert zf.read("slides/slide_01.jpg") == b"img-bytes-1"
        assert zf.read("slides/slide_02.jpg") == b"img-bytes-2"
        assert zf.read("slides/slide_03.jpg") == b"img-bytes-3"


@pytest.mark.asyncio
async def test_export_contains_caption_txt(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert "caption.txt" in zf.namelist()
        text = zf.read("caption.txt").decode()
        assert CAPTION in text
        assert "#AI" in text
        assert POST_NAME in text


@pytest.mark.asyncio
async def test_export_contains_metadata_json(exporter):
    result = await exporter.export_package(FAKE_IMAGES, CAPTION, HASHTAGS, POST_NAME)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert "metadata.json" in zf.namelist()
        meta = json.loads(zf.read("metadata.json"))
        assert meta["post_name"] == POST_NAME
        assert meta["caption"] == CAPTION
        assert meta["hashtags"] == HASHTAGS
        assert meta["num_slides"] == 3
        assert meta["aspect_ratio"] == "1:1"
        assert "generated_at" in meta


@pytest.mark.asyncio
async def test_export_single_image(exporter):
    result = await exporter.export_package([b"single"], CAPTION, HASHTAGS, "solo-post")
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert "slides/slide_01.jpg" in zf.namelist()
        assert "slides/slide_02.jpg" not in zf.namelist()
        meta = json.loads(zf.read("metadata.json"))
        assert meta["num_slides"] == 1


@pytest.mark.asyncio
async def test_export_custom_aspect_ratio(exporter):
    result = await exporter.export_package([b"img"], CAPTION, [], "p", aspect_ratio="4:5")
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        meta = json.loads(zf.read("metadata.json"))
        assert meta["aspect_ratio"] == "4:5"
