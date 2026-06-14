from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


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


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"


class LengthTier(str, Enum):
    HOOK_ZONE = "hook_zone"      # ~125 chars
    SWEET_SPOT = "sweet_spot"    # 125-150 then continues
    DEEP_DIVE = "deep_dive"      # 300-900+ chars


class TemplateStyle(str, Enum):
    SQUARE = "square"               # legacy apply_brand path
    BRANDED_CARD = "branded_card"   # portrait 1080x1350 card


# Selectable colors for the niche box (orange box in the mockup)
NICHE_BOX_PALETTE = ["#ffbf00", "#0076cb", "#5e17eb", "#00bf63", "#000000", "#ff751f"]


class TrendSource(str, Enum):
    BUSINESS_DISCOVERY = "business_discovery"
    SCRAPER = "scraper"


class TrendMediaType(str, Enum):
    REEL = "reel"
    VIDEO = "video"
    IMAGE = "image"
    CAROUSEL = "carousel"


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
    trend_idea_id: Optional[str] = None     # link this post to a trend idea

    @field_validator("niche_box_color")
    @classmethod
    def _validate_niche_box_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower()
        if v not in NICHE_BOX_PALETTE:
            raise ValueError(f"niche_box_color must be one of {NICHE_BOX_PALETTE}")
        return v


class ScheduleRequest(BaseModel):
    publish_time: datetime


class CaptionUpdate(BaseModel):
    caption: Optional[str] = None
    hashtags: Optional[list[str]] = None
    cta: Optional[str] = None
    seo_keywords: Optional[list[str]] = None


class ReplaceSlideRequest(BaseModel):
    """Body for re-fetching a single slide's image without re-running the whole post."""
    search_query: Optional[str] = None             # override stored query
    image_source: Optional[ImageSource] = None     # switch stock ↔ ai_gen
    gen_prompt: Optional[str] = None               # for ai_gen
    image_model: Optional[str] = None              # for ai_gen
    stock_source: Optional[str] = None             # "unsplash" | "pexels" | "auto"


# --- Response Models ---

class SlidePreview(BaseModel):
    slide_number: int
    image_url: str
    image_source: ImageSource
    width: int
    height: int
    attribution: Optional[dict] = None     # stock credits, see services.stock


class PostPreview(BaseModel):
    id: str
    topic: str
    format: PostFormat
    status: PostStatus
    caption: str
    hashtags: list[str]
    seo_keywords: list[str] = []
    cta: Optional[str]
    hook: Optional[str]
    platform: Platform = Platform.INSTAGRAM
    slides: list[SlidePreview]
    text_model_used: str
    image_model_used: Optional[str]
    created_at: datetime
    trend_idea_id: Optional[str] = None
    trend_source_handle: Optional[str] = None
    trend_source_permalink: Optional[str] = None

    model_config = {"from_attributes": True}


class PostSummary(BaseModel):
    id: str
    topic: str
    format: PostFormat
    status: PostStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class PublishResult(BaseModel):
    success: bool
    instagram_media_id: Optional[str] = None
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


# --- Trend Finder Models ---

class CompetitorAccount(BaseModel):
    id: Optional[str] = None
    handle: str
    niche: Optional[str] = None
    active: bool = True
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CompetitorCreate(BaseModel):
    handle: str = Field(..., min_length=1, max_length=64)
    niche: Optional[str] = None
    active: bool = True


class CompetitorUpdate(BaseModel):
    niche: Optional[str] = None
    active: Optional[bool] = None


class TrendingMediaPreview(BaseModel):
    id: str
    source_handle: str
    ig_media_id: str
    media_type: TrendMediaType
    permalink: Optional[str] = None
    thumbnail_url: Optional[str] = None
    caption: Optional[str] = None
    extracted_hook: Optional[str] = None
    extracted_topic: Optional[str] = None
    extracted_cta: Optional[str] = None
    hashtags: list[str] = []
    likes: int = 0
    comments: int = 0
    views: Optional[int] = None
    engagement_score: float = 0.0
    posted_at: Optional[datetime] = None
    fetched_at: datetime

    model_config = {"from_attributes": True}


class TrendIdeaSchema(BaseModel):
    id: str
    source_media_id: str
    hook: str
    short_script: Optional[str] = None
    shot_list: list[str] = []
    caption: Optional[str] = None
    cta: Optional[str] = None
    hashtags: list[str] = []
    seo_keywords: list[str] = []
    platform: Platform = Platform.INSTAGRAM
    length_tier: LengthTier = LengthTier.SWEET_SPOT
    additional_instructions: Optional[str] = None
    created_at: datetime
    source_handle: Optional[str] = None       # denormalized for UI
    source_permalink: Optional[str] = None    # denormalized for UI

    model_config = {"from_attributes": True}


class TrendIdeaUpdate(BaseModel):
    hook: Optional[str] = None
    short_script: Optional[str] = None
    shot_list: Optional[list[str]] = None
    caption: Optional[str] = None
    cta: Optional[str] = None
    hashtags: Optional[list[str]] = None
    seo_keywords: Optional[list[str]] = None


class RefreshTrendsRequest(BaseModel):
    source: TrendSource = TrendSource.BUSINESS_DISCOVERY
    limit_per_account: int = Field(10, ge=1, le=50)
    handles: Optional[list[str]] = None       # None → all active competitors


class AdaptTrendRequest(BaseModel):
    platform: Platform = Platform.INSTAGRAM
    length_tier: LengthTier = LengthTier.SWEET_SPOT
    additional_instructions: Optional[str] = None


class GenerateFromIdeaRequest(BaseModel):
    """Override fields used when generating a post from a trend idea.
    Defaults come from the idea (topic from hook, niche from source) when omitted.
    """
    format: PostFormat = PostFormat.SINGLE
    text_model: Optional[str] = None
    image_model: Optional[str] = None
    default_image_source: ImageSource = ImageSource.STOCK
    tone: str = "professional"
    niche: Optional[str] = None
    target_audience: Optional[str] = None
    apply_branding: bool = True
    template_style: TemplateStyle = TemplateStyle.BRANDED_CARD
    niche_box_color: Optional[str] = None
    show_logo: bool = True
    brand_config_id: Optional[str] = None
    additional_instructions: Optional[str] = None

    @field_validator("niche_box_color")
    @classmethod
    def _validate_niche_box_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower()
        if v not in NICHE_BOX_PALETTE:
            raise ValueError(f"niche_box_color must be one of {NICHE_BOX_PALETTE}")
        return v
