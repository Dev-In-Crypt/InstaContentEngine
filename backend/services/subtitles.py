"""Burned-in subtitles for voiceover Reels — pure functions, no aligner.

We know each narration segment's exact audio duration (measured off the TTS WAV),
so instead of a heavy forced-alignment model (WhisperX needs torch — not for this
VPS) we split a segment into short ALL-CAPS chunks and spread the segment's time
across them proportionally to text length. Word-perfect karaoke it is not; on
1-2-sentence segments the drift is imperceptible.

Style lifted from the proven shorts-pipeline look: 1080x1920 playfield, big bold
outline text low in the frame. DejaVu Sans is present on dev and in the Docker
image (fonts-dejavu-core).
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_CHARS = 24
MIN_DUR = 0.4


@dataclass
class Chunk:
    text: str
    start: float   # seconds from reel start
    end: float


def _split_caps(text: str, max_chars: int) -> list[str]:
    """Greedy word-wrap into ALL-CAPS lines of at most max_chars."""
    words = " ".join((text or "").split()).upper().split(" ")
    out: list[str] = []
    cur = ""
    for w in words:
        if not w:
            continue
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_chars:
            out.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def chunk_segments(texts: list[str], durations: list[float], *,
                   max_chars: int = MAX_CHARS, min_dur: float = MIN_DUR) -> list[Chunk]:
    """Chunk each segment's text and time the chunks proportionally to length
    within that segment's duration. Segments butt up against each other on the
    cumulative timeline (durations already include any inter-segment gap)."""
    if len(texts) != len(durations):
        raise ValueError("texts and durations must have the same length")
    chunks: list[Chunk] = []
    clock = 0.0
    for text, dur in zip(texts, durations, strict=True):
        dur = max(0.0, float(dur))
        parts = _split_caps(text, max_chars)
        if not parts or dur <= 0:
            clock += dur
            continue
        total_chars = sum(len(p) for p in parts) or 1
        start = clock
        for i, p in enumerate(parts):
            share = dur * len(p) / total_chars
            length = max(min_dur, share) if dur >= min_dur * len(parts) else share
            end = start + length
            if i == len(parts) - 1:            # last chunk absorbs rounding drift
                end = clock + dur
            chunks.append(Chunk(text=p, start=round(start, 3), end=round(end, 3)))
            start = end
        clock += dur
    return chunks


def _ts(sec: float) -> str:
    """ASS timestamp H:MM:SS.cc"""
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(chunks: list[Chunk]) -> str:
    """Render chunks as an ASS document string (caller writes it to disk)."""
    lines = [_ASS_HEADER]
    for c in chunks:
        text = c.text.replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"
