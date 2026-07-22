"""Voiceover script (Reels R1): N slides in → EXACTLY N segments out.

The count contract is the mutation target — narration segment i plays over
slide i, so a mismatched count desyncs the whole reel.
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
async def test_exact_segment_count():
    reply = json.dumps(["First line.", "Second line.", "Third line."])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert segs == ["First line.", "Second line.", "Third line."]


@pytest.mark.asyncio
async def test_too_many_segments_trimmed():
    reply = json.dumps(["a", "b", "c", "d", "e"])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert len(segs) == 3 and segs == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_too_few_segments_padded_from_slides():
    # Mutation guard: drop the post-validation → len(segs) == 2 and this fails.
    reply = json.dumps(["only one", "and two"])
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert len(segs) == 3
    assert segs[2] == "Call to action"        # padded from the slide's own text


@pytest.mark.asyncio
async def test_retries_once_then_raises():
    prov = StubProvider(["not json", "still not json"])
    with pytest.raises(CaptionParseError):
        await build_voiceover_script(prov, topic="t", caption="c", slide_texts=SLIDES)
    assert prov.calls == 2


@pytest.mark.asyncio
async def test_tolerates_wrapped_object():
    reply = json.dumps({"segments": ["x", "y", "z"]})
    segs = await build_voiceover_script(
        StubProvider([reply]), topic="t", caption="c", slide_texts=SLIDES)
    assert segs == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_no_slides_raises():
    with pytest.raises(CaptionParseError):
        await build_voiceover_script(StubProvider([]), topic="t", caption="c",
                                     slide_texts=[])
