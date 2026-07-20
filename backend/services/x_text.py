"""Keeping X posts inside the character budget without mangling them.

A tweet has a hard limit, and models routinely overshoot it. Cutting at the limit
mid-word (`text[:250]`) is what we used to do — it produces "…for filter cof" and
can slice a hashtag in half. Instead:

  1. ask the model to shorten while preserving the meaning, then
  2. if it still overshoots, cut on a word boundary and add an ellipsis.

Step 2 alone is the safety net; step 1 is what keeps the text readable. Pure
functions here so the rules are testable without touching a provider.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Optional

from models.schemas import TWEET_CHAR_LIMIT

#: Signature of the "make this shorter" helper (CaptionGenerator.shorten_text).
Shortener = Callable[[str, int], Awaitable[str]]

_ELLIPSIS = "…"


def fit_tweet(text: str, limit: int = TWEET_CHAR_LIMIT) -> str:
    """Return `text` guaranteed to be <= limit, never splitting a word.

    Short text is returned untouched. Over-long text is cut back to the last
    whole word that leaves room for an ellipsis. A single word longer than the
    limit is the one case we must cut mid-word — there is no boundary to use.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    head = text[: limit - len(_ELLIPSIS)]
    cut = head.rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    if not cut:                       # one unbroken token longer than the limit
        cut = text[: limit - len(_ELLIPSIS)]
    return cut + _ELLIPSIS


#: [label](https://…) — the form web-grounded models reach for by default.
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
#: **bold** / __bold__ / *italic* / _italic_ / `code`, paired around real text.
#: The lookarounds keep it off word-internal markers — "file_name.py" and "snake_case"
#: are not emphasis, and "3 * 4" is arithmetic.
_MD_EMPHASIS = re.compile(r"(?<![\w*_`])(\*\*|__|\*|_|`)(\S(?:[^\n]*?\S)?)\1(?![\w*_`])")
#: "## Heading" at the start of a line.
_MD_HEADING = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Flatten markdown that X would publish literally.

    X renders no markdown, so a grounded model's "[JAMA study](https://…)" goes out
    with the brackets showing. Links keep their URL — naming a source without letting
    the reader check it is worse than a few characters spent — everything else loses
    only its markers. List dashes stay: they read fine on X.
    """
    text = text or ""
    text = _MD_LINK.sub(r"\1 (\2)", text)
    text = _MD_HEADING.sub("", text)
    # Twice: "**_word_**" needs an inner pass. Bounded, so no runaway loop.
    for _ in range(2):
        text = _MD_EMPHASIS.sub(r"\2", text)
    return text


def append_tags(text: str, tags: str, limit: Optional[int] = TWEET_CHAR_LIMIT) -> str:
    """Attach the hashtags to a tweet, shortening the BODY if they don't fit.

    The hashtags are the one part that must survive intact — a cut that lands
    inside "#FitnessOver40" publishes a different tag. So when the pair overflows,
    the text gives way, not the tags. `limit=None` is the X Premium long post,
    where no cap applies.
    """
    text = (text or "").strip()
    tags = (tags or "").strip()
    if not tags:
        return text
    if not text:
        return tags
    if limit is None or len(text) + 2 + len(tags) <= limit:
        return f"{text}\n\n{tags}"
    return f"{fit_tweet(text, limit - len(tags) - 2)}\n\n{tags}"


def clamp_count(parts: list[str], lo: int, hi: int) -> list[str]:
    """Bound how many tweets a thread has.

    Trims a too-long thread to `hi`. Deliberately does NOT pad a short one up to
    `lo`: inventing filler tweets to hit a number is exactly what breaks the
    "each tweet continues the previous, reads as one piece" requirement. A model
    that answers a narrow topic in fewer tweets is right, not wrong.
    """
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return cleaned[:hi] if hi and len(cleaned) > hi else cleaned


async def enforce_parts(
    parts: list[str],
    shorten: Optional[Shortener] = None,
    limit: int = TWEET_CHAR_LIMIT,
) -> list[str]:
    """Bring every part inside `limit`, preferring a model rewrite over a cut.

    Parts already within budget are left exactly as they are, so a well-behaved
    model costs nothing extra.
    """
    out: list[str] = []
    for part in parts:
        part = (part or "").strip()
        if len(part) <= limit:
            out.append(part)
            continue
        if shorten is not None:
            try:
                rewritten = (await shorten(part, limit) or "").strip()
                if rewritten:
                    part = rewritten
            except Exception:
                # A failed rewrite must not fail the whole post — fall through to
                # the deterministic cut below.
                pass
        out.append(fit_tweet(part, limit))
    return out


def looks_truncated(text: str) -> bool:
    """True if a tweet appears to end mid-thought — used by tests and as a signal
    that the prompt (not the cutter) needs work."""
    stripped = (text or "").rstrip()
    return stripped.endswith(_ELLIPSIS) or stripped.endswith("...")
