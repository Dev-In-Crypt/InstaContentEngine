"""Subtitle chunking + ASS writer (Reels R1) — pure functions.

The proportional timing is the mutation target: chunks must tile each segment's
duration exactly (no gaps, no overlap), or the burned text drifts off the voice.
"""
import pytest

from services.subtitles import Chunk, chunk_segments, write_ass


def test_chunks_are_caps_and_within_width():
    chunks = chunk_segments(["hello wonderful world of reels"], [5.0])
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert c.text == c.text.upper()
        assert len(c.text) <= 24


def test_timing_tiles_the_segment_exactly():
    chunks = chunk_segments(["one two three four five six seven eight nine ten"], [6.0])
    # starts are monotonic and contiguous
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.start == prev.end
    # mutation guard: break the proportional split → the last end drifts
    assert chunks[0].start == 0.0
    assert abs(chunks[-1].end - 6.0) < 1e-6


def test_two_segments_cumulative_clock():
    chunks = chunk_segments(["first segment", "second segment"], [2.0, 3.0])
    seg2_first = next(c for c in chunks if c.start >= 2.0 - 1e-9)
    assert abs(seg2_first.start - 2.0) < 1e-6      # second segment starts at t=2
    assert abs(chunks[-1].end - 5.0) < 1e-6


def test_empty_text_segment_advances_clock_silently():
    chunks = chunk_segments(["", "spoken"], [1.5, 1.0])
    assert all(c.start >= 1.5 - 1e-9 for c in chunks)   # nothing during silence
    assert abs(chunks[-1].end - 2.5) < 1e-6


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        chunk_segments(["a"], [1.0, 2.0])


def test_advance_durs_hugs_speech_not_slide():
    """Subs time to speech duration but the clock advances by the (longer) slide
    duration, so subtitles finish with the voice and don't linger through the
    silent gap. Mutation guard: ignore advance_durs (advance by speech) → segment
    2 starts at 2.0, not 2.5, and this fails."""
    speech = [2.0, 3.0]
    slide = [2.5, 3.5]                      # speech + a 0.5s gap
    chunks = chunk_segments(["first segment", "second segment"], speech,
                            advance_durs=slide)
    # segment 1's last chunk ends at its SPEECH end (2.0), inside the 2.5 slot
    seg1 = [c for c in chunks if c.start < 2.5 - 1e-9]
    assert abs(seg1[-1].end - 2.0) < 1e-6         # hugs speech, gap left silent
    # segment 2 starts at Σ slide[:1] == 2.5, not 2.0
    seg2 = [c for c in chunks if c.start >= 2.5 - 1e-9]
    assert abs(seg2[0].start - 2.5) < 1e-6
    assert abs(seg2[-1].end - 5.5) < 1e-6         # 2.5 + speech 3.0


def test_advance_durs_defaults_to_durations():
    """Without advance_durs the clock advances by `durations` — the original
    gap-free behaviour is preserved for any existing caller."""
    a = chunk_segments(["first segment", "second segment"], [2.0, 3.0])
    b = chunk_segments(["first segment", "second segment"], [2.0, 3.0],
                       advance_durs=[2.0, 3.0])
    assert [(c.text, c.start, c.end) for c in a] == \
           [(c.text, c.start, c.end) for c in b]


def test_advance_durs_length_mismatch_raises():
    with pytest.raises(ValueError):
        chunk_segments(["a", "b"], [1.0, 2.0], advance_durs=[1.0])


def test_write_ass_structure():
    doc = write_ass([Chunk(text="HELLO", start=0.0, end=1.25),
                     Chunk(text="WORLD", start=1.25, end=2.0)])
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc
    assert "Style: Default,DejaVu Sans" in doc
    assert "Dialogue: 0,0:00:00.00,0:00:01.25,Default,,0,0,0,,HELLO" in doc
    assert "Dialogue: 0,0:00:01.25,0:00:02.00,Default,,0,0,0,,WORLD" in doc


def test_write_ass_escapes_braces():
    doc = write_ass([Chunk(text="A {B} C", start=0, end=1)])
    assert "{" not in doc.split("[Events]")[1].split(",,")[-1] or "(B)" in doc
    assert "(B)" in doc     # ASS override braces neutralised
