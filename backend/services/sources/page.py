"""Generic page fetcher — a changelog or pricing page with no feed.

Many companies publish "what's new" as a plain HTML page. There's no per-item
date or id, so this splits the page on its headings (h1–h3) and treats each
section as an item, keyed by a hash of its heading + url. A blunt instrument on
purpose — it's the fallback when nothing structured exists.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from services.http_utils import describe_request_error
from services.sources.base import FetchedItem, SourceFetchError

_MAX_ITEMS = 30
_MAX_BODY = 600


class GenericPageFetcher:
    def __init__(self, ssl_verify: bool = True) -> None:
        self._ssl_verify = ssl_verify

    async def fetch(self, url: str, since: Optional[datetime] = None) -> list[FetchedItem]:
        try:
            async with httpx.AsyncClient(
                timeout=20.0, verify=self._ssl_verify, follow_redirects=True,
                headers={"User-Agent": "ContentEngine"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPStatusError as e:
            raise SourceFetchError(f"Page returned {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise SourceFetchError(describe_request_error(e, "Page")) from e

        soup = BeautifulSoup(html, "html.parser")
        items: list[FetchedItem] = []
        for heading in soup.find_all(["h1", "h2", "h3"]):
            title = " ".join(heading.get_text().split())
            if len(title) < 4:                       # skip empty/decorative headings
                continue
            anchor = heading.get("id")
            item_url = f"{url}#{anchor}" if anchor else url
            items.append(FetchedItem(
                external_id=hashlib.sha1(f"{item_url}:{title}".encode()).hexdigest()[:16],
                kind="generic_page",
                title=title,
                url=item_url,
                published_at=None,                   # generic pages have no per-item date
                body=_section_text(heading),
                raw={},
            ))
            if len(items) >= _MAX_ITEMS:
                break
        return items


def _section_text(heading) -> str:
    """Text of the siblings after a heading, up to the next heading of any level."""
    parts: list[str] = []
    for sib in heading.find_next_siblings():
        if getattr(sib, "name", None) in ("h1", "h2", "h3"):
            break
        text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else ""
        if text:
            parts.append(text)
        if sum(len(p) for p in parts) >= _MAX_BODY:
            break
    return " ".join(parts)[:_MAX_BODY].strip()
