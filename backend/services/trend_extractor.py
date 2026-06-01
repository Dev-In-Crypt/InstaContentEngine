"""Lightweight text/metric extractors for trending media (no LLM, no cost).

These give the UI something meaningful for every fetched media even before the
LLM-powered TrendAdapter is invoked.
"""

from __future__ import annotations

import re
from typing import Optional

_HASHTAG_RE = re.compile(r"#\w+", flags=re.UNICODE)
_URL_RE = re.compile(r"https?://\S+", flags=re.IGNORECASE)
# Greedy emoji / pictograph stripper for the BEGINNING of the hook line only.
_LEADING_NONWORD_RE = re.compile(r"^[\W_]+", flags=re.UNICODE)
# Simple imperative starters used by the CTA heuristic.
_CTA_STARTERS = (
    "follow", "save", "share", "comment", "tag", "subscribe", "like",
    "click", "tap", "visit", "check", "read", "watch", "download",
    "join", "sign", "register", "book", "buy", "shop", "learn",
    "discover", "try", "swipe", "dm", "send",
)

_MAX_HOOK_CHARS = 150


def extract_hashtags(caption: Optional[str]) -> list[str]:
    if not caption:
        return []
    seen = set()
    out: list[str] = []
    for m in _HASHTAG_RE.findall(caption):
        tag = m.lower()
        if tag not in seen:
            seen.add(tag)
            out.append(m)
    return out


def extract_hook(caption: Optional[str]) -> Optional[str]:
    """First meaningful sentence/line of caption, stripped of leading emoji/spaces
    and trailing hashtags. Capped at 150 chars."""
    if not caption:
        return None
    hashtag_only = re.compile(r"^(\s*#\w+\s*)+$")
    # Take the first non-empty, non-hashtag-only line.
    for raw in caption.splitlines():
        line = raw.strip()
        if not line:
            continue
        if hashtag_only.match(line):
            continue
        # Drop leading emoji / punctuation symbols.
        line = _LEADING_NONWORD_RE.sub("", line).strip()
        # Strip inline hashtags appearing at the END of the hook.
        line = re.sub(r"(\s+#\w+)+\s*$", "", line).strip()
        if not line:
            continue
        # Cut at first sentence boundary if reasonably long.
        match = re.search(r"(.+?[.!?])\s", line[: _MAX_HOOK_CHARS + 40])
        candidate = match.group(1) if match else line
        if len(candidate) > _MAX_HOOK_CHARS:
            candidate = candidate[: _MAX_HOOK_CHARS].rstrip() + "…"
        return candidate or None
    return None


def extract_cta(caption: Optional[str]) -> Optional[str]:
    """Heuristic: last non-hashtag line if it starts with an imperative verb
    or contains a URL / 'link in bio'."""
    if not caption:
        return None
    # Walk lines bottom-up.
    for raw in reversed(caption.splitlines()):
        line = raw.strip()
        if not line:
            continue
        # Skip lines that are only hashtags.
        if line.lstrip("#").replace(" ", "").isalnum() and line.startswith("#"):
            continue
        stripped_tags = _HASHTAG_RE.sub("", line).strip(" .,:;-—")
        if not stripped_tags:
            continue
        lowered = stripped_tags.lower()
        if (
            _URL_RE.search(stripped_tags)
            or "link in bio" in lowered
            or lowered.split()[0] in _CTA_STARTERS
        ):
            # Cap CTA length.
            return stripped_tags[:200]
    return None


def compute_engagement_score(
    likes: int, comments: int, views: Optional[int] = None
) -> float:
    """Absolute engagement score used for sorting trending media.

    - When `views` is known, use views as the dominant signal (Reels with high
      view count get a boost), with likes+comments as a tie-breaker.
    - Otherwise fall back to likes+comments.

    The score is not normalized against follower count (business_discovery does
    not give us follower counts of others reliably); ranking is relative.
    """
    likes = max(0, int(likes or 0))
    comments = max(0, int(comments or 0))
    if views is not None and views > 0:
        # Views dominate when available (a Reel signal). Likes/comments add nuance.
        return float(views) * 10 + likes + comments * 2
    return float(likes + comments * 2)   # comments weighted as a stronger signal
