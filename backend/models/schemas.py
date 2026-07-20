from __future__ import annotations
import re
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class PostFormat(str, Enum):
    SINGLE = "single"
    CAROUSEL_3 = "carousel_3"
    CAROUSEL_5 = "carousel_5"
    CAROUSEL_10 = "carousel_10"
    INFOGRAPHIC = "infographic"
    REEL = "reel"


class ImageSource(str, Enum):
    STOCK = "stock"
    AI_GEN = "ai_gen"
    CANVA = "canva"


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    X = "x"


class XPostMode(str, Enum):
    """How an X post is shaped. SHORT is one tweet; THREAD is a chain of tweets,
    each a complete thought continuing the previous; LONG is a single long-form
    post, which only X Premium accounts may publish."""
    SHORT = "short"
    THREAD = "thread"
    LONG = "long"


#: Hard cap on tweets per thread. A thread of N tweets spends N of the account's
#: monthly X quota (1,500 on the free tier), so this is deliberately bounded.
MAX_THREAD_TWEETS = 15
#: Per-tweet character budget, hashtags included. Below X's 280 on purpose.
TWEET_CHAR_LIMIT = 250


class LengthTier(str, Enum):
    HOOK_ZONE = "hook_zone"      # ~125 chars
    SWEET_SPOT = "sweet_spot"    # 125-150 then continues
    DEEP_DIVE = "deep_dive"      # 300-900+ chars


class TemplateStyle(str, Enum):
    SQUARE = "square"               # legacy apply_brand path
    BRANDED_CARD = "branded_card"   # portrait 1080x1350 card


# Suggested quick swatches for the niche box. NOT a whitelist — slide colours are
# per-tenant, so any valid #rrggbb is accepted; these are just one-click shortcuts.
NICHE_BOX_PALETTE = ["#ffbf00", "#0076cb", "#5e17eb", "#00bf63", "#000000", "#ff751f"]
HEX_COLOR_RE = r"^#[0-9a-fA-F]{6}$"


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

class SlideConfig(BaseModel):
    slide_number: int
    image_source: ImageSource
    search_query: Optional[str] = None
    gen_prompt: Optional[str] = None
    gen_model: Optional[str] = None
    canva_template_id: Optional[str] = None
    page_number: Optional[int] = None       # manual carousel page number; None → none rendered


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    format: PostFormat
    text_model: Optional[str] = None        # None → use DEFAULT_TEXT_MODEL from .env
    image_model: Optional[str] = None       # None → use DEFAULT_IMAGE_MODEL from .env
    default_image_source: ImageSource = ImageSource.STOCK
    slides: Optional[list[SlideConfig]] = None
    tone: str = "professional"
    niche: Optional[str] = None
    target_audience: Optional[str] = None
    apply_branding: bool = True
    brand_engine: str = "pillow"
    additional_instructions: Optional[str] = None
    platform: Platform = Platform.INSTAGRAM
    length_tier: LengthTier = LengthTier.SWEET_SPOT
    template_style: TemplateStyle = TemplateStyle.BRANDED_CARD
    niche_box_color: Optional[str] = None   # None → brand default; else must be in palette
    show_logo: bool = True
    brand_config_id: Optional[str] = None   # None → default brand preset
    brand_voice_preset: Optional[str] = None  # per-post override of the saved brand voice
    # X only. thread_min/max bound how many tweets a thread may have; the model
    # picks a number inside the range that fits the topic, rather than padding or
    # squeezing the argument to hit an exact count.
    x_mode: XPostMode = XPostMode.SHORT
    thread_min: int = Field(3, ge=2, le=MAX_THREAD_TWEETS)
    thread_max: int = Field(7, ge=2, le=MAX_THREAD_TWEETS)

    @model_validator(mode="after")
    def _validate_thread_range(self):
        if self.thread_min > self.thread_max:
            raise ValueError("thread_min cannot be greater than thread_max")
        return self

    @field_validator("niche_box_color")
    @classmethod
    def _validate_niche_box_color(cls, v: Optional[str]) -> Optional[str]:
        """Any valid #rrggbb — slide colours are per-tenant, the palette is only
        a set of suggested swatches."""
        if v is None:
            return v
        v = v.strip().lower()
        if not re.fullmatch(HEX_COLOR_RE, v):
            raise ValueError("niche_box_color must be a hex colour like #ff751f")
        return v


class ScheduleRequest(BaseModel):
    publish_at: datetime


class CaptionUpdate(BaseModel):
    caption: Optional[str] = None
    hashtags: Optional[list[str]] = None
    cta: Optional[str] = None
    seo_keywords: Optional[list[str]] = None
    thread_parts: Optional[list[str]] = None   # edited X thread, in order


class ReplaceSlideRequest(BaseModel):
    """Body for re-fetching a single slide's image without re-running the whole post."""
    search_query: Optional[str] = None             # override stored query
    image_source: Optional[ImageSource] = None     # switch stock ↔ ai_gen
    gen_prompt: Optional[str] = None               # for ai_gen
    image_model: Optional[str] = None              # for ai_gen
    stock_source: Optional[str] = None             # "unsplash" | "pexels" | "auto"


class OverlayUpdateRequest(BaseModel):
    """Body for editing overlay text in-place on an existing slide (re-renders
    over the stored raw image, no fresh image fetch)."""
    overlay_text: Optional[str] = None        # description box (white, lower)
    niche_text: Optional[str] = None          # niche box (colored, upper) — slide 1 only typically


class RegenFieldRequest(BaseModel):
    field: str = Field(..., pattern="^(caption|hook|cta|hashtags|seo_keywords)$")
    count: int = Field(4, ge=1, le=8)


class RegenFieldResponse(BaseModel):
    field: str
    variants: list                              # list[str] or list[list[str]]


class PostInsightSchema(BaseModel):
    snapshot_at: datetime
    reach: Optional[int] = None
    impressions: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    saved: Optional[int] = None
    shares: Optional[int] = None
    total_interactions: Optional[int] = None
    plays: Optional[int] = None
    video_views: Optional[int] = None

    model_config = {"from_attributes": True}


# --- Response Models ---

class SlidePreview(BaseModel):
    slide_number: int
    image_url: str
    image_source: ImageSource
    width: int
    height: int
    attribution: Optional[dict] = None     # stock credits, see services.stock
    # overlay editing
    overlay_text: Optional[str] = None
    niche_text: Optional[str] = None
    original_overlay_text: Optional[str] = None   # LLM-generated, for Reset
    original_niche_text: Optional[str] = None
    has_raw_image: bool = False                   # True if PUT /overlay is supported


class PostPreview(BaseModel):
    id: str
    topic: str
    format: PostFormat
    status: PostStatus
    caption: str
    thread_parts: list[str] = []      # non-empty only for an X thread
    hashtags: list[str]
    seo_keywords: list[str] = []
    cta: Optional[str]
    hook: Optional[str]
    platform: Platform = Platform.INSTAGRAM
    slides: list[SlidePreview]
    text_model_used: str
    image_model_used: Optional[str]
    created_at: datetime
    sources: list[dict] = []           # [{title,url}] from web-grounded LLM (:online)
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    schedule_error: Optional[str] = None
    instagram_media_id: Optional[str] = None

    model_config = {"from_attributes": True}


class PostSummary(BaseModel):
    id: str
    topic: str
    format: PostFormat
    status: PostStatus
    thumb_url: Optional[str] = None      # first slide image, for grid/calendar
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PublishResult(BaseModel):
    success: bool
    instagram_media_id: Optional[str] = None   # platform post id
    published_url: Optional[str] = None         # permalink to the published post
    error: Optional[str] = None


class ExportResult(BaseModel):
    download_url: str
    filename: str
    size_bytes: int


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str


class StockPhoto(BaseModel):
    id: str
    url: str
    thumb_url: str
    alt: Optional[str] = None
    source: str  # "unsplash" or "pexels"


class CanvaDesign(BaseModel):
    id: str
    title: str
    thumbnail_url: Optional[str] = None


class BrandConfigSchema(BaseModel):
    id: Optional[str] = None
    name: str = "Default"
    is_default: bool = False
    logo_path: Optional[str] = None
    primary_color: str = "#2E75B6"
    secondary_color: str = "#1A4D8A"
    accent_color: str = "#F0A500"
    heading_font_path: Optional[str] = None
    body_font_path: Optional[str] = None
    logo_position: str = "bottom_right"
    logo_scale: float = 0.15
    padding: int = 40
    template_style: str = "branded_card"
    niche_box_color: str = "#ff751f"
    niche_box_palette: list[str] = NICHE_BOX_PALETTE
    description_box_alpha: float = 0.79
    show_logo: bool = True

    model_config = {"from_attributes": True}


# --- Brand voice (generation style preference) ---

class BrandVoiceUpdate(BaseModel):
    preset: Optional[str] = None                       # a preset key or "custom"
    custom: Optional[str] = Field(None, max_length=800)  # used when preset == "custom"


class BrandVoiceResponse(BaseModel):
    preset: str                    # current saved preset (default "balanced")
    custom: str = ""               # current custom text (empty unless preset == "custom")
    presets: list[dict] = []       # [{key,label,description}] for the settings UI


# --- Brand profile (niche/audience/brand set once, used as generation defaults) ---

class ProfileUpdate(BaseModel):
    niche: Optional[str] = Field(None, max_length=120)
    target_audience: Optional[str] = Field(None, max_length=120)
    brand_name: Optional[str] = Field(None, max_length=120)


class ProfileResponse(BaseModel):
    niche: str = ""
    target_audience: str = ""
    brand_name: str = ""


# --- Slide colours (per-tenant branding of the generated slides) ---

class SlideStyleUpdate(BaseModel):
    accent_color: Optional[str] = None      # niche box fill; "" clears to default
    text_box_color: Optional[str] = None    # description box fill

    @field_validator("accent_color", "text_box_color")
    @classmethod
    def _validate_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        v = v.strip().lower()
        if not re.fullmatch(HEX_COLOR_RE, v):
            raise ValueError("colour must be a hex value like #ff751f")
        return v


class SlideStyleResponse(BaseModel):
    accent_color: str = ""          # empty → the platform default is used
    text_box_color: str = ""
    default_accent_color: str = ""  # what generation falls back to when unset
    palette: list[str] = []         # suggested swatches for the UI


# --- X-specific account settings ---

class XSettingsUpdate(BaseModel):
    # Our own record of the tenant's X plan, not a check against X. Enabling it
    # unlocks the long-post mode; if the account isn't actually Premium, X
    # rejects the tweet and we surface its error.
    x_premium: bool


class XSettingsResponse(BaseModel):
    x_premium: bool = False
    tweet_char_limit: int = TWEET_CHAR_LIMIT
    max_thread_tweets: int = MAX_THREAD_TWEETS


# --- AI provider + model selection (per tenant, they pay for it) ---

class AISettingsUpdate(BaseModel):
    text_provider: Optional[str] = None
    text_model: Optional[str] = Field(None, max_length=120)
    image_provider: Optional[str] = None
    image_model: Optional[str] = Field(None, max_length=120)


class AISettingsResponse(BaseModel):
    text_provider: str = ""
    text_model: str = ""
    image_provider: str = ""
    image_model: str = ""
    #: {provider_key: {"set": bool, "masked": str|None}} — never raw keys.
    keys: dict = {}


class AITestRequest(BaseModel):
    kind: str = "text"              # "text" | "image"


class AITestResponse(BaseModel):
    ok: bool
    message: str
