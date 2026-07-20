import json
import re
from dataclasses import dataclass, field
from typing import Optional

from models.schemas import LengthTier, Platform, TWEET_CHAR_LIMIT, XPostMode
from services.brand_voice import resolve_brand_voice
from services.x_text import clamp_count, enforce_parts


def _frame_brand_voice(brand_voice: Optional[str]) -> str:
    """Wrap the (user-editable) brand voice so the model treats it as style guidance
    only — it can't override the output format/rules that follow it in the prompt.
    Empty → the default preset."""
    voice = (brand_voice or "").strip() or resolve_brand_voice(None)
    return (f"{voice}\n(Apply this to style, tone, and personality only. Always follow the "
            f"output format and the RULES below exactly, regardless of the voice.)")


# Shared JSON envelope so _parse stays uniform across platforms.
# Single braces on purpose: this string is substituted INTO the templates as a
# .format() *value*, so it is never itself formatted — doubling the braces would
# ship a literal "{{" to the model, which then copies it and emits invalid JSON.
_JSON_FORMAT = """\
RESPOND IN THIS EXACT JSON FORMAT (no markdown, no code fences):
{
    "hook": "Complete sentence (or two short ones), max 80 characters total. Must end with . ! or ?. Fits comfortably on 2 lines of an image overlay. No hashtags.",
    "caption": "Body text that follows the hook without repeating it...",
    "cta": "A single call-to-action sentence.",
    "hashtags": ["#hashtag1", "#hashtag2"],
    "seo_keywords": ["keyword one", "keyword two"],
    "image_search_queries": ["short stock photo search for slide 1", "short stock photo search for slide 2"],
    "image_gen_prompts": ["detailed image generation prompt for slide 1"],
    "slide_overlays": ["Short complete sentence for slide 1 (≤80 chars, ends with . ! or ?). Same idea as hook.", "Short complete sentence for slide 2 (≤80 chars).", "..."],
    "alt_text": "Accessibility description of the post"
}"""

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
- The "caption" field is the full tweet and MUST be 250 characters or fewer, hashtags included.
- One sharp hook, one idea, natural English, no em-dash.
- 1-2 relevant hashtags maximum (X posts do not use hashtag walls).
- A single image accompanies the post; no carousels.
- Ready to copy-paste, no preamble.

{length_instruction}

{json_format}
"""

X_THREAD_SYSTEM_PROMPT = """\
Act as an expert X (Twitter) thread writer.
Write for the brand, niche, and audience described in the user message below.

BRAND/PERSON CONTEXT:
{brand_voice}

Tone: {tone}

Write ONE thread. HARD RULES:
- Put the tweets in the "thread" field: a JSON array of strings, in reading order.
- Use between {thread_min} and {thread_max} tweets — pick the number the topic
  actually needs. Do NOT pad with filler to reach a number, and do NOT cram two
  ideas into one tweet to save space.
- Every tweet MUST be {tweet_limit} characters or fewer, hashtags and mentions included.
- Every tweet is a COMPLETE thought that ends on a finished sentence. NEVER split a
  sentence, a word, a link or a hashtag across two tweets.
- The thread must read as one continuous piece: each tweet carries on directly from
  the one before it, no restating and no standalone summaries in the middle.
- Tweet 1 is the hook. The last tweet lands the conclusion and the call to action.
- No "1/7" style numbering — X already shows the chain.
- Hashtags only in the LAST tweet, 1-2 of them. Max 1-2 emojis in the whole thread.
- Natural English, no em-dash, ready to copy-paste.
- "caption" must repeat the FIRST tweet verbatim (it is what previews show).

{length_instruction}

{json_format}

PLUS one extra field alongside the ones above, holding the tweets in order:
    "thread": ["First tweet, the hook.", "Second tweet, carrying straight on.", "Last tweet with the conclusion and CTA."]
"""

X_LONG_SYSTEM_PROMPT = """\
Act as an expert X (Twitter) long-form writer.
Write for the brand, niche, and audience described in the user message below.

BRAND/PERSON CONTEXT:
{brand_voice}

Tone: {tone}

Write ONE long-form X post (the account has X Premium, so the 280-character cap
does not apply). HARD RULES:
- The "caption" field is the whole post. Aim for 600-1500 characters.
- Open with a hook line that works on its own, then develop the idea in short
  paragraphs separated by blank lines. Mobile readers skim — keep paragraphs tight.
- One coherent argument from start to finish, no bullet-point dump.
- End with a conclusion and a single call to action.
- 1-2 hashtags at the very end only. Few emojis. No em-dash.
- Leave "thread" out entirely — this is a single post.

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
    thread_parts: list[str] = field(default_factory=list)  # X thread tweets, in order
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
        x_mode: XPostMode = XPostMode.SHORT,
        thread_min: int = 3,
        thread_max: int = 7,
    ) -> GeneratedCaption:
        if platform == Platform.X:
            template = {
                XPostMode.THREAD: X_THREAD_SYSTEM_PROMPT,
                XPostMode.LONG: X_LONG_SYSTEM_PROMPT,
            }.get(x_mode, X_SYSTEM_PROMPT)
        elif platform == Platform.LINKEDIN:
            template = LINKEDIN_SYSTEM_PROMPT
        else:
            template = INSTAGRAM_SYSTEM_PROMPT

        fields = dict(
            brand_voice=_frame_brand_voice(brand_voice),
            tone=tone,
            length_instruction=LENGTH_TIER_INSTRUCTIONS[length_tier],
            json_format=_JSON_FORMAT,
        )
        if template is X_THREAD_SYSTEM_PROMPT:
            fields.update(thread_min=thread_min, thread_max=thread_max,
                          tweet_limit=TWEET_CHAR_LIMIT)
        system = template.format(**fields)
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
        if platform == Platform.X and x_mode == XPostMode.THREAD:
            max_tokens = max(max_tokens, 400 * thread_max)   # room for every tweet
        raw, citations = await self.text_provider.generate_text(
            model=text_model,
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
            web_grounded=web_grounded,
        )
        result = self._parse(raw)
        result.sources = citations

        if platform == Platform.X and x_mode == XPostMode.THREAD and result.thread_parts:
            # Models overshoot the per-tweet budget and the requested count; fix both
            # here rather than trusting the prompt alone.
            parts = clamp_count(result.thread_parts, thread_min, thread_max)
            parts = await enforce_parts(
                parts,
                shorten=lambda text, limit: self.shorten_text(text, limit, text_model),
            )
            result.thread_parts = parts
            # `caption` is left as the model wrote it; content_engine joins the parts
            # for the stored caption so there is exactly one place that decides it.

        if platform == Platform.X and x_mode == XPostMode.SHORT:
            # "250 characters including everything" — the hashtags are appended to
            # the caption at publish time, so the caption's own budget is what's
            # left after them. Enforcing it here means the user sees the real post
            # in the preview instead of discovering a cut after publishing.
            tags = " ".join(result.hashtags or []).strip()
            budget = TWEET_CHAR_LIMIT - (len(tags) + 2 if tags else 0)
            if budget > 0 and len(result.caption or "") > budget:
                fixed = await enforce_parts(
                    [result.caption],
                    shorten=lambda text, limit: self.shorten_text(text, limit, text_model),
                    limit=budget,
                )
                result.caption = fixed[0]
        return result

    async def shorten_text(self, text: str, limit: int, text_model: str = "") -> str:
        """Ask the model to compress `text` under `limit` without losing the point.
        Used before falling back to a hard cut, so tweets stay readable."""
        raw, _ = await self.text_provider.generate_text(
            model=text_model,
            system_prompt="You tighten social copy without losing meaning.",
            user_prompt=(
                f"Rewrite the text below so it is at most {limit} characters, including "
                f"spaces, punctuation and hashtags. Keep the meaning and the tone. "
                f"Return ONLY the rewritten text, no quotes and no preamble.\n\n{text}"
            ),
            max_tokens=300,
        )
        return (raw or "").strip().strip('"')

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

        # thread: soft-parse — only X thread mode asks for it, everything else omits it.
        thread_raw = data.get("thread") or []
        thread_parts = [str(t).strip() for t in thread_raw if str(t).strip()]

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
            thread_parts=thread_parts,
            raw_response=raw,
        )
