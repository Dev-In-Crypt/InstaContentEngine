"""LLM-powered adapter that turns a competitor's trending media into a
My-Life-My-Game-branded post idea (hook + short script + shot list + caption + ...).

Mirrors the JSON-envelope pattern of `services/caption_generator.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from models.schemas import LengthTier, Platform
from services.caption_generator import LENGTH_TIER_INSTRUCTIONS
from services.openrouter import OpenRouterClient


MLMG_BRAND_VOICE = (
    "The brand is My Life My Game. Motto: Life is a game, play it well. "
    "Audience: busy professionals, runners, fitness lovers, parents, and people who "
    "want to improve health, productivity, discipline, and lifestyle."
)


_JSON_FORMAT = """\
RESPOND IN THIS EXACT JSON FORMAT (no markdown, no code fences):
{{
    "hook": "Scroll-stopping first line (max 12 words). No hashtags.",
    "short_script": "Line 1\\nLine 2\\nLine 3 -- 3 to 6 short script lines for the creator to shoot",
    "shot_list": ["shot 1 description", "shot 2 description", "..."],
    "caption": "Full caption body, following the brand voice and length tier below.",
    "cta": "A single call-to-action sentence.",
    "hashtags": ["#hashtag1", "#hashtag2"],
    "seo_keywords": ["keyword one", "keyword two"]
}}"""


_SYSTEM_PROMPT = """\
You are a content strategist for My Life My Game (running, fitness, healthy habits,
productivity). You will be shown a TRENDING piece of content from a competitor in
the same niche. Your job is to ADAPT the winning idea into the My Life My Game voice,
NOT to copy it.

BRAND VOICE:
{brand_voice}
Target platform: {platform}.
Tone: inspiring, practical, human. Avoid AI-sounding language, exaggerated claims,
and motivational clichés. No em dashes. Few emojis. Short paragraphs.

Produce, in the JSON fields below:
- "hook": a stronger version of the competitor's first 2 seconds, in our voice.
- "short_script": 3-6 short script lines describing what the creator says/shows
  on camera, in order. Use newline separators inside the string.
- "shot_list": each item is a concrete shot to film (camera angle, action,
  framing). 3-6 items. Plain English, no jargon.
- "caption": the post caption body that will appear under the video/image.
  Naturally include 1-2 relevant keywords in the first 2-3 lines.
- "cta": a single, soft call-to-action sentence.
- "hashtags": 12-18 hashtags (mix of broad, niche, community).
- "seo_keywords": 8-12 SEO keywords (SEPARATE from hashtags) that help the post
  surface in Instagram search.

{length_instruction}

RULES:
- Be specific to the topic. Do not just rewrite the competitor's caption verbatim.
- If the competitor's idea would not fit our brand (off-topic, controversial,
  not aligned), pivot to the closest on-brand angle.
- Output must be ready to copy and paste. No em dashes.

{json_format}
"""


_USER_PROMPT = """\
TRENDING POST CONTEXT
Source handle: @{source_handle}
Media type: {media_type}
Permalink: {permalink}
Approx engagement -- likes: {likes}, comments: {comments}, views: {views}
Competitor caption hook (first line): {source_hook}

Full competitor caption:
{caption}

Target platform: {platform}
Length tier: {length_tier}
Additional instructions from user: {additional_instructions}
"""


@dataclass
class AdaptedIdea:
    hook: str
    short_script: str
    shot_list: list[str]
    caption: str
    cta: str
    hashtags: list[str]
    seo_keywords: list[str] = field(default_factory=list)
    raw_response: str = field(default="", repr=False)


class TrendAdaptError(Exception):
    pass


class TrendAdapter:
    def __init__(self, openrouter: OpenRouterClient):
        self.openrouter = openrouter

    async def adapt(
        self,
        *,
        source_handle: str,
        media_type: str,
        permalink: Optional[str],
        caption: Optional[str],
        source_hook: Optional[str],
        likes: int = 0,
        comments: int = 0,
        views: Optional[int] = None,
        platform: Platform = Platform.INSTAGRAM,
        length_tier: LengthTier = LengthTier.SWEET_SPOT,
        additional_instructions: Optional[str] = None,
        text_model: str = "",
        brand_voice: str = MLMG_BRAND_VOICE,
    ) -> AdaptedIdea:
        system = _SYSTEM_PROMPT.format(
            brand_voice=brand_voice,
            platform=platform.value,
            length_instruction=LENGTH_TIER_INSTRUCTIONS[length_tier],
            json_format=_JSON_FORMAT,
        )
        user = _USER_PROMPT.format(
            source_handle=source_handle,
            media_type=media_type,
            permalink=permalink or "n/a",
            likes=likes,
            comments=comments,
            views="n/a" if views is None else views,
            source_hook=source_hook or "(no caption hook available)",
            caption=(caption or "(empty)")[:4000],
            platform=platform.value,
            length_tier=length_tier.value,
            additional_instructions=additional_instructions or "None",
        )
        max_tokens = 3000 if length_tier == LengthTier.DEEP_DIVE else 2000
        # Trend adapter doesn't need web grounding — the source is the competitor's
        # post itself. Drop annotations.
        raw, _citations = await self.openrouter.generate_text(
            model=text_model,
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
        )
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> AdaptedIdea:
        text_ = raw.strip()
        text_ = re.sub(r"^```(?:json)?\s*", "", text_)
        text_ = re.sub(r"\s*```$", "", text_)
        try:
            data = json.loads(text_)
        except json.JSONDecodeError as e:
            raise TrendAdaptError(
                f"Could not parse JSON from model response: {e}\n\nRaw:\n{raw}"
            ) from e

        required = ("hook", "short_script", "shot_list", "caption", "cta", "hashtags")
        for key in required:
            if key not in data:
                raise TrendAdaptError(f"Missing required field '{key}' in model response")

        shot_list = data["shot_list"]
        if isinstance(shot_list, str):
            # Tolerate the rare model that returns a newline-joined string.
            shot_list = [line.strip(" -•\t") for line in shot_list.splitlines() if line.strip()]

        return AdaptedIdea(
            hook=str(data["hook"]),
            short_script=str(data["short_script"]),
            shot_list=[str(s) for s in shot_list],
            caption=str(data["caption"]),
            cta=str(data["cta"]),
            hashtags=[str(h) for h in (data["hashtags"] or [])],
            seo_keywords=[str(k) for k in (data.get("seo_keywords") or [])],
            raw_response=raw,
        )
