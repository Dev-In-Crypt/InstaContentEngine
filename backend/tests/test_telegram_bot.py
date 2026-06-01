"""
Tests for the Telegram bot conversation handlers.
We mock all Telegram API objects (Update, Message, CallbackQuery, etc.)
and verify that state transitions and engine calls behave correctly.
"""
import io
import json
import zipfile
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

from models.schemas import ImageSource, PostFormat
from services.content_engine import ContentEngine, GeneratedPost, GeneratedSlide
from bot.telegram_bot import InstaBot, TOPIC, FORMAT, IMAGE_SOURCE, MODEL, OUTPUT_CHOICE


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def make_jpeg() -> bytes:
    img = Image.new("RGB", (100, 100), "blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("slides/slide_01.jpg", make_jpeg())
        zf.writestr("caption.txt", "caption")
        zf.writestr("metadata.json", json.dumps({}))
    buf.seek(0)
    return buf.getvalue()


def make_post(num_slides: int = 1) -> GeneratedPost:
    slides = [
        GeneratedSlide(
            slide_number=i,
            image_bytes=make_jpeg(),
            image_source=ImageSource.STOCK,
        )
        for i in range(1, num_slides + 1)
    ]
    return GeneratedPost(
        id="test-post-id",
        topic="AI trends",
        format=PostFormat.SINGLE if num_slides == 1 else PostFormat.CAROUSEL_3,
        caption="Caption about AI.",
        hashtags=["#AI", "#Tech"],
        cta="Follow!",
        hook="AI is here.",
        alt_text="AI image",
        slides=slides,
        text_model_used="anthropic/claude-sonnet-4",
        image_model_used=None,
    )


def make_update_with_message(text: str) -> MagicMock:
    update = MagicMock(spec=["message", "callback_query"])
    update.callback_query = None
    msg = AsyncMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    msg.reply_photo = AsyncMock()
    msg.reply_document = AsyncMock()
    update.message = msg
    return update


def make_update_with_callback(data: str) -> MagicMock:
    update = MagicMock(spec=["message", "callback_query"])
    query = AsyncMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    # query.message for sending new messages
    query.message = AsyncMock()
    query.message.reply_text = AsyncMock()
    query.message.reply_photo = AsyncMock()
    query.message.reply_document = AsyncMock()
    update.callback_query = query
    update.message = None
    return update


def make_context(user_data: dict | None = None, bot_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot_data = bot_data if bot_data is not None else {}
    return ctx


def make_bot(engine: ContentEngine | None = None) -> InstaBot:
    if engine is None:
        engine = AsyncMock(spec=ContentEngine)
    # Patch Application.builder so no real network call is made
    with patch("bot.telegram_bot.Application") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        bot = InstaBot(token="fake-token", engine=engine)
    return bot


# ------------------------------------------------------------------
# Tests: command handlers
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cmd_start_replies():
    bot = make_bot()
    update = make_update_with_message("anything")
    ctx = make_context()
    await bot.cmd_start(update, ctx)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "create" in text.lower()


@pytest.mark.asyncio
async def test_cmd_create_returns_topic_state():
    bot = make_bot()
    update = make_update_with_message("/create")
    ctx = make_context()
    state = await bot.cmd_create(update, ctx)
    assert state == TOPIC
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_cancel_clears_user_data():
    bot = make_bot()
    update = make_update_with_message("/cancel")
    ctx = make_context(user_data={"topic": "something", "format": "single"})
    from telegram.ext import ConversationHandler
    state = await bot.cmd_cancel(update, ctx)
    assert state == ConversationHandler.END
    assert ctx.user_data == {}


@pytest.mark.asyncio
async def test_cmd_models_lists_models():
    bot = make_bot()
    update = make_update_with_message("/models")
    ctx = make_context()
    await bot.cmd_models(update, ctx)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Claude" in text or "GPT" in text


# ------------------------------------------------------------------
# Tests: conversation state transitions
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receive_topic_stores_and_returns_format_state():
    bot = make_bot()
    update = make_update_with_message("AI trends in 2026")
    ctx = make_context()
    state = await bot.receive_topic(update, ctx)
    assert state == FORMAT
    assert ctx.user_data["topic"] == "AI trends in 2026"
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_receive_format_stores_and_returns_image_source_state():
    bot = make_bot()
    update = make_update_with_callback("carousel_3")
    ctx = make_context()
    state = await bot.receive_format(update, ctx)
    assert state == IMAGE_SOURCE
    assert ctx.user_data["format"] == "carousel_3"
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_receive_image_source_stores_and_returns_model_state():
    bot = make_bot()
    update = make_update_with_callback("stock")
    ctx = make_context(user_data={"topic": "AI", "format": "single"})
    state = await bot.receive_image_source(update, ctx)
    assert state == MODEL
    assert ctx.user_data["image_source"] == "stock"


@pytest.mark.asyncio
async def test_receive_model_calls_engine_and_returns_output_choice():
    engine = AsyncMock(spec=ContentEngine)
    engine.generate_post.return_value = make_post(num_slides=1)
    bot = make_bot(engine=engine)

    update = make_update_with_callback("anthropic/claude-sonnet-4")
    ctx = make_context(user_data={
        "topic": "AI trends",
        "format": "single",
        "image_source": "stock",
    })
    state = await bot.receive_model(update, ctx)
    assert state == OUTPUT_CHOICE
    assert ctx.user_data["text_model"] == "anthropic/claude-sonnet-4"
    engine.generate_post.assert_awaited_once()
    # Should send slide photo + caption text + action keyboard
    assert update.callback_query.message.reply_photo.await_count == 1
    assert update.callback_query.message.reply_text.await_count == 2  # caption + output choice


@pytest.mark.asyncio
async def test_receive_model_carousel_sends_multiple_photos():
    engine = AsyncMock(spec=ContentEngine)
    engine.generate_post.return_value = make_post(num_slides=3)
    bot = make_bot(engine=engine)

    update = make_update_with_callback("openai/gpt-4o")
    ctx = make_context(user_data={
        "topic": "Tips",
        "format": "carousel_3",
        "image_source": "ai_gen",
    })
    await bot.receive_model(update, ctx)
    assert update.callback_query.message.reply_photo.await_count == 3


# ------------------------------------------------------------------
# Tests: output choice handler
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_choice_export_sends_zip():
    engine = AsyncMock(spec=ContentEngine)
    engine.export_template.return_value = make_zip()
    bot = make_bot(engine=engine)

    post = make_post()
    update = make_update_with_callback("export")
    ctx = make_context(user_data={"post": post})

    from telegram.ext import ConversationHandler
    state = await bot.handle_output_choice(update, ctx)
    assert state == ConversationHandler.END
    engine.export_template.assert_awaited_once_with(post)
    update.callback_query.message.reply_document.assert_awaited_once()
    call_kwargs = update.callback_query.message.reply_document.call_args.kwargs
    assert call_kwargs["filename"].endswith(".zip")


@pytest.mark.asyncio
async def test_output_choice_regenerate_calls_receive_model():
    engine = AsyncMock(spec=ContentEngine)
    engine.generate_post.return_value = make_post()
    bot = make_bot(engine=engine)

    post = make_post()
    update = make_update_with_callback("regenerate")
    ctx = make_context(user_data={
        "post": post,
        "topic": "AI",
        "format": "single",
        "image_source": "stock",
        "text_model": "anthropic/claude-sonnet-4",
    })
    state = await bot.handle_output_choice(update, ctx)
    assert state == OUTPUT_CHOICE
    engine.generate_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_output_choice_publish_no_publisher_configured():
    engine = AsyncMock(spec=ContentEngine)
    bot = make_bot(engine=engine)

    post = make_post()
    update = make_update_with_callback("publish")
    ctx = make_context(user_data={"post": post}, bot_data={})  # No publisher

    from telegram.ext import ConversationHandler
    state = await bot.handle_output_choice(update, ctx)
    assert state == ConversationHandler.END
    update.callback_query.message.reply_text.assert_awaited()
    error_msg = update.callback_query.message.reply_text.call_args[0][0]
    assert "not configured" in error_msg.lower()


@pytest.mark.asyncio
async def test_output_choice_publish_success_single():
    engine = AsyncMock(spec=ContentEngine)
    publisher = AsyncMock()
    publisher.publish_single.return_value = "media-id-123"
    bot = make_bot(engine=engine)

    post = make_post(num_slides=1)
    update = make_update_with_callback("publish")
    ctx = make_context(
        user_data={"post": post},
        bot_data={"instagram_publisher": publisher, "cdn_base_url": "https://cdn.example.com"},
    )
    from telegram.ext import ConversationHandler
    state = await bot.handle_output_choice(update, ctx)
    assert state == ConversationHandler.END
    publisher.publish_single.assert_awaited_once()
    success_text = update.callback_query.message.reply_text.call_args[0][0]
    assert "media-id-123" in success_text
