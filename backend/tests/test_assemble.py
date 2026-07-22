"""Reel assembly mux (Reels R1) — runs the real ffmpeg like test_kenburns does.

Tiny inputs keep it fast: a 0.2s one-slide render, a 0.3s generated tone, a
two-line ASS. The assertion that the output actually HAS an audio stream is the
mutation guard — drop the -map 1:a / -c:a aac and it fails.
"""
import asyncio
import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from services.subtitles import Chunk, write_ass
from services.tts import ffmpeg_exe
from services.video.assemble import mux_reel_sync
from services.video.base import VideoError
from services.video.kenburns import KenBurnsVideoProvider


def _slide(color):
    img = Image.new("RGB", (1080, 1350), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _tone_wav(path: Path, seconds: float) -> Path:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-ac", "1", str(path)], capture_output=True, check=True)
    return path


def _streams(path: Path) -> str:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, errors="replace")
    return proc.stderr


def test_mux_burns_subs_and_adds_audio(tmp_path):
    video = tmp_path / "silent.mp4"
    video.write_bytes(asyncio.run(
        KenBurnsVideoProvider().make_reel([_slide("red")], duration_per=0.2)))
    audio = _tone_wav(tmp_path / "tone.wav", 0.3)
    ass = tmp_path / "subs.ass"
    ass.write_text(write_ass([Chunk(text="HELLO", start=0.0, end=0.2)]),
                   encoding="utf-8")
    out = tmp_path / "reel.mp4"

    mux_reel_sync(video, audio, ass, out)

    assert out.exists() and out.stat().st_size > 0
    info = _streams(out)
    assert "Audio:" in info            # mutation guard: the voice track is there
    assert "Video:" in info


def test_mux_without_subs_copies_video(tmp_path):
    video = tmp_path / "silent.mp4"
    video.write_bytes(asyncio.run(
        KenBurnsVideoProvider().make_reel([_slide("blue")], duration_per=0.2)))
    audio = _tone_wav(tmp_path / "tone.wav", 0.3)
    out = tmp_path / "reel.mp4"
    mux_reel_sync(video, audio, None, out)
    assert out.exists() and "Audio:" in _streams(out)


def test_mux_bad_input_raises(tmp_path):
    bad = tmp_path / "nope.mp4"
    bad.write_bytes(b"not a video")
    audio = _tone_wav(tmp_path / "tone.wav", 0.2)
    with pytest.raises(VideoError):
        mux_reel_sync(bad, audio, None, tmp_path / "out.mp4")
