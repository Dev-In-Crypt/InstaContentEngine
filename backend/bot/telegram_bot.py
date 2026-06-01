from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from models.schemas import ImageSource, PostFormat
from services.content_engine import ContentEngine, GeneratedPost

# Conversation states
TOPIC, FORMAT, IMAGE_SOURCE, MODEL, OUTPUT_CHOICE = range(5)

TEXT_MODEL_OPTIONS = [
    ("Claude Sonnet", "anthropic/claude-sonnet-4"),
    ("GPT-4o", "openai/gpt-4o"),
    ("Gemini Flash", "google/gemini-2.5-flash"),
    ("Llama 70B (cheap)", "meta-llama/llama-3.3-70b-instruct"),
]


class InstaBot:
    def __init__(self, token: str, engine: ContentEngine):
        self.engine = engine
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        conv = ConversationHandler(
            entry_points=[CommandHandler("create", self.cmd_create)],
            states={
                TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_topic)],
                FORMAT: [CallbackQueryHandler(self.receive_format)],
                IMAGE_SOURCE: [CallbackQueryHandler(self.receive_image_source)],
                MODEL: [CallbackQueryHandler(self.receive_model)],
                OUTPUT_CHOICE: [CallbackQueryHandler(self.handle_output_choice)],
            },
            fallbacks=[CommandHandler("cancel", self.cmd_cancel)],
        )
        self.app.add_handler(conv)
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("models", self.cmd_models))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def cmd_start(self, update: Update, context) -> None:
        await update.message.reply_text(
            "👋 Welcome to InstaContentEngine!\n\n"
            "Use /create to generate an Instagram post.\n"
            "Use /models to see available AI models.\n"
            "Use /cancel to stop at any time."
        )

    async def cmd_create(self, update: Update, context) -> int:
        await update.message.reply_text("What topic should the post be about?")
        return TOPIC

    async def cmd_cancel(self, update: Update, context) -> int:
        await update.message.reply_text("❌ Cancelled. Use /create to start again.")
        context.user_data.clear()
        return ConversationHandler.END

    async def cmd_models(self, update: Update, context) -> None:
        lines = [f"• {name}: `{model_id}`" for name, model_id in TEXT_MODEL_OPTIONS]
        await update.message.reply_text(
            "Available text models:\n" + "\n".join(lines),
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------
    # Conversation steps
    # ------------------------------------------------------------------

    async def receive_topic(self, update: Update, context) -> int:
        context.user_data["topic"] = update.message.text
        keyboard = [
            [InlineKeyboardButton("🖼 Single image", callback_data="single")],
            [InlineKeyboardButton("🎠 Carousel (3 slides)", callback_data="carousel_3")],
            [InlineKeyboardButton("🎠 Carousel (5 slides)", callback_data="carousel_5")],
            [InlineKeyboardButton("🎠 Carousel (10 slides)", callback_data="carousel_10")],
            [InlineKeyboardButton("📊 Infographic", callback_data="infographic")],
        ]
        await update.message.reply_text(
            "Choose the post format:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return FORMAT

    async def receive_format(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["format"] = query.data

        keyboard = [
            [InlineKeyboardButton("📷 Stock photos (Unsplash/Pexels)", callback_data="stock")],
            [InlineKeyboardButton("🎨 AI generated (DALL-E/Flux)", callback_data="ai_gen")],
            [InlineKeyboardButton("🎯 Canva template", callback_data="canva")],
        ]
        await query.edit_message_text(
            "Where should I get the images from?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return IMAGE_SOURCE

    async def receive_image_source(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["image_source"] = query.data

        keyboard = [
            [InlineKeyboardButton(name, callback_data=model_id)]
            for name, model_id in TEXT_MODEL_OPTIONS
        ]
        await query.edit_message_text(
            "Which text model should I use?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return MODEL

    async def receive_model(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["text_model"] = query.data

        await query.edit_message_text("⏳ Generating your post… this may take a moment.")

        post = await self._generate_from_context(context)
        context.user_data["post"] = post

        # Send slide previews
        for slide in post.slides:
            caption = f"Slide {slide.slide_number}/{len(post.slides)}" if len(post.slides) > 1 else None
            await query.message.reply_photo(photo=slide.image_bytes, caption=caption)

        caption_text = f"*Caption:*\n{post.caption}\n\n{' '.join(post.hashtags)}"
        await query.message.reply_text(caption_text, parse_mode="Markdown")

        keyboard = [
            [InlineKeyboardButton("📤 Publish to Instagram", callback_data="publish")],
            [InlineKeyboardButton("📦 Export as ZIP", callback_data="export")],
            [InlineKeyboardButton("🔄 Regenerate", callback_data="regenerate")],
        ]
        await query.message.reply_text(
            "What would you like to do?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return OUTPUT_CHOICE

    async def handle_output_choice(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        post: GeneratedPost = context.user_data["post"]

        match query.data:
            case "publish":
                await query.edit_message_text("📤 Publishing to Instagram…")
                try:
                    # Publishing handled by a separate publisher wired in at startup
                    publisher = context.bot_data.get("instagram_publisher")
                    if publisher is None:
                        await query.message.reply_text("❌ Instagram publisher not configured.")
                        return ConversationHandler.END
                    # Upload images first (public URL required) — simplified: use first slide only
                    if len(post.slides) == 1:
                        img_url = context.bot_data.get("cdn_base_url", "") + f"/{post.id}/slide_1.jpg"
                        media_id = await publisher.publish_single(img_url, post.caption, alt_text=post.alt_text)
                    else:
                        img_urls = [
                            context.bot_data.get("cdn_base_url", "") + f"/{post.id}/slide_{s.slide_number}.jpg"
                            for s in post.slides
                        ]
                        media_id = await publisher.publish_carousel(img_urls, post.caption)
                    await query.message.reply_text(f"✅ Published! Media ID: `{media_id}`", parse_mode="Markdown")
                except Exception as e:
                    await query.message.reply_text(f"❌ Publishing failed: {e}")
                return ConversationHandler.END

            case "export":
                await query.edit_message_text("📦 Packing your template…")
                zip_bytes = await self.engine.export_template(post)
                filename = f"{post.topic[:40].replace(' ', '_')}_template.zip"
                await query.message.reply_document(
                    document=zip_bytes,
                    filename=filename,
                    caption="✅ Here is your template package!",
                )
                return ConversationHandler.END

            case "regenerate":
                await query.edit_message_text("🔄 Regenerating…")
                return await self.receive_model(update, context)

            case _:
                await query.edit_message_text("Unknown action.")
                return ConversationHandler.END

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _generate_from_context(self, context) -> GeneratedPost:
        ud = context.user_data
        fmt_str = ud.get("format", "single")
        fmt = PostFormat(fmt_str)
        img_src = ImageSource(ud.get("image_source", "stock"))
        return await self.engine.generate_post(
            topic=ud["topic"],
            format=fmt,
            text_model=ud.get("text_model", "anthropic/claude-sonnet-4"),
            default_image_source=img_src,
        )

    def run(self) -> None:
        self.app.run_polling()
