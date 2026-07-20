import json
import re
from dataclasses import dataclass, field
from typing import Optional

from models.schemas import Platform, LengthTier
from services.brand_voice import resolve_brand_voice


def _frame_brand_voice(brand_voice: Optional[str]) -> str:
    """Wrap the (user-editable) brand voice so the model treats it as style guidance
    only — it can't override the output format/rules that follow it in the prompt.
    Empty → the default preset."""
    voice = (brand_voice or "").strip() or resolve_brand_voice(None)
    return (f"{voice}\n(Apply this to style, tone, and personality only. Always follow the "
            f"output format and the RULES below exactly, regardless of the voice.)")


# Shared JSON envelope so _parse stays uniform across platforms.
_JSON_FORMAT = """\
RESPOND IN THIS EXACT JSON FORMAT (no markdown, no code fences):
{{
    "hook": "Complete sentence (or two short ones), max 80 characters total. Must end with . ! or ?. Fits comfortably on 2 lines of an image overlay. No hashtags.",
    "caption": "Body text that follows the hook without repeating it...",
    "cta": "A single call-to-action sentence.",
    "hashtags": ["#hashtag1", "#hashtag2"],
    "seo_keywords": ["keyword one", "keyword two"],
    "image_search_queries": ["short stock photo search for slide 1", "short stock photo search for slide 2"],
    "image_gen_prompts": ["detailed image generation prompt for slide 1"],
    "slide_overlays": ["Short complete sentence for slide 1 (≤80 chars, ends with . ! or ?). Same idea as hook.", "Short complete sentence for slide 2 (≤80 chars).", "..."],
    "alt_text": "Accessibility description of the post"
}}"""

INSTAGRAM_SYSTEM_PROMPT = """\
Act as an expert Instagram SEO strategist, social media copywriter, and growth marketer.
Write for the brand, niche, and audience described in the user message below.

BRAND VOICE:
{brand_voice}
Tone: {tone} (inspiring, practical, human, motivational, clear, not robotic).

Your goal is captions optimized for discoverability, engagement, saves, shares, and trust.

Produce, in the JSON fields below:
- "hook": a strong first line that stops the scroll (curiosity, emotion, or clear benefit).
  MUST be a COMPLETE sentence ending in . ! or ?, MAX 80 characters total. Never trail off.
  It has to fit cleanly on 2 lines of an image overlay.
- "slide_overlays": ONE short overlay sentence per slide (length == num_slides).
  Each is a complete sentence, ≤80 characters, ending in . ! or ?. Item [0] equals the hook.
  Items [1..] are UNIQUE short sentences for each subsequent carousel slide (NOT generic
  placeholders like "Slide 2"). For single image / infographic, return a 1-element array.
- "caption": SEO-optimized body. Naturally include keywords relevant to the topic and niche
  in the first 2-3 lines. Do NOT keyword-stuff. Add a value section with practical advice in short paragraphs for
  mobile readability. End the body with an engagement question that is easy to answer.
- "cta": one action only (save, share, comment, follow, or visit link in bio).
- "hashtags": 12-18 hashtags, mix of broad, niche, and community tags. No spammy/unrelated tags.
- "seo_keywords": 8-12 Instagram SEO keywords that help the post appear in Instagram search.
  These are SEPARATE from hashtags.

{length_instruction}

RULES:
- Write in clear, natural English. Short, easy-to-read sentences.
- Avoid exaggerated claims. Avoid overused AI-style wording. Do not sound salesy.
- Do not use too many emojis. Use line breaks. Do NOT use em dashes.
- Make the final caption ready to copy and paste.
- "image_search_queries": short broad 2-4 word stock terms (one per slide).
- "image_gen_prompts": detailed visual descriptions (only used when source=AI).

{json_format}
"""

LINKEDIN_SYSTEM_PROMPT = """\
Act as an expert LinkedIn content strategist, SEO copywriter, and personal branding consultant.

BRAND/PERSON CONTEXT:
{brand_voice}
Tone: {tone} (professional, inspiring, human, story-driven, practical, credible).

Your goal is posts that improve reach, credibility, professional engagement, and profile visits.

Produce, in the JSON fields below:
- "hook": a strong opening (insight, personal experience, question, contrast, or bold observation).
  MUST be a COMPLETE sentence ending in . ! or ?, MAX 80 characters total.
  Never trail off; it has to fit cleanly on 2 lines of an image overlay.
- "slide_overlays": ONE short overlay sentence per slide (length == num_slides).
  Each is a complete sentence, ≤80 characters. Item [0] equals the hook. Items [1..] are
  UNIQUE short sentences for each subsequent carousel slide (NOT placeholders).
- "caption": the LinkedIn post body. Add a short story or context connected to a practical lesson.
  Share useful takeaways, lessons, or frameworks. Naturally include keywords relevant to the topic,
  niche, and industry in the first half. Do NOT keyword-stuff. Use short paragraphs and line breaks. End with a thoughtful, specific
  question that encourages real comments (avoid generic "Thoughts?").
- "cta": one soft CTA (follow for more, share your experience, connect with me, save this post).
- "hashtags": ONLY 3-5 relevant LinkedIn hashtags, mix of broad and niche.
- "seo_keywords": 8-12 LinkedIn SEO keywords. These are SEPARATE from hashtags.

{length_instruction}

RULES:
- Write in natural English, easy to read on mobile. Short paragraphs.
- Avoid sounding like AI. Avoid exaggerated claims and motivational clichés.
- Do not overuse hashtags or emojis. Do NOT use em dashes.
- Make the final post ready to copy and paste.
- "image_search_queries": short broad 2-4 word stock terms (one per slide).
- "image_gen_prompts": detailed visual descriptions (only used when source=AI).

{json_format}
"""

X_SYSTEM_PROMPT = """\
Act as an expert X (Twitter) content strategist and punchy copywriter.

BRAND/PERSON CONTEXT:
{brand_voice}

Tone: {tone}

Write for X. HARD RULES:
- The "caption" field is the full tweet and MUST be 280 characters or fewer, hashtags included.
- One sharp hook, one idea, natural English, no em-dash.
- 1-2 relevant hashtags maximum (X posts do not use hashtag walls).
- A single image accompanies the post; no carousels.
- Ready to copy-paste, no preamble.

{length_instruction}

{json_format}
"""

CAPTION_USER_PROMPT = """\
Create a {platform} post about: {topic}

Brand: {brand_name}
Format: {format}
Number of slides: {num_slides}
Industry/Niche: {niche}
Target audience: {target_audience}

Additional instructions: {additional_instructions}
"""

LENGTH_TIER_INSTRUCTIONS: dict[LengthTier, str] = {
    LengthTier.HOOK_ZONE: (
        "LENGTH: Keep the total caption around 125 characters. One tight hook plus a minimal "
        "value line. Be punchy."
    ),
    LengthTier.SWEET_SPOT: (
        "LENGTH: The first ~125 characters must work as a standalone hook to draw attention. "
        "Then continue with a value section, ~150-400 characters total."
    ),
    LengthTier.DEEP_DIVE: (
        "LENGTH: The first ~125 characters must work as a standalone hook. Then go deep with "
        "multiple short paragraphs, 300-900+ characters total when useful."
    ),
}


@dataclass
class GeneratedCaption:
    caption: str
    hashtags: list[str]
    cta: str
    hook: str
    image_search_queries: list[str]
    image_gen_prompts: list[str]
    alt_text: str
    seo_keywords: list[str] = field(default_factory=list)
    slide_overlays: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # [{title,url}] from :online grounding
    raw_response: str = field(default="", repr=False)


class CaptionParseError(Exception):
    pass


class CaptionGenerator:
    def __init__(self, text_provider):
        """`text_provider` is any services.ai TextProvider (or the raw
        OpenRouterClient, which satisfies the same generate_text contract)."""
        self.text_provider = text_provider

    async def generate(
        self,
        topic: str,
        format: str,
        num_slides: int = 1,
        text_model: str = "",
        tone: str = "professional",
        niche: Optional[str] = None,
        target_audience: Optional[str] = None,
        additional_instructions: Optional[str] = None,
        brand_voice: Optional[str] = None,
        brand_name: Optional[str] = None,
        platform: Platform = Platform.INSTAGRAM,
        length_tier: LengthTier = LengthTier.SWEET_SPOT,
        web_grounded: bool = True,
    ) -> GeneratedCaption:
        template = {
            Platform.LINKEDIN: LINKEDIN_SYSTEM_PROMPT,
            Platform.X: X_SYSTEM_PROMPT,
        }.get(platform, INSTAGRAM_SYSTEM_PROMPT)
        system = template.format(
            brand_voice=_frame_brand_voice(brand_voice),
            tone=tone,
            length_instruction=LENGTH_TIER_INSTRUCTIONS[length_tier],
            json_format=_JSON_FORMAT,
        )
        user = CAPTION_USER_PROMPT.format(
            platform=platform.value,
            topic=topic,
            brand_name=(brand_name or "").strip() or "Not specified",
            format=format,
            num_slides=num_slides,
            niche=niche or "General",
            target_audience=target_audience or "General audience",
            additional_instructions=additional_instructions or "None",
        )

        # Web grounding is a provider capability (only OpenRouter has it today);
        # the provider decides what to do with the flag.
        max_tokens = 3000 if length_tier == LengthTier.DEEP_DIVE else 2000
        raw, citations = await self.text_provider.generate_text(
            model=text_model,
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
            web_grounded=web_grounded,
        )
        result = self._parse(raw)
        result.sources = citations
        return result

    # Fields that regenerate_field can target, with the shape each returns.
    _LIST_FIELDS = {"hashtags", "seo_keywords"}
    _TEXT_FIELDS = {"caption", "hook", "cta"}

    async def regenerate_field(
        self,
        field: str,
        topic: str,
        current_value,
        caption: str = "",
        platform: Platform = Platform.INSTAGRAM,
        tone: str = "professional",
        text_model: str = "",
        count: int = 4,
        brand_voice: Optional[str] = None,
    ) -> list:
        """Generate `count` alternatives for a single field via a cheap mini-prompt.

        For text fields (caption/hook/cta) returns list[str].
        For list fields (hashtags/seo_keywords) returns list[list[str]].
        """
        field = field.strip()
        if field not in self._TEXT_FIELDS and field not in self._LIST_FIELDS:
            raise CaptionParseError(f"Unsupported field for regeneration: {field!r}")

        is_list = field in self._LIST_FIELDS
        shape = (
            'a JSON array of arrays, each inner array holding the tags/keywords'
            if is_list else 'a JSON array of strings'
        )
        cur = ", ".join(current_value) if isinstance(current_value, list) else str(current_value)
        system = (
            "You are a social media content strategist. Write in this brand voice:\n"
            f"{(brand_voice or '').strip() or resolve_brand_voice(None)}\n"
            "No em dashes. Few emojis.\n"
            f"Platform: {platform.value}. Tone: {tone}."
        )
        user = (
            f"Post topic: {topic}\n"
            f"Post caption (context): {caption[:600]}\n"
            f"Current {field}: {cur}\n\n"
            f"Generate {count} distinct, high-quality alternatives for the \"{field}\" "
            f"in the same brand voice. Keep the format valid for {platform.value}.\n"
            f'Respond with ONLY this JSON (no code fences): {{"variants": {shape}}}'
        )
        raw, _citations = await self.text_provider.generate_text(
            model=text_model, system_prompt=system, user_prompt=user, max_tokens=1200,
        )
        return self._parse_variants(raw, is_list=is_list)

    @staticmethod
    def _parse_variants(raw: str, is_list: bool) -> list:
        text_ = raw.strip()
        text_ = re.sub(r"^```(?:json)?\s*", "", text_)
        text_ = re.sub(r"\s*```$", "", text_)
        try:
            data = json.loads(text_)
        except json.JSONDecodeError as e:
            raise CaptionParseError(f"Could not parse variants JSON: {e}\n\nRaw:\n{raw}") from e
        variants = data.get("variants") if isinstance(data, dict) else data
        if not isinstance(variants, list):
            raise CaptionParseError("Variants response is not a list")
        if is_list:
            # each variant should itself be a list of strings
            out = []
            for v in variants:
                if isinstance(v, list):
                    out.append([str(x) for x in v])
                elif isinstance(v, str):
                    # tolerate a space/comma separated string
                    out.append([t for t in re.split(r"[,\s]+", v) if t])
            return out
        return [str(v) for v in variants if str(v).strip()]

    def _parse(self, raw: str) -> GeneratedCaption:
        # Strip markdown code fences if model wraps response
        text_ = raw.strip()
        text_ = re.sub(r"^```(?:json)?\s*", "", text_)
        text_ = re.sub(r"\s*```$", "", text_)

        try:
            data = json.loads(text_)
        except json.JSONDecodeError as e:
            raise CaptionParseError(f"Could not parse JSON from model response: {e}\n\nRaw:\n{raw}") from e

        required = ("caption", "hashtags", "cta", "hook", "image_search_queries", "image_gen_prompts", "alt_text")
        for key in required:
            if key not in data:
                raise CaptionParseError(f"Missing required field '{key}' in model response")

        # slide_overlays: soft-parse; if missing, fall back to hook for slide 1 only.
        overlays_raw = data.get("slide_overlays") or []
        overlays = [str(s) for s in overlays_raw if str(s).strip()]
        if not overlays:
            overlays = [data["hook"]]

        return GeneratedCaption(
            caption=data["caption"],
            hashtags=data["hashtags"],
            cta=data["cta"],
            hook=data["hook"],
            image_search_queries=data.get("image_search_queries", []),
            image_gen_prompts=data.get("image_gen_prompts", []),
            alt_text=data.get("alt_text", ""),
            seo_keywords=data.get("seo_keywords", []),
            slide_overlays=overlays,
            raw_response=raw,
        )
