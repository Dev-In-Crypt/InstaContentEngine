"""Business source fetchers + detection (Phase 1).

Each fetcher turns a mocked HTTP response into normalised FetchedItems. The date
parse is a mutation target: a fetcher that loses published_at silently breaks the
recency filter, so the tests pin the parsed datetime.
"""
import pytest
from pytest_httpx import HTTPXMock

from services.sources import SourceFetchError, detect_source_type, get_source_fetcher
from services.sources.base import parse_iso, strip_html
from services.sources.github import GitHubReleasesFetcher
from services.sources.feed import FeedFetcher
from services.sources.page import GenericPageFetcher
from datetime import datetime, timezone


# ── detection + helpers (pure) ───────────────────────────────────────────────

def test_detect_github_repo():
    assert detect_source_type("https://github.com/fastapi/fastapi") == "github_releases"
    assert detect_source_type("https://github.com/fastapi/fastapi/releases") == "github_releases"


def test_detect_feed():
    assert detect_source_type("https://blog.example.com/feed") == "rss"
    assert detect_source_type("https://example.com/index.atom") == "rss"


def test_detect_generic_fallback():
    assert detect_source_type("https://example.com/changelog") == "generic_page"
    assert detect_source_type("github.com") == "generic_page"   # no owner/repo


def test_parse_iso_and_strip_html():
    assert parse_iso("2026-07-01T12:00:00Z") == datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert parse_iso("nonsense") is None
    assert parse_iso("") is None
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_get_source_fetcher_rejects_unknown():
    with pytest.raises(SourceFetchError):
        get_source_fetcher("youtube")


# ── GitHub releases ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_fetch_parses_releases(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json=[
        {"id": 1, "tag_name": "v2.0", "name": "Version 2.0",
         "html_url": "https://github.com/o/r/releases/tag/v2.0",
         "published_at": "2026-07-01T12:00:00Z", "body": "Now 50% faster.", "draft": False},
        {"id": 2, "tag_name": "v1.0", "name": "", "html_url": "https://github.com/o/r/releases/tag/v1.0",
         "published_at": "2020-01-01T00:00:00Z", "body": "old", "draft": False},
    ])
    items = await GitHubReleasesFetcher().fetch("https://github.com/o/r")
    assert len(items) == 2
    top = items[0]
    assert top.title == "Version 2.0"
    assert top.url.endswith("/tag/v2.0")
    assert top.body == "Now 50% faster."
    # date parse is the mutation guard — a broken parser makes this None.
    assert top.published_at == datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert items[1].title == "v1.0"          # empty name falls back to tag


@pytest.mark.asyncio
async def test_github_since_filters_old_releases(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json=[
        {"id": 1, "tag_name": "new", "published_at": "2026-07-01T00:00:00Z", "draft": False},
        {"id": 2, "tag_name": "old", "published_at": "2020-01-01T00:00:00Z", "draft": False},
    ])
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = await GitHubReleasesFetcher().fetch("https://github.com/o/r", since=since)
    assert [i.raw["tag_name"] for i in items] == ["new"]


@pytest.mark.asyncio
async def test_github_skips_drafts(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json=[
        {"id": 1, "tag_name": "draft", "draft": True},
        {"id": 2, "tag_name": "real", "published_at": "2026-07-01T00:00:00Z", "draft": False},
    ])
    items = await GitHubReleasesFetcher().fetch("https://github.com/o/r")
    assert [i.raw["tag_name"] for i in items] == ["real"]


def test_github_rejects_non_repo_url():
    with pytest.raises(SourceFetchError):
        GitHubReleasesFetcher()._owner_repo("https://github.com/onlyowner")


@pytest.mark.asyncio
async def test_github_http_error_becomes_source_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=404)
    with pytest.raises(SourceFetchError):
        await GitHubReleasesFetcher().fetch("https://github.com/o/r")


# ── RSS/Atom feed ────────────────────────────────────────────────────────────

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>New pricing tiers</title>
    <link>https://ex.com/pricing</link>
    <guid>g1</guid>
    <pubDate>Wed, 01 Jul 2026 12:00:00 GMT</pubDate>
    <description>We changed &lt;b&gt;prices&lt;/b&gt;.</description>
  </item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_feed_fetch_parses_entries(httpx_mock: HTTPXMock):
    httpx_mock.add_response(content=_RSS.encode(), headers={"content-type": "application/rss+xml"})
    items = await FeedFetcher().fetch("https://ex.com/feed")
    assert len(items) == 1
    it = items[0]
    assert it.title == "New pricing tiers"
    assert it.url == "https://ex.com/pricing"
    assert "prices" in it.body and "<b>" not in it.body   # HTML flattened
    assert it.published_at.year == 2026 and it.published_at.month == 7


@pytest.mark.asyncio
async def test_feed_http_error_becomes_source_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=500)
    with pytest.raises(SourceFetchError):
        await FeedFetcher().fetch("https://ex.com/feed")


# ── Generic page ─────────────────────────────────────────────────────────────

_HTML = """<html><body>
  <h2 id="v2">Version 2.0 released</h2>
  <p>Now 50% faster and cheaper.</p>
  <h2>Bug fixes</h2>
  <p>Small stuff.</p>
</body></html>"""


@pytest.mark.asyncio
async def test_page_fetch_splits_on_headings(httpx_mock: HTTPXMock):
    httpx_mock.add_response(text=_HTML, headers={"content-type": "text/html"})
    items = await GenericPageFetcher().fetch("https://ex.com/changelog")
    assert [i.title for i in items] == ["Version 2.0 released", "Bug fixes"]
    assert items[0].url.endswith("#v2")            # heading id → anchor
    assert "faster" in items[0].body
    assert items[0].published_at is None           # generic pages have no per-item date


@pytest.mark.asyncio
async def test_page_http_error_becomes_source_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=503)
    with pytest.raises(SourceFetchError):
        await GenericPageFetcher().fetch("https://ex.com/changelog")
