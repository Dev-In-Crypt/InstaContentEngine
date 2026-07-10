import uuid
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text, DateTime, JSON
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic = Column(Text, nullable=False)
    format = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    caption = Column(Text)
    hashtags = Column(JSON)          # stored as JSON array
    seo_keywords = Column(JSON)      # separate from hashtags
    sources = Column(JSON)           # [{title,url}] from web-grounded LLM (:online)
    cta = Column(Text)
    hook = Column(Text)
    alt_text = Column(Text)
    platform = Column(String(20), default="instagram")
    template_style = Column(String(20), default="branded_card")
    text_model = Column(String(100))
    image_model = Column(String(100))
    brand_engine = Column(String(20), default="pillow")
    trend_idea_id = Column(String(36), ForeignKey("trend_ideas.id", ondelete="SET NULL"))
    instagram_media_id = Column(String(100))
    scheduled_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    published_image_urls = Column(JSON)   # imgbb public URLs used for publishing
    schedule_error = Column(Text)          # last publish failure (status=failed)
    pillar = Column(String(30))            # content pillar (educational/inspirational/...)
    video_path = Column(Text)              # generated Reel MP4 on disk
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    slides = relationship("Slide", back_populates="post", cascade="all, delete-orphan")
    trend_idea = relationship("TrendIdea", back_populates="posts")
    insights = relationship("PostInsight", back_populates="post", cascade="all, delete-orphan")


class Slide(Base):
    __tablename__ = "slides"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String(36), ForeignKey("posts.id", ondelete="CASCADE"))
    slide_number = Column(Integer, nullable=False)
    page_number = Column(Integer)    # manual carousel page number
    image_source = Column(String(20), nullable=False)
    image_path = Column(Text)        # absolute path on disk
    image_url = Column(Text)
    search_query = Column(Text)
    gen_prompt = Column(Text)
    attribution = Column(JSON)        # {source, author_name, author_profile_url, source_link}
    render_params = Column(JSON)      # overlay text + brand config used so single-slide regenerate can reproduce the look
    raw_image_path = Column(Text)     # unbranded background JPEG path, used by PUT /overlay
    original_overlay_text = Column(Text)   # LLM-generated overlay text, for Reset
    original_niche_text = Column(Text)     # LLM-generated niche text (slide 1), for Reset
    gen_model = Column(String(100))
    canva_template_id = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    post = relationship("Post", back_populates="slides")


class BrandConfig(Base):
    __tablename__ = "brand_configs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False)
    is_default = Column(Boolean, default=False)
    logo_path = Column(Text)
    primary_color = Column(String(7))
    secondary_color = Column(String(7))
    accent_color = Column(String(7))
    heading_font_path = Column(Text)
    body_font_path = Column(Text)
    logo_position = Column(String(20), default="bottom_right")
    logo_scale = Column(Float, default=0.15)
    padding = Column(Integer, default=40)
    template_style = Column(String(20), default="branded_card")
    niche_box_color = Column(String(7), default="#ff751f")
    niche_box_palette = Column(JSON)
    description_box_alpha = Column(Float, default=0.79)
    show_logo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CanvaToken(Base):
    __tablename__ = "canva_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InstagramToken(Base):
    __tablename__ = "instagram_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    access_token = Column(Text, nullable=False)
    ig_user_id = Column(String(100), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class HashtagStat(Base):
    """Cached hashtag intelligence (IG hashtag limit is 30 unique / 7 days,
    so we memoize lookups here)."""
    __tablename__ = "hashtag_stats"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tag = Column(String(120), nullable=False, unique=True, index=True)
    media_count = Column(Integer)          # absolute IG count (often unavailable → nullable)
    avg_engagement = Column(Float)         # avg likes+comments of top media / competitor posts
    frequency = Column(Integer)            # times seen across trending_media
    trend = Column(String(4))              # "up" | "flat" | "down"
    source = Column(String(20))            # "ig_api" | "heuristic" | "both"
    checked_at = Column(DateTime(timezone=True), server_default=func.now())


class PostInsight(Base):
    """A point-in-time snapshot of a published post's Instagram metrics."""
    __tablename__ = "post_insights"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String(36), ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    snapshot_at = Column(DateTime(timezone=True), server_default=func.now())
    reach = Column(Integer)
    impressions = Column(Integer)
    likes = Column(Integer)
    comments = Column(Integer)
    saved = Column(Integer)
    shares = Column(Integer)
    total_interactions = Column(Integer)
    plays = Column(Integer)          # video / Reels only
    video_views = Column(Integer)    # video / Reels only
    raw = Column(JSON)               # full Graph API response

    post = relationship("Post", back_populates="insights")


# ---------------- Trend Finder ----------------

class CompetitorAccount(Base):
    __tablename__ = "competitor_accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    handle = Column(String(64), nullable=False, unique=True)
    niche = Column(String(100))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TrendingMedia(Base):
    __tablename__ = "trending_media"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_handle = Column(String(64), nullable=False, index=True)
    ig_media_id = Column(String(100), nullable=False, unique=True)
    media_type = Column(String(20), nullable=False)
    permalink = Column(Text)
    thumbnail_url = Column(Text)
    caption = Column(Text)
    extracted_hook = Column(Text)
    extracted_topic = Column(Text)
    extracted_cta = Column(Text)
    hashtags = Column(JSON)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    views = Column(Integer)
    engagement_score = Column(Float, default=0.0)
    posted_at = Column(DateTime(timezone=True))
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())
    raw_payload = Column(JSON)

    ideas = relationship("TrendIdea", back_populates="source_media", cascade="all, delete-orphan")


class TrendIdea(Base):
    __tablename__ = "trend_ideas"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_media_id = Column(String(36), ForeignKey("trending_media.id", ondelete="CASCADE"))
    hook = Column(Text, nullable=False)
    short_script = Column(Text)
    shot_list = Column(JSON)
    caption = Column(Text)
    cta = Column(Text)
    hashtags = Column(JSON)
    seo_keywords = Column(JSON)
    platform = Column(String(20), default="instagram")
    length_tier = Column(String(20), default="sweet_spot")
    additional_instructions = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    source_media = relationship("TrendingMedia", back_populates="ideas")
    posts = relationship("Post", back_populates="trend_idea")
