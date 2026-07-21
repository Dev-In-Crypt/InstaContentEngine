"""Guess a source's kind from its URL.

A heuristic, not a promise — the fetcher for the guessed kind still validates.
GitHub repo links → releases; anything that looks like a feed → rss; everything
else → a generic page. Unknown/ambiguous falls through to generic_page, the most
forgiving fetcher.
"""
from __future__ import annotations

from urllib.parse import urlparse

_FEED_HINTS = ("/feed", "/rss", "/atom", "feed.xml", "rss.xml", "atom.xml")


def detect_source_type(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    if host in ("github.com", "www.github.com"):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:               # /owner/repo[/...] → its releases
            return "github_releases"

    if path.endswith((".rss", ".atom")) or any(h in path for h in _FEED_HINTS):
        return "rss"

    return "generic_page"
