"""Post CRUD, export, slide image, and the /generate SSE stream.

Restores coverage deleted in 8c917a2, which justified dropping these on the
grounds that test_publishing_api.py / test_slide_replace.py already covered
them. They did not: until this file, no test called any PUT endpoint (there are
four), the /generate happy path, /export, or the slide-image route.

Fixture shape follows test_publishing_api.py.
"""
import io
import json
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_content_engine, get_db, get_settings
from config import Settings
from main import app
from models.database import Base, Post as PostModel, Slide as SlideModel
from models.schemas import ImageSource, PostFormat
from services.content_engine import ContentEngine, GeneratedPost, GeneratedSlide
from services.openrouter import OpenRouterError

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads" / "posts"

TEXT_MODEL = "test/text-model"
IMAGE_MODEL = "test/image-model"


def _jpeg(color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color).save(buf, format="JPEG")
    return buf.getvalue()


def _settings(db_url: str) -> Settings:
    # Every field the assertions depend on is explicit: Settings() otherwise reads
    # the developer's real backend/.env, so an API_TOKEN there would 401 every
    # request here, and the model-fallback tests would assert their .env values.
    return Settings(
        database_url=db_url,
        api_token="",
        default_text_model=TEXT_MODEL,
        default_image_model=IMAGE_MODEL,
    )


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'crud.db'}"


@pytest.fixture
def seeded(db_url):
    """A post with one slide whose JPEG exists on disk."""
    post_id = str(uuid.uuid4())

    async def _setup():
        eng = create_async_engine(db_url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SM = async_sessionmaker(eng, expire_on_commit=False)
        async with SM() as s:
            s.add(PostModel(
                id=post_id, topic="Running tips", format="single", status="preview",
                caption="Run every day.", hashtags=["#run"], seo_keywords=["running"],
                cta="Follow!", hook="Run daily.", platform="instagram",
                template_style="branded_card",
            ))
            path = UPLOADS_DIR / post_id / "slide_1.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_jpeg())
            s.add(SlideModel(post_id=post_id, slide_number=1, image_source="stock",
                             image_path=str(path), search_query="running"))
            await s.commit()
        await eng.dispose()

    import asyncio
    asyncio.run(_setup())
    yield post_id
    _cleanup_post_dir(post_id)


def _cleanup_post_dir(post_id: str) -> None:
    d = UPLOADS_DIR / post_id
    if not d.exists():
        return
    for f in d.iterdir():
        f.unlink()
    try:
        d.rmdir()
    except OSError:
        pass


@pytest.fixture
def client(db_url):
    import asyncio

    eng = create_async_engine(db_url)

    async def _ensure():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_ensure())
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    fake_engine = AsyncMock(spec=ContentEngine)
    # spec=ContentEngine blocks *reading* instance attributes, so the export and
    # regenerate routes would raise AttributeError. Assigning them is allowed.
    fake_engine.exporter = AsyncMock()
    fake_engine.image_router = AsyncMock()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_content_engine] = lambda: fake_engine
    app.dependency_overrides[get_settings] = lambda: _settings(db_url)
    app.state.sessionmaker = SM

    tc = TestClient(app)
    tc.fake_engine = fake_engine
    yield tc

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_content_engine, None)
    app.dependency_overrides.pop(get_settings, None)
    asyncio.run(eng.dispose())


def _sse_events(resp) -> list[dict]:
    """TestClient buffers the stream, so the whole body is in .text."""
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


# ── GET /{post_id} ──────────────────────────────────────────────────────────

def test_get_post_returns_preview(client, seeded):
    res = client.get(f"/api/posts/{seeded}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == seeded
    assert body["caption"] == "Run every day."
    assert body["hashtags"] == ["#run"]
    assert body["slides"][0]["image_url"] == f"/api/posts/{seeded}/slides/1/image"


def test_get_post_unknown_returns_404(client):
    assert client.get(f"/api/posts/{uuid.uuid4()}").status_code == 404


# ── PUT /{post_id}/caption ──────────────────────────────────────────────────

def test_update_caption_persists(client, seeded):
    res = client.put(f"/api/posts/{seeded}/caption", json={
        "caption": "Updated caption text",
        "hashtags": ["#health", "#fitness"],
    })
    assert res.status_code == 200, res.text
    assert res.json()["caption"] == "Updated caption text"

    # Re-read: proves it was committed, not just echoed back.
    again = client.get(f"/api/posts/{seeded}")
    assert again.json()["caption"] == "Updated caption text"
    assert again.json()["hashtags"] == ["#health", "#fitness"]


def test_update_caption_partial_leaves_other_fields_intact(client, seeded):
    """Pins the four `is not None` guards: omitted fields must not be cleared."""
    res = client.put(f"/api/posts/{seeded}/caption", json={"cta": "New CTA"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cta"] == "New CTA"
    assert body["caption"] == "Run every day."      # untouched
    assert body["hashtags"] == ["#run"]
    assert body["seo_keywords"] == ["running"]


def test_update_caption_unknown_returns_404(client):
    res = client.put(f"/api/posts/{uuid.uuid4()}/caption", json={"caption": "x"})
    assert res.status_code == 404


# ── POST /{post_id}/export ──────────────────────────────────────────────────

def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("caption.txt", "caption")
    return buf.getvalue()


def test_export_returns_zip(client, seeded):
    client.fake_engine.exporter.export_package.return_value = _zip_bytes()

    res = client.post(f"/api/posts/{seeded}/export")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "Running_tips_template.zip" in res.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(res.content))

    # The route's actual job is reading slide bytes off disk and handing them over.
    kwargs = client.fake_engine.exporter.export_package.await_args.kwargs
    assert kwargs["images"] == [_jpeg()]
    assert kwargs["caption"] == "Run every day."
    assert kwargs["hashtags"] == ["#run"]


def test_export_missing_image_file_returns_404(client, seeded):
    (UPLOADS_DIR / seeded / "slide_1.jpg").unlink()
    res = client.post(f"/api/posts/{seeded}/export")
    assert res.status_code == 404
    assert "slide 1" in res.json()["detail"]


def test_export_unknown_post_returns_404(client):
    assert client.post(f"/api/posts/{uuid.uuid4()}/export").status_code == 404


# ── GET /{post_id}/slides/{n}/image ─────────────────────────────────────────

def test_get_slide_image_returns_file_bytes(client, seeded):
    res = client.get(f"/api/posts/{seeded}/slides/1/image")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == (UPLOADS_DIR / seeded / "slide_1.jpg").read_bytes()


def test_get_slide_image_unknown_slide_returns_404(client, seeded):
    res = client.get(f"/api/posts/{seeded}/slides/99/image")
    assert res.status_code == 404
    assert res.json()["detail"] == "Slide not found"


def test_get_slide_image_missing_file_returns_404(client, seeded):
    (UPLOADS_DIR / seeded / "slide_1.jpg").unlink()
    res = client.get(f"/api/posts/{seeded}/slides/1/image")
    assert res.status_code == 404
    assert res.json()["detail"] == "Image file not found on disk"


# ── POST /generate (SSE) ────────────────────────────────────────────────────

def _generated(post_id: str) -> GeneratedPost:
    return GeneratedPost(
        id=post_id,
        topic="AI trends",
        format=PostFormat.SINGLE,
        caption="Full caption here.",
        hashtags=["#AI"],
        cta="Follow!",
        hook="AI is here.",
        alt_text="AI image",
        slides=[GeneratedSlide(
            slide_number=1,
            image_bytes=_jpeg("blue"),
            image_source=ImageSource.STOCK,
            search_query="ai",
        )],
        text_model_used=TEXT_MODEL,
        image_model_used=IMAGE_MODEL,
        seo_keywords=["ai"],
    )


@pytest.fixture
def generated_ids():
    """_persist writes real files under uploads/posts/<id>."""
    ids = []
    yield ids
    for pid in ids:
        _cleanup_post_dir(pid)


def test_generate_streams_progress_then_complete(client, generated_ids):
    post_id = str(uuid.uuid4())
    generated_ids.append(post_id)

    async def fake_generate(**kwargs):
        await kwargs["progress"]("Writing caption...")
        return _generated(post_id)

    client.fake_engine.generate_post.side_effect = fake_generate

    res = client.post("/api/posts/generate", json={"topic": "AI trends", "format": "single"})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(res)
    assert events[0] == {"type": "progress", "message": "Writing caption..."}
    assert {"type": "progress", "message": "Saving to database..."} in events
    assert events[-1]["type"] == "complete"
    post = events[-1]["post"]
    assert post["id"] == post_id
    assert post["caption"] == "Full caption here."
    assert post["slides"][0]["image_url"] == f"/api/posts/{post_id}/slides/1/image"

    # Persisted, not just streamed.
    assert client.get(f"/api/posts/{post_id}").status_code == 200


def test_generate_streams_error_event_and_still_returns_200(client):
    """The stream carries a failure; the HTTP status stays 200. The message is
    generic — internal error text (which can include upstream API responses) is
    logged server-side, not leaked to the client."""
    client.fake_engine.generate_post.side_effect = OpenRouterError("boom-secret-detail")

    res = client.post("/api/posts/generate", json={"topic": "AI trends", "format": "single"})
    assert res.status_code == 200

    events = _sse_events(res)
    assert events[-1]["type"] == "error"
    assert "boom-secret-detail" not in events[-1]["message"]   # internals masked
    assert events[-1]["message"] == "Generation failed. Please try again."


# ── model fallback ──────────────────────────────────────────────────────────

def test_generate_falls_back_to_configured_default_models(client, generated_ids):
    post_id = str(uuid.uuid4())
    generated_ids.append(post_id)
    client.fake_engine.generate_post.return_value = _generated(post_id)

    res = client.post("/api/posts/generate", json={"topic": "AI trends", "format": "single"})
    assert res.status_code == 200

    kwargs = client.fake_engine.generate_post.await_args.kwargs
    assert kwargs["text_model"] == TEXT_MODEL
    assert kwargs["image_model"] == IMAGE_MODEL
    # the route resolves the acting user's brand voice and forwards it (default preset here)
    assert "brand_voice" in kwargs and kwargs["brand_voice"]


def test_generate_request_models_override_defaults(client, generated_ids):
    post_id = str(uuid.uuid4())
    generated_ids.append(post_id)
    client.fake_engine.generate_post.return_value = _generated(post_id)

    res = client.post("/api/posts/generate", json={
        "topic": "AI trends", "format": "single",
        "text_model": "req/text", "image_model": "req/image",
    })
    assert res.status_code == 200

    kwargs = client.fake_engine.generate_post.await_args.kwargs
    assert kwargs["text_model"] == "req/text"
    assert kwargs["image_model"] == "req/image"


def test_regenerate_slide_falls_back_to_default_image_model(client, seeded):
    """Without this fallback the router raises 'No image model configured'."""
    client.fake_engine.image_router.fetch_image.return_value = (_jpeg("green"), None)

    res = client.post(f"/api/posts/{seeded}/slides/1/regenerate",
                      json={"image_source": "ai_gen", "gen_prompt": "a runner"})
    assert res.status_code == 200, res.text

    cfg = client.fake_engine.image_router.fetch_image.await_args.args[0]
    assert cfg.gen_model == IMAGE_MODEL
