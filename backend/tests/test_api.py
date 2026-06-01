import io
import json
import zipfile
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from PIL import Image
from fastapi.testclient import TestClient

from main import app
from api.deps import get_content_engine
from api.routes import posts as posts_module
from models.schemas import ImageSource, PostFormat
from services.content_engine import GeneratedPost, GeneratedSlide
from services.stock import StockPhotoResult


def make_jpeg(color="teal") -> bytes:
    img = Image.new("RGB", (1080, 1080), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_fake_post(post_id: str = "post-001", num_slides: int = 1) -> GeneratedPost:
    slides = [
        GeneratedSlide(
            slide_number=i,
            image_bytes=make_jpeg(),
            image_source=ImageSource.STOCK,
            search_query="AI trends",
        )
        for i in range(1, num_slides + 1)
    ]
    return GeneratedPost(
        id=post_id,
        topic="AI trends",
        format=PostFormat.SINGLE if num_slides == 1 else PostFormat.CAROUSEL_3,
        caption="Full caption here.",
        hashtags=["#AI", "#Tech"],
        cta="Follow for more!",
        hook="AI is here.",
        alt_text="AI image",
        slides=slides,
        text_model_used="anthropic/claude-sonnet-4",
        image_model_used="openai/dall-e-3",
    )


@pytest.fixture(autouse=True)
def clear_store():
    posts_module._post_store.clear()
    yield
    posts_module._post_store.clear()


@pytest.fixture
def mock_engine():
    engine = AsyncMock()
    engine.generate_post.return_value = make_fake_post()
    engine.export_template.return_value = _make_zip()
    return engine


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("slides/slide_01.jpg", make_jpeg())
        zf.writestr("caption.txt", "caption here")
        zf.writestr("metadata.json", json.dumps({"post_name": "test"}))
    buf.seek(0)
    return buf.getvalue()


@pytest.fixture
def client(mock_engine):
    app.dependency_overrides[get_content_engine] = lambda: mock_engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_text_models(client):
    resp = client.get("/api/models/text")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert all("id" in m and "name" in m and "provider" in m for m in data)


def test_list_image_models(client):
    resp = client.get("/api/models/image")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert any(m["name"] == "dall-e-3" for m in data)


def test_generate_post_success(client, mock_engine):
    resp = client.post("/api/posts/generate", json={
        "topic": "AI trends in 2026",
        "format": "single",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["topic"] == "AI trends"  # from mock
    assert data["caption"] == "Full caption here."
    assert data["status"] == "preview"
    assert len(data["slides"]) == 1
    assert data["slides"][0]["slide_number"] == 1


def test_generate_post_stored(client, mock_engine):
    resp = client.post("/api/posts/generate", json={"topic": "Test topic here", "format": "single"})
    post_id = resp.json()["id"]
    # Should now be retrievable
    resp2 = client.get(f"/api/posts/{post_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == post_id


def test_list_posts_empty(client):
    resp = client.get("/api/posts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_posts_after_generate(client, mock_engine):
    client.post("/api/posts/generate", json={"topic": "Some topic here", "format": "single"})
    resp = client.get("/api/posts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_post_not_found(client):
    resp = client.get("/api/posts/nonexistent-id")
    assert resp.status_code == 404


def test_update_caption(client, mock_engine):
    gen_resp = client.post("/api/posts/generate", json={"topic": "Health tips for everyone", "format": "single"})
    post_id = gen_resp.json()["id"]

    resp = client.put(f"/api/posts/{post_id}/caption", json={
        "caption": "Updated caption text",
        "hashtags": ["#health", "#fitness"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["caption"] == "Updated caption text"
    assert data["hashtags"] == ["#health", "#fitness"]


def test_update_caption_not_found(client):
    resp = client.put("/api/posts/bad-id/caption", json={"caption": "new"})
    assert resp.status_code == 404


def test_export_post_returns_zip(client, mock_engine):
    gen_resp = client.post("/api/posts/generate", json={"topic": "Export topic here", "format": "single"})
    post_id = gen_resp.json()["id"]

    resp = client.post(f"/api/posts/{post_id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(io.BytesIO(resp.content))


def test_export_post_not_found(client):
    resp = client.post("/api/posts/ghost-id/export")
    assert resp.status_code == 404


def test_get_slide_image(client, mock_engine):
    gen_resp = client.post("/api/posts/generate", json={"topic": "Slide image topic", "format": "single"})
    post_id = gen_resp.json()["id"]

    resp = client.get(f"/api/posts/{post_id}/slides/1/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_get_slide_image_not_found(client, mock_engine):
    gen_resp = client.post("/api/posts/generate", json={"topic": "Slide topic test here", "format": "single"})
    post_id = gen_resp.json()["id"]
    resp = client.get(f"/api/posts/{post_id}/slides/99/image")
    assert resp.status_code == 404


def test_generate_post_invalid_topic(client):
    resp = client.post("/api/posts/generate", json={"topic": "AI", "format": "single"})
    assert resp.status_code == 422


def test_stock_search_missing_query(client):
    resp = client.get("/api/stock/search")
    assert resp.status_code == 422


def test_stock_search_short_query(client):
    resp = client.get("/api/stock/search?query=a")
    assert resp.status_code == 422
