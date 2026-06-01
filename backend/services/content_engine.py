import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from models.schemas import ImageSource, PostFormat, Platform, LengthTier, TemplateStyle
from services.caption_generator import CaptionGenerator, GeneratedCaption
from services.image_router import ImageRouter, SlideImageConfig, ImageFetchError
from services.openrouter import OpenRouterError
from services.stock import StockError
from services.brand_engine import PillowBrandEngine, BrandConfig
from services.exporter import TemplateExporter


@dataclass
class GeneratedSlide:
    slide_number: int
    image_bytes: bytes
    image_source: ImageSource
    search_query: Optional[str] = None
    gen_prompt: Optional[str] = None


@dataclass
class GeneratedPost:
    id: str
    topic: str
    format: PostFormat
    caption: str
    hashtags: list[str]
    cta: str
    hook: str
    alt_text: str
    slides: list[GeneratedSlide]
    text_model_used: str
    image_model_used: Optional[str]
    seo_keywords: list[str] = field(default_factory=list)
    platform: Platform = Platform.INSTAGRAM

    @property
    def images(self) -> list[bytes]:
        return [s.image_bytes for s in self.slides]


ProgressFn = Callable[[str], Awaitable[None]]


def _num_slides(format: PostFormat) -> int:
    mapping = {
        PostFormat.SINGLE: 1,
        PostFormat.CAROUSEL_3: 3,
        PostFormat.CAROUSEL_5: 5,
        PostFormat.CAROUSEL_10: 10,
        PostFormat.INFOGRAPHIC: 1,
    }
    return mapping.get(format, 1)


class ContentEngine:
    def __init__(
        self,
        caption_generator: CaptionGenerator,
        image_router: ImageRouter,
        brand_engine: PillowBrandEngine,
        exporter: TemplateExporter,
    ):
        self.caption_gen = caption_generator
        self.image_router = image_router
        self.brand_engine = brand_engine
        self.exporter = exporter

    async def generate_post(
        self,
        topic: str,
        format: PostFormat,
        text_model: str = "",
        image_model: Optional[str] = None,
        default_image_source: ImageSource = ImageSource.STOCK,
        slide_configs: Optional[list[SlideImageConfig]] = None,
        tone: str = "professional",
        niche: Optional[str] = None,
        target_audience: Optional[str] = None,
        additional_instructions: Optional[str] = None,
        apply_branding: bool = True,
        platform: Platform = Platform.INSTAGRAM,
        length_tier: LengthTier = LengthTier.SWEET_SPOT,
        template_style: TemplateStyle = TemplateStyle.BRANDED_CARD,
        niche_box_color: Optional[str] = None,
        show_logo: bool = True,
        progress: Optional[ProgressFn] = None,
    ) -> GeneratedPost:
        num = _num_slides(format)

        # 1. Generate caption + prompts
        if progress:
            await progress("Writing caption...")
        caption_data: GeneratedCaption = await self.caption_gen.generate(
            topic=topic,
            format=format.value,
            num_slides=num,
            text_model=text_model,
            tone=tone,
            niche=niche,
            target_audience=target_audience,
            additional_instructions=additional_instructions,
            platform=platform,
            length_tier=length_tier,
        )

        # 2. Build per-slide configs if not supplied
        if not slide_configs:
            slide_configs = self._build_default_slide_configs(
                num=num,
                image_source=default_image_source,
                search_queries=caption_data.image_search_queries,
                gen_prompts=caption_data.image_gen_prompts,
                image_model=image_model,
            )

        # 3. Fetch + brand all slides in parallel
        if progress:
            await progress("Fetching & branding images...")
        tasks = [
            self._fetch_and_brand(
                cfg, num, format, caption_data, apply_branding, topic,
                template_style, niche_box_color, show_logo, niche,
            )
            for cfg in slide_configs[:num]
        ]
        slides = await asyncio.gather(*tasks)
        slides = sorted(slides, key=lambda s: s.slide_number)

        return GeneratedPost(
            id=str(uuid.uuid4()),
            topic=topic,
            format=format,
            caption=caption_data.caption,
            hashtags=caption_data.hashtags,
            cta=caption_data.cta,
            hook=caption_data.hook,
            alt_text=caption_data.alt_text,
            slides=slides,
            text_model_used=text_model,
            image_model_used=image_model,
            seo_keywords=caption_data.seo_keywords,
            platform=platform,
        )

    async def _fetch_and_brand(
        self,
        cfg: SlideImageConfig,
        num: int,
        format: PostFormat,
        caption_data: GeneratedCaption,
        apply_branding: bool,
        topic: str,
        template_style: TemplateStyle = TemplateStyle.BRANDED_CARD,
        niche_box_color: Optional[str] = None,
        show_logo: bool = True,
        niche: Optional[str] = None,
    ) -> GeneratedSlide:
        try:
            raw_bytes = await self.image_router.fetch_image(cfg)
        except (ImageFetchError, OpenRouterError, StockError):
            fallback = SlideImageConfig(
                slide_number=cfg.slide_number,
                image_source=ImageSource.STOCK,
                search_query=cfg.search_query or topic,
                page_number=cfg.page_number,
            )
            raw_bytes = await self.image_router.fetch_image(fallback)
            cfg = fallback

        if not apply_branding:
            branded = raw_bytes
        elif template_style == TemplateStyle.BRANDED_CARD:
            heading = caption_data.hook if cfg.slide_number == 1 else f"Slide {cfg.slide_number}"
            page_num = cfg.page_number if cfg.page_number is not None else (
                cfg.slide_number if num > 1 else None
            )
            branded = self.brand_engine.create_branded_card(
                background_image=raw_bytes,
                niche_text=niche or topic,
                description_text=heading,
                niche_box_color=niche_box_color,
                show_logo=show_logo,
                page_number=page_num,
                total_slides=num if num > 1 else None,
            )
        elif format in (PostFormat.SINGLE, PostFormat.INFOGRAPHIC):
            branded = self.brand_engine.apply_brand(
                raw_bytes,
                text_overlay=caption_data.hook,
                subtitle=caption_data.cta,
            )
        else:
            heading = caption_data.hook if cfg.slide_number == 1 else f"Slide {cfg.slide_number}"
            body = caption_data.cta if cfg.slide_number == num else ""
            branded = self.brand_engine.create_carousel_slide(
                slide_number=cfg.slide_number,
                total_slides=num,
                heading=heading,
                body_text=body,
                background_image=raw_bytes,
            )

        return GeneratedSlide(
            slide_number=cfg.slide_number,
            image_bytes=branded,
            image_source=cfg.image_source,
            search_query=cfg.search_query,
            gen_prompt=cfg.gen_prompt,
        )

    async def export_template(self, post: GeneratedPost) -> bytes:
        return await self.exporter.export_package(
            images=post.images,
            caption=post.caption,
            hashtags=post.hashtags,
            post_name=post.topic[:50],
        )

    @staticmethod
    def _build_default_slide_configs(
        num: int,
        image_source: ImageSource,
        search_queries: list[str],
        gen_prompts: list[str],
        image_model: Optional[str],
    ) -> list[SlideImageConfig]:
        configs = []
        for i in range(1, num + 1):
            idx = i - 1
            configs.append(SlideImageConfig(
                slide_number=i,
                image_source=image_source,
                search_query=search_queries[idx] if idx < len(search_queries) else f"slide {i}",
                gen_prompt=gen_prompts[idx] if idx < len(gen_prompts) else None,
                gen_model=image_model,
            ))
        return configs
