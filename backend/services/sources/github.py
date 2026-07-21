"""GitHub Releases fetcher — public repos, no token (MVP discipline).

A release is the clearest "something shipped" signal a company emits, so it's the
first source kind. Reads the public Releases API; unauthenticated calls are rate
limited by GitHub per IP, which is fine for a demo and a low-frequency poller.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx

from services.http_utils import describe_request_error
from services.sources.base import FetchedItem, SourceFetchError, parse_iso

_API = "https://api.github.com"


class GitHubReleasesFetcher:
    def __init__(self, ssl_verify: bool = True) -> None:
        self._ssl_verify = ssl_verify

    @staticmethod
    def _owner_repo(url: str) -> tuple[str, str]:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(parts) < 2:
            raise SourceFetchError(f"Not a GitHub repository URL: {url!r}")
        return parts[0], parts[1]

    async def fetch(self, url: str, since: Optional[datetime] = None) -> list[FetchedItem]:
        owner, repo = self._owner_repo(url)
        try:
            async with httpx.AsyncClient(
                timeout=20.0, verify=self._ssl_verify,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "ContentEngine"},
            ) as client:
                resp = await client.get(
                    f"{_API}/repos/{owner}/{repo}/releases", params={"per_page": 30})
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise SourceFetchError(
                f"GitHub returned {e.response.status_code} for {owner}/{repo}") from e
        except httpx.RequestError as e:
            raise SourceFetchError(describe_request_error(e, "GitHub")) from e

        items: list[FetchedItem] = []
        for rel in data if isinstance(data, list) else []:
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            published = parse_iso(rel.get("published_at") or rel.get("created_at"))
            if since and published and published < since:
                continue
            tag = str(rel.get("tag_name") or "").strip()
            items.append(FetchedItem(
                external_id=str(rel.get("id") or tag),
                kind="github_releases",
                title=(str(rel.get("name") or "").strip() or tag or "Release"),
                url=str(rel.get("html_url") or url),
                published_at=published,
                body=str(rel.get("body") or "").strip(),
                raw={"tag_name": tag, "prerelease": bool(rel.get("prerelease"))},
            ))
        return items
