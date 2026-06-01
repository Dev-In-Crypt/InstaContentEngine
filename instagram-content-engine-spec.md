# Instagram Content Engine — Technical Specification

> **Purpose**: This document is a complete implementation guide for an AI-powered Instagram content generation and publishing system. It should contain everything an AI coder needs to build the project from scratch.

---

## 1. Project Overview

### What It Does
A system that takes a **topic** as input and produces a **ready-to-publish Instagram post** — including branded images (single or carousel) and a caption with hashtags. The user can then either **publish directly** to Instagram via API, or **export as a template package** (images + text file) for manual posting.

### Key Principles
- **Variability in image sourcing**: User picks per-slide whether to use stock photos (Unsplash/Pexels), AI-generated images (via OpenRouter), or Canva templates (via Connect API)
- **Model flexibility**: All LLM and image generation calls go through **OpenRouter** — user selects which model to use at generation time
- **Dual branding path**: Simple overlay via Pillow (logo + text on photo), or full template rendering via Canva Design Editing API
- **Dual interface**: Telegram bot for quick generation, Web GUI for full editing
- **Dual output**: Publish to Instagram Graph API, or download as template package

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    USER INPUT                        │
│  Topic, format, model choice, image source per slide │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              OPENROUTER API GATEWAY                  │
│  Single API key — model selector                     │
│                                                      │
│  Text models:        Image models:                   │
│  ┌─────────┐         ┌─────────┐                     │
│  │ Claude  │         │ DALL-E  │                     │
│  │ GPT-4o  │         │ Flux    │                     │
│  │ Gemini  │         │ SDXL    │                     │
│  │ Llama   │         │ ...     │                     │
│  └─────────┘         └─────────┘                     │
└──────┬──────────────────────┬───────────────────────┘
       │                      │
       ▼                      ▼
┌──────────────┐    ┌──────────────────────────────────┐
│ CAPTION GEN  │    │        IMAGE ROUTER              │
│ Text+hashtags│    │  ┌────────┐ ┌───────┐ ┌───────┐  │
│ + CTA        │    │  │AI Gen  │ │ Stock │ │ Canva │  │
└──────┬───────┘    │  │DALL-E  │ │Unspla.│ │Connect│  │
       │            │  │Flux    │ │Pexels │ │API    │  │
       │            │  └────────┘ └───────┘ └───────┘  │
       │            └──────────────┬───────────────────┘
       │                           │
       ▼                           ▼
┌─────────────────────────────────────────────────────┐
│                   BRAND ENGINE                       │
│  Path A: Pillow (logo overlay, text, colors, fonts)  │
│  Path B: Canva (template + Design Editing API)       │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                 PREVIEW + EDIT                       │
│  Swap images, change source, edit text, reorder      │
└──────────────────────┬──────────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
     ┌────────────────┐  ┌────────────────┐
     │  TELEGRAM BOT  │  │   WEB GUI      │
     │  Quick gen      │  │  Full editor   │
     └────────┬───────┘  └───────┬────────┘
              │                  │
              └────────┬─────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                    OUTPUT                             │
│                                                      │
│  Option A: PUBLISH → Instagram Graph API             │
│    - Single image post                               │
│    - Carousel post (up to 10 slides)                 │
│    - Scheduled publishing                            │
│                                                      │
│  Option B: EXPORT TEMPLATE PACKAGE                   │
│    - Images as PNG/JPG files (1080x1080 or 1080x1350)│
│    - Caption text file with hashtags                 │
│    - ZIP archive for download                        │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## 3. Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Backend | **Python 3.11+ / FastAPI** | Async, fast, OpenAPI docs auto-generated |
| Telegram Bot | **python-telegram-bot** (v20+) | Async, well-maintained, conversation handlers |
| Web GUI | **React + Tailwind CSS** | Fast dev, responsive, component-based |
| Image Processing | **Pillow (PIL)** | Logo overlay, text rendering, image compositing |
| Database | **PostgreSQL + SQLAlchemy** | Posts history, templates, scheduled posts |
| Task Queue | **Celery + Redis** | Async image generation, scheduled publishing |
| File Storage | **Local filesystem or S3** | Generated images, templates, exports |
| AI Gateway | **OpenRouter API** | Single key for all LLM + image models |
| Stock Photos | **Unsplash API + Pexels API** | Free, high-quality, direct REST |
| Design | **Canva Connect API** | Templates, brand assets, design editing |
| Publishing | **Instagram Graph API (Meta)** | Official, supports carousels + scheduling |

---

## 4. Module Specifications

### 4.1 OpenRouter Integration

**Base URL**: `https://openrouter.ai/api/v1`

**Authentication**: Bearer token via `OPENROUTER_API_KEY` env var.

All requests use the OpenAI-compatible format. The only thing that changes is the `model` field.

#### Text Generation

```python
# config/models.py

TEXT_MODELS = {
    "claude-sonnet": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-haiku-4",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gemini-pro": "google/gemini-2.5-pro",
    "gemini-flash": "google/gemini-2.5-flash",
    "llama-70b": "meta-llama/llama-3.3-70b-instruct",
}

IMAGE_MODELS = {
    "dall-e-3": "openai/dall-e-3",
    "flux-pro": "black-forest-labs/flux-1.1-pro",
    "sdxl": "stabilityai/stable-diffusion-xl",
}
```

```python
# services/openrouter.py

import httpx

class OpenRouterClient:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://your-app-domain.com",
                "X-Title": "InstaContentEngine",
            },
            timeout=120.0,
        )

    async def generate_text(self, model: str, system_prompt: str, user_prompt: str) -> str:
        response = await self.client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 2000,
                "temperature": 0.7,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def generate_image(self, model: str, prompt: str, size: str = "1024x1024") -> str:
        """Returns URL of generated image."""
        response = await self.client.post(
            "/images/generations",  # OpenRouter image endpoint
            json={
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
            },
        )
        response.raise_for_status()
        return response.json()["data"][0]["url"]
```

> **IMPORTANT**: Check OpenRouter docs for the latest image generation endpoint format. Some models may use `/chat/completions` with vision, others use `/images/generations`. The implementation should handle both patterns.

---

### 4.2 Caption Generator

The caption generator uses the selected text model via OpenRouter to produce:
- Instagram caption (English)
- 5–15 relevant hashtags
- Optional CTA (call-to-action)
- Image search queries (for stock photo sourcing)
- Image generation prompts (for AI image sourcing)

#### System Prompt Template

```python
CAPTION_SYSTEM_PROMPT = """
You are an Instagram content strategist for a brand.

BRAND VOICE:
{brand_voice_description}

RULES:
- Write captions in English
- Keep captions between 100-300 words
- Include a hook in the first line (this shows in preview)
- Add a clear CTA at the end
- Generate 5-15 relevant hashtags (mix of popular and niche)
- Tone: {tone}  (professional / casual / educational / inspirational)

RESPOND IN THIS EXACT JSON FORMAT:
{{
    "caption": "The full caption text...",
    "hashtags": ["#hashtag1", "#hashtag2", ...],
    "cta": "Follow for more tips!",
    "hook": "The first line of the caption",
    "image_search_queries": ["query for stock photo 1", "query 2"],
    "image_gen_prompts": ["DALL-E prompt for slide 1", "prompt for slide 2"],
    "alt_text": "Accessibility description of the post"
}}
"""
```

#### User Prompt Template

```python
CAPTION_USER_PROMPT = """
Create an Instagram post about: {topic}

Format: {format}  (single_image | carousel_{n}_slides | infographic)
Number of slides: {num_slides}
Industry/Niche: {niche}
Target audience: {target_audience}

Additional instructions: {additional_instructions}
"""
```

---

### 4.3 Image Router

The image router is the central module that fetches images from the selected source for each slide.

```python
# services/image_router.py

from enum import Enum

class ImageSource(str, Enum):
    STOCK = "stock"        # Unsplash / Pexels
    AI_GEN = "ai_gen"      # DALL-E / Flux / SDXL via OpenRouter
    CANVA = "canva"        # Canva template export

class SlideConfig:
    """Configuration for a single slide in a post."""
    slide_number: int
    image_source: ImageSource
    # For stock:
    search_query: str | None = None
    # For AI gen:
    gen_prompt: str | None = None
    gen_model: str | None = None
    # For Canva:
    canva_template_id: str | None = None
    canva_data: dict | None = None  # text/image overrides for the template

class ImageRouter:
    def __init__(self, openrouter: OpenRouterClient, stock_client: StockClient, canva_client: CanvaClient):
        self.openrouter = openrouter
        self.stock = stock_client
        self.canva = canva_client

    async def fetch_image(self, config: SlideConfig) -> bytes:
        """Fetch image bytes based on the configured source."""
        match config.image_source:
            case ImageSource.STOCK:
                return await self.stock.search_and_download(
                    query=config.search_query,
                    orientation="squarish",  # Instagram-friendly
                    size="regular",
                )
            case ImageSource.AI_GEN:
                url = await self.openrouter.generate_image(
                    model=config.gen_model,
                    prompt=config.gen_prompt,
                    size="1024x1024",
                )
                return await self._download_image(url)
            case ImageSource.CANVA:
                return await self.canva.export_design(
                    template_id=config.canva_template_id,
                    overrides=config.canva_data,
                )
```

---

### 4.4 Stock Photo Integration

#### Unsplash API

```python
# services/stock.py

class UnsplashClient:
    BASE_URL = "https://api.unsplash.com"

    def __init__(self, access_key: str):
        self.access_key = access_key
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Client-ID {access_key}"},
        )

    async def search_photos(
        self, query: str, per_page: int = 5, orientation: str = "squarish"
    ) -> list[dict]:
        response = await self.client.get(
            "/search/photos",
            params={
                "query": query,
                "per_page": per_page,
                "orientation": orientation,
            },
        )
        response.raise_for_status()
        return response.json()["results"]

    async def download_photo(self, photo_id: str, size: str = "regular") -> bytes:
        # First trigger download endpoint (required by Unsplash API guidelines)
        photos = await self.client.get(f"/photos/{photo_id}")
        download_url = photos.json()["urls"][size]

        async with httpx.AsyncClient() as dl_client:
            response = await dl_client.get(download_url)
            return response.content
```

#### Pexels API

```python
class PexelsClient:
    BASE_URL = "https://api.pexels.com/v1"

    def __init__(self, api_key: str):
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": api_key},
        )

    async def search_photos(self, query: str, per_page: int = 5) -> list[dict]:
        response = await self.client.get(
            "/search",
            params={"query": query, "per_page": per_page, "size": "medium"},
        )
        response.raise_for_status()
        return response.json()["photos"]
```

> **API Keys**: Unsplash gives 50 req/hour on free tier. Pexels gives 200 req/month on free tier. Register at:
> - https://unsplash.com/developers
> - https://www.pexels.com/api/

---

### 4.5 Canva Connect API Integration

#### Authentication

Canva uses OAuth 2.0. You need to register an integration in the Canva Developer Portal (https://www.canva.com/developers/).

```python
# services/canva.py

class CanvaClient:
    BASE_URL = "https://api.canva.com/rest/v1"

    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )

    # --- Templates ---

    async def list_designs(self) -> list[dict]:
        """List user's Canva designs."""
        response = await self.client.get("/designs")
        response.raise_for_status()
        return response.json()["items"]

    async def get_design(self, design_id: str) -> dict:
        """Get design metadata including thumbnail."""
        response = await self.client.get(f"/designs/{design_id}")
        response.raise_for_status()
        return response.json()["design"]

    # --- Assets ---

    async def upload_asset(self, image_bytes: bytes, filename: str) -> str:
        """Upload an image to Canva as an asset. Returns asset_id."""
        response = await self.client.post(
            "/asset-uploads",
            headers={"Content-Type": "application/octet-stream"},
            content=image_bytes,
            params={"name": filename},
        )
        response.raise_for_status()
        job = response.json()["job"]
        # Poll until complete
        return await self._poll_job(job["id"])

    # --- Design Creation ---

    async def create_design_from_asset(self, asset_id: str, title: str) -> dict:
        """Create a new design from an uploaded asset."""
        response = await self.client.post(
            "/designs",
            json={
                "type": "type_and_asset",
                "design_type": {"type": "preset", "name": "instagramPost"},
                "asset_id": asset_id,
                "title": title,
            },
        )
        response.raise_for_status()
        return response.json()["design"]

    # --- Export ---

    async def export_design(self, design_id: str, format: str = "png") -> bytes:
        """Export a design as image bytes."""
        # Start export job
        response = await self.client.post(
            f"/designs/{design_id}/exports",
            json={"format": {"type": format}},
        )
        response.raise_for_status()
        job = response.json()["job"]

        # Poll until complete
        export_url = await self._poll_export_job(job["id"])

        # Download the exported file
        async with httpx.AsyncClient() as dl:
            img_response = await dl.get(export_url)
            return img_response.content

    # --- Design Editing API ---

    async def update_design_elements(self, design_id: str, updates: dict) -> None:
        """
        Use Design Editing API to programmatically update text/images
        in a Canva design.

        `updates` example:
        {
            "elements": [
                {"id": "element_abc", "type": "TEXT", "text": "New headline"},
                {"id": "element_xyz", "type": "IMAGE", "url": "https://..."}
            ]
        }
        """
        response = await self.client.patch(
            f"/designs/{design_id}/elements",
            json=updates,
        )
        response.raise_for_status()
```

#### OAuth 2.0 Flow (for Web GUI)

```
1. User clicks "Connect Canva" in Web GUI
2. Redirect to: https://www.canva.com/api/oauth/authorize
   ?client_id={CANVA_CLIENT_ID}
   &redirect_uri={YOUR_CALLBACK_URL}
   &response_type=code
   &scope=design:meta:read design:content:read design:content:write
          asset:read asset:write
3. User authorizes → Canva redirects back with ?code=...
4. Exchange code for access_token at POST /api/oauth/token
5. Store token securely (encrypted in DB), refresh when expired
```

> **Note**: Design Editing API is GA as of 2025. Brand Templates API and Autofill API require Canva Enterprise. With a paid Pro/Teams account, you have access to: Design API, Asset API, Export API, Design Editing API, Folder API.

---

### 4.6 Brand Engine

Two paths — user chooses per post:

#### Path A: Pillow (Local Processing)

For simple branded posts: stock/AI photo + logo overlay + text on image.

```python
# services/brand_engine.py

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

class BrandConfig:
    logo_path: Path
    primary_color: str        # hex, e.g. "#2E75B6"
    secondary_color: str
    accent_color: str
    heading_font_path: Path   # .ttf or .otf file
    body_font_path: Path
    logo_position: str        # "bottom_right" | "bottom_left" | "top_right" | "top_left"
    logo_scale: float         # 0.0-1.0, relative to image width
    padding: int              # px

class PillowBrandEngine:
    INSTAGRAM_SIZES = {
        "square": (1080, 1080),
        "portrait": (1080, 1350),
        "landscape": (1080, 608),
    }

    def __init__(self, config: BrandConfig):
        self.config = config
        self.logo = Image.open(config.logo_path).convert("RGBA")

    def apply_brand(
        self,
        image_bytes: bytes,
        text_overlay: str | None = None,
        subtitle: str | None = None,
        aspect: str = "square",
    ) -> bytes:
        """Apply branding to an image and return branded image bytes."""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        # 1. Resize & crop to Instagram dimensions
        target = self.INSTAGRAM_SIZES[aspect]
        img = self._resize_and_crop(img, target)

        # 2. Optional: darken/tint overlay for text readability
        if text_overlay:
            img = self._add_dark_overlay(img, opacity=0.4)

        draw = ImageDraw.Draw(img)

        # 3. Add text overlay (heading)
        if text_overlay:
            font_heading = ImageFont.truetype(
                str(self.config.heading_font_path), size=64
            )
            self._draw_centered_text(
                draw, text_overlay, font_heading, self.config.primary_color,
                y_position=target[1] * 0.35, max_width=target[0] - 120
            )

        # 4. Add subtitle
        if subtitle:
            font_body = ImageFont.truetype(
                str(self.config.body_font_path), size=36
            )
            self._draw_centered_text(
                draw, subtitle, font_body, "#FFFFFF",
                y_position=target[1] * 0.55, max_width=target[0] - 120
            )

        # 5. Add logo watermark
        self._add_logo(img)

        # 6. Convert to RGB and return as bytes
        final = img.convert("RGB")
        buffer = io.BytesIO()
        final.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()

    def create_carousel_slide(
        self,
        slide_number: int,
        total_slides: int,
        heading: str,
        body_text: str,
        background_color: str | None = None,
        background_image: bytes | None = None,
    ) -> bytes:
        """Create a branded carousel slide with text content."""
        target = self.INSTAGRAM_SIZES["square"]

        if background_image:
            img = Image.open(io.BytesIO(background_image)).convert("RGBA")
            img = self._resize_and_crop(img, target)
            img = self._add_dark_overlay(img, opacity=0.5)
        else:
            color = background_color or self.config.primary_color
            img = Image.new("RGBA", target, color)

        draw = ImageDraw.Draw(img)

        # Slide number indicator (e.g., "2/5")
        font_small = ImageFont.truetype(str(self.config.body_font_path), size=24)
        draw.text((target[0] - 80, 40), f"{slide_number}/{total_slides}",
                   fill="#FFFFFF", font=font_small)

        # Heading
        font_heading = ImageFont.truetype(str(self.config.heading_font_path), size=56)
        self._draw_centered_text(draw, heading, font_heading, "#FFFFFF",
                                  y_position=target[1] * 0.25, max_width=target[0] - 160)

        # Body
        font_body = ImageFont.truetype(str(self.config.body_font_path), size=32)
        self._draw_centered_text(draw, body_text, font_body, "#E0E0E0",
                                  y_position=target[1] * 0.45, max_width=target[0] - 160)

        # Logo
        self._add_logo(img)

        final = img.convert("RGB")
        buffer = io.BytesIO()
        final.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()
```

#### Path B: Canva (API-Based)

For complex branded posts using Canva templates:

```python
class CanvaBrandEngine:
    def __init__(self, canva_client: CanvaClient):
        self.canva = canva_client

    async def create_from_template(
        self,
        template_id: str,
        text_replacements: dict[str, str],
        image_replacements: dict[str, str],  # element_id -> image_url
    ) -> bytes:
        """
        1. Duplicate the template as a new design
        2. Update text and image elements via Design Editing API
        3. Export as PNG
        4. Return image bytes
        """
        # Clone template
        design = await self.canva.get_design(template_id)

        # Update elements
        updates = {"elements": []}
        for elem_id, text in text_replacements.items():
            updates["elements"].append({
                "id": elem_id, "type": "TEXT", "text": text
            })
        for elem_id, url in image_replacements.items():
            updates["elements"].append({
                "id": elem_id, "type": "IMAGE", "url": url
            })

        await self.canva.update_design_elements(design["id"], updates)

        # Export
        return await self.canva.export_design(design["id"], format="png")
```

---

### 4.7 Instagram Graph API — Publishing

Requires:
- Instagram **Business** or **Creator** account
- Connected to a **Facebook Page**
- A **Meta App** with `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` permissions
- Images must be hosted on a **publicly accessible URL** at publish time

#### Publishing Flow

```python
# services/instagram.py

class InstagramPublisher:
    BASE_URL = "https://graph.instagram.com"

    def __init__(self, access_token: str, ig_user_id: str):
        self.token = access_token
        self.ig_user_id = ig_user_id
        self.client = httpx.AsyncClient(timeout=60.0)

    # --- Single Image Post ---

    async def publish_single(
        self, image_url: str, caption: str, alt_text: str | None = None
    ) -> str:
        """Publish a single image post. Returns media ID."""
        # Step 1: Create container
        params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.token,
        }
        if alt_text:
            params["alt_text"] = alt_text

        resp = await self.client.post(
            f"{self.BASE_URL}/v25.0/{self.ig_user_id}/media",
            json=params,
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]

        # Step 2: Wait for processing
        await self._wait_for_container(container_id)

        # Step 3: Publish
        return await self._publish_container(container_id)

    # --- Carousel Post ---

    async def publish_carousel(
        self, image_urls: list[str], caption: str
    ) -> str:
        """Publish a carousel post (2-10 images). Returns media ID."""
        # Step 1: Create child containers
        child_ids = []
        for url in image_urls:
            resp = await self.client.post(
                f"{self.BASE_URL}/v25.0/{self.ig_user_id}/media",
                json={
                    "image_url": url,
                    "is_carousel_item": True,
                    "access_token": self.token,
                },
            )
            resp.raise_for_status()
            child_ids.append(resp.json()["id"])

        # Step 2: Create carousel container
        resp = await self.client.post(
            f"{self.BASE_URL}/v25.0/{self.ig_user_id}/media",
            json={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": caption,
                "access_token": self.token,
            },
        )
        resp.raise_for_status()
        carousel_id = resp.json()["id"]

        # Step 3: Wait + Publish
        await self._wait_for_container(carousel_id)
        return await self._publish_container(carousel_id)

    # --- Scheduled Post ---

    async def schedule_post(
        self, image_url: str, caption: str, publish_time: int
    ) -> str:
        """Schedule a post for future publishing. publish_time is Unix timestamp."""
        resp = await self.client.post(
            f"{self.BASE_URL}/v25.0/{self.ig_user_id}/media",
            json={
                "image_url": image_url,
                "caption": caption,
                "published": False,
                "publish_time": publish_time,  # Unix timestamp, 10 min to 75 days ahead
                "access_token": self.token,
            },
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]
        await self._wait_for_container(container_id)
        return await self._publish_container(container_id)

    # --- Helpers ---

    async def _wait_for_container(self, container_id: str, max_retries: int = 30):
        """Poll container status until FINISHED."""
        for _ in range(max_retries):
            resp = await self.client.get(
                f"{self.BASE_URL}/v25.0/{container_id}",
                params={"fields": "status_code", "access_token": self.token},
            )
            status = resp.json().get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise Exception(f"Container processing failed: {resp.json()}")
            await asyncio.sleep(2)
        raise TimeoutError("Container processing timed out")

    async def _publish_container(self, container_id: str) -> str:
        resp = await self.client.post(
            f"{self.BASE_URL}/v25.0/{self.ig_user_id}/media_publish",
            json={
                "creation_id": container_id,
                "access_token": self.token,
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]
```

> **IMPORTANT**: Instagram requires images to be hosted at a **publicly accessible URL**. Before publishing, upload the final images to your server/S3/Cloudflare R2 and use that URL. Images must be JPEG, minimum 600px wide. Carousels support up to 10 items. Rate limit: 50 published posts per 24 hours.

---

### 4.8 Template Package Export

When the user chooses "Export as template" instead of publishing:

```python
# services/exporter.py

import zipfile
from pathlib import Path

class TemplateExporter:
    async def export_package(
        self,
        images: list[bytes],
        caption: str,
        hashtags: list[str],
        post_name: str,
    ) -> bytes:
        """Create a ZIP with images and caption text file."""
        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Save images
            for i, img_bytes in enumerate(images, 1):
                zf.writestr(f"slides/slide_{i:02d}.jpg", img_bytes)

            # Save caption
            caption_text = f"""{caption}

---
HASHTAGS:
{' '.join(hashtags)}

---
Generated by InstaContentEngine
Post name: {post_name}
Slides: {len(images)}
"""
            zf.writestr("caption.txt", caption_text)

            # Save metadata as JSON
            metadata = {
                "post_name": post_name,
                "caption": caption,
                "hashtags": hashtags,
                "num_slides": len(images),
                "aspect_ratio": "1:1",
                "generated_at": datetime.utcnow().isoformat(),
            }
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        buffer.seek(0)
        return buffer.getvalue()
```

---

### 4.9 Telegram Bot

```python
# bot/telegram_bot.py

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

# Conversation states
TOPIC, FORMAT, IMAGE_SOURCE, MODEL, PREVIEW, OUTPUT_CHOICE = range(6)

class InstaBot:
    def __init__(self, token: str, engine: ContentEngine):
        self.engine = engine
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        conv = ConversationHandler(
            entry_points=[CommandHandler("create", self.cmd_create)],
            states={
                TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_topic)],
                FORMAT: [CallbackQueryHandler(self.receive_format)],
                IMAGE_SOURCE: [CallbackQueryHandler(self.receive_image_source)],
                MODEL: [CallbackQueryHandler(self.receive_model)],
                PREVIEW: [CallbackQueryHandler(self.handle_preview_action)],
                OUTPUT_CHOICE: [CallbackQueryHandler(self.handle_output_choice)],
            },
            fallbacks=[CommandHandler("cancel", self.cmd_cancel)],
        )
        self.app.add_handler(conv)
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("templates", self.cmd_templates))
        self.app.add_handler(CommandHandler("models", self.cmd_models))

    async def cmd_create(self, update: Update, context) -> int:
        await update.message.reply_text("What topic should the post be about?")
        return TOPIC

    async def receive_topic(self, update: Update, context) -> int:
        context.user_data["topic"] = update.message.text
        keyboard = [
            [InlineKeyboardButton("Single image", callback_data="single")],
            [InlineKeyboardButton("Carousel (3 slides)", callback_data="carousel_3")],
            [InlineKeyboardButton("Carousel (5 slides)", callback_data="carousel_5")],
            [InlineKeyboardButton("Carousel (10 slides)", callback_data="carousel_10")],
            [InlineKeyboardButton("Infographic", callback_data="infographic")],
        ]
        await update.message.reply_text(
            "Choose format:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return FORMAT

    async def receive_format(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["format"] = query.data

        keyboard = [
            [InlineKeyboardButton("📷 Stock photos", callback_data="stock")],
            [InlineKeyboardButton("🎨 AI generated", callback_data="ai_gen")],
            [InlineKeyboardButton("🎯 Canva template", callback_data="canva")],
            [InlineKeyboardButton("🔀 Mix (choose per slide)", callback_data="mix")],
        ]
        await query.edit_message_text(
            "Where should I get images from?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return IMAGE_SOURCE

    async def receive_image_source(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["image_source"] = query.data

        keyboard = [
            [InlineKeyboardButton("Claude Sonnet", callback_data="claude-sonnet")],
            [InlineKeyboardButton("GPT-4o", callback_data="gpt-4o")],
            [InlineKeyboardButton("Gemini Flash", callback_data="gemini-flash")],
            [InlineKeyboardButton("Llama 70B (cheap)", callback_data="llama-70b")],
        ]
        await query.edit_message_text(
            "Which text model to use?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return MODEL

    async def receive_model(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["text_model"] = query.data

        await query.edit_message_text("⏳ Generating your post...")

        # Generate the post
        post = await self.engine.generate_post(
            topic=context.user_data["topic"],
            format=context.user_data["format"],
            image_source=context.user_data["image_source"],
            text_model=context.user_data["text_model"],
        )
        context.user_data["post"] = post

        # Send preview
        for i, img_bytes in enumerate(post.images):
            await query.message.reply_photo(
                photo=img_bytes,
                caption=f"Slide {i+1}/{len(post.images)}" if len(post.images) > 1 else None,
            )

        await query.message.reply_text(
            f"**Caption:**\n{post.caption}\n\n{' '.join(post.hashtags)}",
            parse_mode="Markdown",
        )

        # Output choice
        keyboard = [
            [InlineKeyboardButton("📤 Publish to Instagram", callback_data="publish")],
            [InlineKeyboardButton("📦 Export template (ZIP)", callback_data="export")],
            [InlineKeyboardButton("🔄 Regenerate", callback_data="regenerate")],
            [InlineKeyboardButton("✏️ Edit caption", callback_data="edit_caption")],
        ]
        await query.message.reply_text(
            "What would you like to do?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return OUTPUT_CHOICE

    async def handle_output_choice(self, update: Update, context) -> int:
        query = update.callback_query
        await query.answer()
        post = context.user_data["post"]

        match query.data:
            case "publish":
                await query.edit_message_text("📤 Publishing to Instagram...")
                media_id = await self.engine.publish_to_instagram(post)
                await query.message.reply_text(
                    f"✅ Published! Media ID: {media_id}"
                )
                return ConversationHandler.END

            case "export":
                await query.edit_message_text("📦 Packing template...")
                zip_bytes = await self.engine.export_template(post)
                await query.message.reply_document(
                    document=zip_bytes,
                    filename=f"{post.name}_template.zip",
                    caption="Here's your template package!",
                )
                return ConversationHandler.END

            case "regenerate":
                await query.edit_message_text("🔄 Regenerating...")
                # Re-run generation with same params
                return await self.receive_model(update, context)

            case "edit_caption":
                await query.edit_message_text(
                    "Send me the new caption text:"
                )
                return TOPIC  # Reuse topic state for editing
```

---

### 4.10 Web GUI (React)

#### Key Pages

| Route | Description |
|-------|-------------|
| `/` | Dashboard — recent posts, quick create |
| `/create` | Post creation wizard (topic → format → source → model → preview → output) |
| `/create/:id/preview` | Full preview with editing — swap images, edit text, reorder slides |
| `/templates` | Manage Canva templates, brand settings |
| `/history` | Post history with performance data |
| `/settings` | API keys, brand config, Instagram connection |

#### Create Page Flow

```
Step 1: Topic Input
  - Text field for topic
  - Dropdown: format (single / carousel N / infographic)
  - Dropdown: text model (Claude / GPT-4o / Gemini / Llama)

Step 2: Image Source (per slide for carousel)
  - For each slide: radio buttons [Stock | AI Gen | Canva]
  - If Stock: shows search query (auto-filled, editable) + photo grid to pick
  - If AI Gen: shows prompt (auto-filled, editable) + model selector + "Generate" button
  - If Canva: shows template picker from user's Canva designs

Step 3: Brand Application
  - Toggle: "Apply branding" (on/off)
  - If on: choose Pillow (simple overlay) or Canva (full template)
  - Preview of branded result

Step 4: Preview + Edit
  - Carousel preview (swipeable)
  - Inline caption editor (rich text)
  - Hashtag editor (add/remove/reorder)
  - "Swap image" button per slide (re-pick from any source)

Step 5: Output Choice
  ┌──────────────────────────────────┐
  │  ○ Publish to Instagram          │
  │    [Publish Now] [Schedule ▼]    │
  │                                  │
  │  ○ Export as template package    │
  │    [Download ZIP]                │
  └──────────────────────────────────┘
```

#### API Endpoints (FastAPI Backend)

```python
# api/routes.py

@router.post("/api/posts/generate")
async def generate_post(request: GenerateRequest) -> PostPreview:
    """Generate a post preview (text + images). Does NOT publish."""

@router.post("/api/posts/{post_id}/publish")
async def publish_post(post_id: str) -> PublishResult:
    """Publish a generated post to Instagram."""

@router.post("/api/posts/{post_id}/schedule")
async def schedule_post(post_id: str, schedule: ScheduleRequest) -> ScheduleResult:
    """Schedule a post for future publishing."""

@router.post("/api/posts/{post_id}/export")
async def export_post(post_id: str) -> FileResponse:
    """Export post as ZIP template package."""

@router.get("/api/posts")
async def list_posts(status: str = None) -> list[PostSummary]:
    """List generated posts, optionally filtered by status."""

@router.put("/api/posts/{post_id}/caption")
async def update_caption(post_id: str, caption: CaptionUpdate) -> PostPreview:
    """Update caption text and hashtags."""

@router.put("/api/posts/{post_id}/slides/{slide_num}/image")
async def replace_slide_image(post_id: str, slide_num: int, config: SlideConfig) -> SlidePreview:
    """Replace a single slide's image from a different source."""

@router.get("/api/models/text")
async def list_text_models() -> list[ModelInfo]:
    """List available text models via OpenRouter."""

@router.get("/api/models/image")
async def list_image_models() -> list[ModelInfo]:
    """List available image generation models."""

@router.get("/api/canva/designs")
async def list_canva_designs() -> list[CanvaDesign]:
    """List user's Canva designs for template selection."""

@router.get("/api/stock/search")
async def search_stock(query: str, source: str = "unsplash") -> list[StockPhoto]:
    """Search stock photos for preview/selection."""

@router.get("/api/brand/config")
async def get_brand_config() -> BrandConfig:
    """Get current brand configuration."""

@router.put("/api/brand/config")
async def update_brand_config(config: BrandConfig) -> BrandConfig:
    """Update brand configuration (colors, fonts, logo)."""
```

---

## 5. Data Models

```python
# models/schemas.py

from pydantic import BaseModel
from enum import Enum
from datetime import datetime

class PostFormat(str, Enum):
    SINGLE = "single"
    CAROUSEL_3 = "carousel_3"
    CAROUSEL_5 = "carousel_5"
    CAROUSEL_10 = "carousel_10"
    INFOGRAPHIC = "infographic"

class ImageSource(str, Enum):
    STOCK = "stock"
    AI_GEN = "ai_gen"
    CANVA = "canva"

class PostStatus(str, Enum):
    DRAFT = "draft"
    PREVIEW = "preview"
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    EXPORTED = "exported"
    FAILED = "failed"

class OutputChoice(str, Enum):
    PUBLISH = "publish"
    EXPORT = "export"
    SCHEDULE = "schedule"

# --- Request Models ---

class GenerateRequest(BaseModel):
    topic: str
    format: PostFormat
    text_model: str = "anthropic/claude-sonnet-4"
    image_model: str | None = "openai/dall-e-3"
    slides: list[SlideConfig] | None = None  # per-slide image source config
    tone: str = "professional"
    niche: str | None = None
    target_audience: str | None = None
    apply_branding: bool = True
    brand_engine: str = "pillow"  # "pillow" or "canva"
    additional_instructions: str | None = None

class SlideConfig(BaseModel):
    slide_number: int
    image_source: ImageSource
    search_query: str | None = None
    gen_prompt: str | None = None
    gen_model: str | None = None
    canva_template_id: str | None = None

class ScheduleRequest(BaseModel):
    publish_time: datetime  # UTC

class CaptionUpdate(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    cta: str | None = None

# --- Response Models ---

class SlidePreview(BaseModel):
    slide_number: int
    image_url: str  # URL to preview image on server
    image_source: ImageSource
    width: int
    height: int

class PostPreview(BaseModel):
    id: str
    topic: str
    format: PostFormat
    status: PostStatus
    caption: str
    hashtags: list[str]
    cta: str | None
    slides: list[SlidePreview]
    text_model_used: str
    image_model_used: str | None
    created_at: datetime

class PublishResult(BaseModel):
    success: bool
    instagram_media_id: str | None
    error: str | None

class ExportResult(BaseModel):
    download_url: str
    filename: str
    size_bytes: int
```

---

## 6. Database Schema

```sql
CREATE TABLE posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic TEXT NOT NULL,
    format VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    caption TEXT,
    hashtags TEXT[],
    cta TEXT,
    text_model VARCHAR(100),
    image_model VARCHAR(100),
    brand_engine VARCHAR(20) DEFAULT 'pillow',
    instagram_media_id VARCHAR(100),
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE slides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    slide_number INT NOT NULL,
    image_source VARCHAR(20) NOT NULL,
    image_path TEXT,  -- local file path or S3 key
    image_url TEXT,   -- public URL (for Instagram publishing)
    search_query TEXT,
    gen_prompt TEXT,
    gen_model VARCHAR(100),
    canva_template_id VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(post_id, slide_number)
);

CREATE TABLE brand_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    logo_path TEXT,
    primary_color VARCHAR(7),
    secondary_color VARCHAR(7),
    accent_color VARCHAR(7),
    heading_font_path TEXT,
    body_font_path TEXT,
    logo_position VARCHAR(20) DEFAULT 'bottom_right',
    logo_scale FLOAT DEFAULT 0.15,
    padding INT DEFAULT 40,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE canva_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE instagram_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    access_token TEXT NOT NULL,
    ig_user_id VARCHAR(100) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 7. Environment Variables

```env
# === Core ===
DATABASE_URL=postgresql://user:pass@localhost:5432/insta_engine
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=your-secret-key-for-jwt

# === OpenRouter ===
OPENROUTER_API_KEY=sk-or-...

# === Stock Photos ===
UNSPLASH_ACCESS_KEY=...
PEXELS_API_KEY=...

# === Canva ===
CANVA_CLIENT_ID=...
CANVA_CLIENT_SECRET=...
CANVA_REDIRECT_URI=http://localhost:3000/auth/canva/callback

# === Instagram / Meta ===
INSTAGRAM_ACCESS_TOKEN=...
INSTAGRAM_USER_ID=...
META_APP_ID=...
META_APP_SECRET=...

# === Telegram ===
TELEGRAM_BOT_TOKEN=...

# === File Storage ===
STORAGE_TYPE=local  # "local" or "s3"
STORAGE_PATH=./uploads
# If S3:
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=insta-engine-assets
AWS_REGION=us-east-1

# === App ===
API_HOST=0.0.0.0
API_PORT=8000
FRONTEND_URL=http://localhost:3000
```

---

## 8. Project Structure

```
instagram-content-engine/
├── backend/
│   ├── main.py                    # FastAPI app entry
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── api/
│   │   ├── routes/
│   │   │   ├── posts.py           # Post CRUD + generate/publish/export
│   │   │   ├── models.py          # List available AI models
│   │   │   ├── canva.py           # Canva OAuth + design listing
│   │   │   ├── stock.py           # Stock photo search
│   │   │   ├── brand.py           # Brand config management
│   │   │   └── auth.py            # OAuth callbacks (Canva, Instagram)
│   │   └── deps.py                # Dependency injection
│   ├── services/
│   │   ├── openrouter.py          # OpenRouter client (text + image gen)
│   │   ├── caption_generator.py   # Caption/hashtag generation logic
│   │   ├── image_router.py        # Image source routing
│   │   ├── stock.py               # Unsplash + Pexels clients
│   │   ├── canva.py               # Canva Connect API client
│   │   ├── brand_engine.py        # Pillow branding
│   │   ├── canva_brand_engine.py  # Canva-based branding
│   │   ├── instagram.py           # Instagram Graph API publisher
│   │   ├── exporter.py            # ZIP template exporter
│   │   └── content_engine.py      # Orchestrator — ties everything together
│   ├── models/
│   │   ├── schemas.py             # Pydantic models (request/response)
│   │   └── database.py            # SQLAlchemy models
│   ├── tasks/
│   │   ├── celery_app.py          # Celery configuration
│   │   ├── generate.py            # Async post generation task
│   │   └── publish.py             # Async publishing task
│   ├── bot/
│   │   ├── telegram_bot.py        # Telegram bot handlers
│   │   └── run_bot.py             # Bot entry point
│   ├── assets/
│   │   ├── brand/                 # Default brand assets
│   │   │   ├── logo.png
│   │   │   ├── heading_font.ttf
│   │   │   └── body_font.ttf
│   │   └── templates/             # Pillow layout templates
│   ├── migrations/                # Alembic migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── CreatePost.tsx     # Multi-step wizard
│   │   │   ├── PreviewPost.tsx    # Full preview + editing
│   │   │   ├── Templates.tsx      # Canva templates manager
│   │   │   ├── History.tsx        # Post history
│   │   │   └── Settings.tsx       # API keys, brand config
│   │   ├── components/
│   │   │   ├── SlideEditor.tsx    # Per-slide image source picker
│   │   │   ├── CaptionEditor.tsx  # Rich text caption editor
│   │   │   ├── HashtagEditor.tsx  # Add/remove/reorder hashtags
│   │   │   ├── ImagePicker.tsx    # Stock search / AI gen / Canva picker
│   │   │   ├── CarouselPreview.tsx # Swipeable carousel preview
│   │   │   ├── ModelSelector.tsx  # AI model dropdown
│   │   │   ├── OutputChoice.tsx   # Publish vs Export selector
│   │   │   └── BrandSettings.tsx  # Logo, colors, fonts config
│   │   ├── api/
│   │   │   └── client.ts          # Axios/fetch API client
│   │   └── App.tsx
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 9. Implementation Order (Recommended)

### Phase 1: Core Engine (MVP)
1. Set up FastAPI project with PostgreSQL
2. Implement OpenRouter client (text generation only)
3. Implement caption generator with system prompts
4. Implement Unsplash stock photo search + download
5. Implement Pillow brand engine (basic logo overlay)
6. Implement ZIP template exporter
7. Build a minimal `/api/posts/generate` + `/api/posts/{id}/export` endpoint
8. **Test**: Generate a post about "AI trends" → get a ZIP with branded image + caption

### Phase 2: Telegram Bot
9. Set up python-telegram-bot with conversation handler
10. Wire bot to content engine
11. Add output choice (export ZIP via Telegram document)
12. **Test**: `/create` in Telegram → get post preview + download ZIP

### Phase 3: Image Variability
13. Add Pexels API as second stock source
14. Implement OpenRouter image generation (DALL-E, Flux)
15. Build image router with per-slide source selection
16. Implement carousel generation (multi-slide)
17. **Test**: Generate carousel with slide 1 = stock, slide 2 = AI gen

### Phase 4: Canva Integration
18. Implement Canva OAuth flow
19. Build Canva client (list designs, export)
20. Add Canva as third image source option
21. Implement Canva brand engine (Design Editing API)
22. **Test**: Pick Canva template → auto-fill text → export branded image

### Phase 5: Instagram Publishing
23. Set up Meta App + Instagram Graph API auth
24. Implement single image publishing
25. Implement carousel publishing
26. Implement scheduled publishing
27. Add publish option to both Telegram bot and API
28. **Test**: Generate + publish a single post to Instagram

### Phase 6: Web GUI
29. Create React app with Tailwind
30. Build creation wizard (multi-step form)
31. Build preview/edit page with carousel viewer
32. Build output choice component (publish vs export)
33. Build settings page (API keys, brand config)
34. **Test**: Full flow in browser from topic to published post

### Phase 7: Polish
35. Add Celery for async generation/publishing
36. Add post history and status tracking
37. Add error handling and retry logic throughout
38. Add rate limiting awareness (OpenRouter, Instagram, stock APIs)
39. Docker Compose for full stack deployment
40. Write README with setup instructions

---

## 10. Key Considerations

### Rate Limits
| Service | Limit | Notes |
|---------|-------|-------|
| OpenRouter | Varies by model | Check `x-ratelimit-*` headers |
| Unsplash | 50 req/hour (free) | Upgrade for production |
| Pexels | 200 req/month (free) | Generous for testing |
| Instagram Graph API | 50 posts/24h | Per account |
| Canva Connect API | 100 req/min per user | Per endpoint |

### Image Requirements (Instagram)
- Minimum width: 600px (recommended: 1080px)
- Aspect ratios: 1:1 (square), 4:5 (portrait), 1.91:1 (landscape)
- Max file size: 8MB per image
- Formats: JPEG, PNG
- Carousel: 2–10 items
- Images must be at a **publicly accessible URL** when publishing via API

### Security
- Store all API tokens encrypted in DB (use `cryptography.fernet`)
- Never expose API keys to frontend — all calls go through backend
- Use HTTPS in production
- Instagram and Canva tokens need periodic refresh — implement refresh logic
- Rate-limit your own API endpoints to prevent abuse

### Cost Optimization
- Use cheaper models (Llama, Gemini Flash) for batch generation
- Cache stock photo search results (same query = same photos for 1 hour)
- Use Pillow branding for simple posts, Canva only when templates are needed
- Store generated images locally/S3 — don't regenerate

---

## 11. Output Decision Flow

```
Post generated and previewed
            │
            ▼
    ┌───────────────┐
    │ User chooses   │
    │ output action  │
    └───────┬───────┘
            │
     ┌──────┴──────┐
     ▼              ▼
 PUBLISH         EXPORT
     │              │
     ▼              ▼
┌─────────┐   ┌──────────┐
│ Upload   │   │ Package  │
│ images   │   │ images   │
│ to CDN   │   │ + caption│
│          │   │ as ZIP   │
│ Create   │   │          │
│ IG media │   │ Send via │
│ container│   │ TG / HTTP│
│          │   │ download │
│ Publish  │   │          │
│ or       │   └──────────┘
│ Schedule │
└─────────┘
```

The user can ALWAYS choose between publishing or exporting at the final step, regardless of which interface (Telegram or Web) they used to create the post.
