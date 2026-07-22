"""Voiceover script for a Reel — one spoken segment per slide.

The narration must track the slides: segment i is read aloud while slide i is on
screen, so the count is a hard contract (N slides in → N segments out). The LLM
proposes the wording; we enforce the shape deterministically — a generation must
never fail because the model returned 4 lines for 5 slides.

Mirrors the lead_builder shape: injected text_provider, JSON via _loads with one
retry, pure post-processing that tests can hit without a network.
"""
from __future__ import annotations

import logging
from typing import Optional

from services.caption_generator import CaptionParseError
from services.lead_builder import _loads

log = logging.getLogger(__name__)

_SYSTEM = """\
You write short voiceover scripts for vertical social videos (Reels). The video
shows {n} slides in order; you write EXACTLY {n} spoken segments — segment i is
narrated while slide i is on screen.

Rules:
- Conversational, energetic, spoken-out-loud style. 1-2 short sentences per segment.
- Segment 1 hooks the viewer; the last segment ends with a light call to action.
- Write in the SAME LANGUAGE as the post text you are given.
- No hashtags, no emojis, no stage directions, no quotes around sentences.

Return ONLY a JSON array of {n} strings, no markdown:
["segment 1 text", "segment 2 text", ...]
"""


def _coerce(data: object, n: int, slide_texts: list[str]) -> Optional[list[str]]:
    """Force the model output into exactly n non-empty strings, or None if the
    payload is unusable. Too many → trim; too few → pad from the slide's own
    overlay text (better a plain line than a crashed render)."""
    if isinstance(data, dict):                     # tolerate {"segments": [...]}
        for k in ("segments", "script", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        return None
    segs = [" ".join(str(s).split()) for s in data if str(s).strip()]
    if not segs:
        return None
    segs = segs[:n]
    while len(segs) < n:
        fallback = (slide_texts[len(segs)] or "").strip() if len(segs) < len(slide_texts) else ""
        segs.append(fallback or segs[-1])
    return segs


async def build_voiceover_script(
    text_provider, *, topic: str, caption: str, slide_texts: list[str],
    text_model: str = "",
) -> list[str]:
    """Return exactly len(slide_texts) narration segments for the Reel."""
    n = len(slide_texts)
    if n == 0:
        raise CaptionParseError("No slides to narrate")

    slides_block = "\n".join(
        f"Slide {i + 1}: {t or '(image only)'}" for i, t in enumerate(slide_texts))
    user = (f"Post topic: {topic}\n\nPost caption:\n{caption}\n\n"
            f"Slides on screen:\n{slides_block}")

    async def _call() -> Optional[list[str]]:
        raw, _cit = await text_provider.generate_text(
            model=text_model, system_prompt=_SYSTEM.format(n=n),
            user_prompt=user, max_tokens=900)
        return _coerce(_loads(raw), n, slide_texts)

    segs = await _call()
    if segs is None:
        log.warning("Voiceover script unparseable; retrying once")
        segs = await _call()
    if segs is None:
        raise CaptionParseError("The model did not return a usable voiceover script.")
    return segs
