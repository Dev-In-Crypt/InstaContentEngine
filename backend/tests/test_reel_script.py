"""Voiceover script (Reels R1/R2): N slides in → EXACTLY N segments out, each
with a non-empty b-roll shot query.

The count contract is the mutation target — narration segment i plays over
slide i, so a mismatched count desyncs the whole reel. The query fallback is the
second guard: a missing query must degrade to the narration text, never crash.
"""
import json

import pytest

from services.caption_generator import CaptionParseError
from services.reel_script import build_voiceover_script


class StubProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def generate_text(self, **kwargs):
        self.calls += 1
        return (self.replies.pop(0), [])


SLIDES = ["Hook text", "Point one", "Call to action"]


@pytest.mark.asyncio
async def test_exact_segment_count_with_queries():
    reply = json.dumps([
        {"text": "First line.", "query": "close up city sunrise"},
        {"text": "Second line.", "query": "macro coffee pour slow"},
        {"text": "Third line.", "query": "wide office team cheering"},
    ])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert [s.text for s in segs] == ["First line.", "Second line.", "Third line."]
    # mutation guard: every segment carries a non-empty query
    assert all(s.query for s in segs)
    assert segs[0].query == "close up city sunrise"


@pytest.mark.asyncio
async def test_bare_string_list_still_coerced():
    # pre-R2 shape: list of strings → query falls back to the narration text
    reply = json.dumps(["a line", "b line", "c line"])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert len(segs) == 3
    assert segs[0].text == "a line" and segs[0].query == "a line"


@pytest.mark.asyncio
async def test_too_many_segments_trimmed():
    reply = json.dumps([{"text": t, "query": "q"} for t in "abcde"])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert [s.text for s in segs] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_too_few_segments_padded_from_slides():
    # Mutation guard: drop the post-validation → len(segs) == 2 and this fails.
    reply = json.dumps([{"text": "only one", "query": "q1"},
                        {"text": "and two", "query": "q2"}])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert len(segs) == 3
    assert segs[2].text == "Call to action"      # padded from the slide's own text
    assert segs[2].query == "Call to action"


@pytest.mark.asyncio
async def test_retries_once_then_raises():
    prov = StubProvider(["not json", "still not json"])
    with pytest.raises(CaptionParseError):
        await build_voiceover_script(prov, topic="t", caption="c", slide_texts=SLIDES)
    assert prov.calls == 2


@pytest.mark.asyncio
async def test_tolerates_wrapped_object():
    reply = json.dumps({"segments": [{"text": "x", "query": "qx"},
                                     {"text": "y", "query": "qy"},
                                     {"text": "z", "query": "qz"}]})
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert [s.text for s in segs] == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_no_slides_raises():
    with pytest.raises(CaptionParseError):
        await build_voiceover_script(StubProvider([]), topic="t", caption="c",
                                     slide_texts=[])
