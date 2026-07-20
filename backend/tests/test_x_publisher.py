"""XPublisher against mocked X endpoints (no live API — needs a paid tier)."""
import re

import pytest
from pytest_httpx import HTTPXMock

from services.publishing.base import PublisherError
from services.publishing.x import MAX_CHARS, MAX_IMAGES, XPublisher

UPLOAD = re.compile(r"https://upload\.twitter\.com/1\.1/media/upload\.json")
TWEET = "https://api.twitter.com/2/tweets"


def _pub() -> XPublisher:
    return XPublisher("ck", "cs", "at", "ats")


async def test_publish_uploads_media_then_tweets(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=UPLOAD, json={"media_id_string": "m1"})
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "tw123", "text": "hi"}})

    pub = _pub()
    out = await pub.publish([b"jpegbytes"], "Run daily.")
    await pub.close()

    assert out.media_id == "tw123"
    assert out.permalink == "https://x.com/i/web/status/tw123"

    reqs = httpx_mock.get_requests()
    # Every request is OAuth1-signed (Authorization: OAuth ...).
    assert all(r.headers.get("Authorization", "").startswith("OAuth ") for r in reqs)
    # The tweet references the uploaded media id.
    import json
    tweet_body = json.loads([r for r in reqs if str(r.url) == TWEET][0].content)
    assert tweet_body["media"]["media_ids"] == ["m1"]
    assert tweet_body["text"] == "Run daily."


async def test_caption_truncated_to_280(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t", "text": ""}})
    pub = _pub()
    await pub.publish([], "A" * 500)
    await pub.close()

    import json
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert len(body["text"]) == MAX_CHARS


async def test_at_most_four_images(httpx_mock: HTTPXMock):
    for i in range(MAX_IMAGES):
        httpx_mock.add_response(url=UPLOAD, json={"media_id_string": f"m{i}"})
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t"}})

    pub = _pub()
    await pub.publish([b"a", b"b", b"c", b"d", b"e", b"f"], "cap")  # 6 → only 4 uploaded
    await pub.close()

    uploads = [r for r in httpx_mock.get_requests() if UPLOAD.match(str(r.url))]
    assert len(uploads) == MAX_IMAGES


async def test_tweet_error_raises_publisher_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=UPLOAD, json={"media_id_string": "m1"})
    httpx_mock.add_response(url=TWEET, status_code=403, text="not permitted")

    pub = _pub()
    with pytest.raises(PublisherError, match="X tweet failed"):
        await pub.publish([b"x"], "cap")
    await pub.close()


async def test_missing_tweet_id_raises(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=TWEET, json={"data": {}})
    pub = _pub()
    with pytest.raises(PublisherError, match="missing tweet id"):
        await pub.publish([], "cap")
    await pub.close()


# ── threads: a chain of replies, not N standalone tweets ────────────────────

async def test_publish_thread_chains_replies(httpx_mock: HTTPXMock):
    """Each tweet must reply to the previous one, or X shows three loose posts."""
    import json as _json
    httpx_mock.add_response(url=UPLOAD, json={"media_id_string": "m1"})
    for tid in ("t1", "t2", "t3"):
        httpx_mock.add_response(url=TWEET, json={"data": {"id": tid}})

    pub = _pub()
    out = await pub.publish_thread(["First.", "Second.", "Third."], [b"img"])
    await pub.close()

    tweets = [r for r in httpx_mock.get_requests() if str(r.url) == TWEET]
    bodies = [_json.loads(r.content) for r in tweets]
    assert len(bodies) == 3
    assert "reply" not in bodies[0]                                  # the hook opens the chain
    assert bodies[0]["media"] == {"media_ids": ["m1"]}               # image on the first only
    assert bodies[1]["reply"]["in_reply_to_tweet_id"] == "t1"
    assert bodies[2]["reply"]["in_reply_to_tweet_id"] == "t2"
    assert "media" not in bodies[1] and "media" not in bodies[2]
    assert out.media_id == "t1"                                      # thread identity = first tweet
    assert out.permalink.endswith("t1")


async def test_publish_thread_enforces_the_char_limit(httpx_mock: HTTPXMock):
    import json as _json
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t1"}})
    pub = _pub()
    await pub.publish_thread(["word " * 200], [])
    await pub.close()
    body = _json.loads(httpx_mock.get_requests()[0].content)
    assert len(body["text"]) <= MAX_CHARS


async def test_publish_thread_reports_partial_failure(httpx_mock: HTTPXMock):
    """X cannot roll back tweets already posted — say what went out."""
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t1"}})
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t2"}})
    httpx_mock.add_response(url=TWEET, status_code=403, text="duplicate content")

    pub = _pub()
    with pytest.raises(PublisherError) as err:
        await pub.publish_thread(["One.", "Two.", "Three."], [])
    await pub.close()

    msg = str(err.value)
    assert "2 of 3" in msg               # how many are live
    assert "t1" in msg                   # link to what exists
    assert "403" in msg                  # and why it stopped


async def test_publish_thread_rejects_empty(httpx_mock: HTTPXMock):
    pub = _pub()
    with pytest.raises(PublisherError, match="empty"):
        await pub.publish_thread(["", "   "], [])
    await pub.close()


async def test_long_form_publish_skips_the_cap(httpx_mock: HTTPXMock):
    """X Premium lifts the limit — a long post must not be cut."""
    import json as _json
    httpx_mock.add_response(url=TWEET, json={"data": {"id": "t1"}})
    long_text = "sentence. " * 200
    pub = _pub()
    await pub.publish([], long_text, long_form=True)
    await pub.close()
    body = _json.loads(httpx_mock.get_requests()[0].content)
    assert len(body["text"]) > MAX_CHARS
