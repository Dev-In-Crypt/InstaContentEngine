"""Clip normalization + concat (Reels R2) — real ffmpeg on tiny lavfi clips.

The aspect-to-1080x1920 conversion is the mutation target: break either aspect
branch and the probed output size drifts.
"""
import subprocess
from pathlib import Path

import pytest

from services.tts import ffmpeg_exe
from services.video.base import VideoError
from services.video.normalize import (
    concat_clips_sync, normalize_clip_sync, probe_video,
)


def _clip(path: Path, seconds: float, size: str) -> Path:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-y", "-f", "lavfi",
                    "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset",
                    "ultrafast", str(path)], capture_output=True, check=True)
    return path


def test_probe_video(tmp_path):
    p = _clip(tmp_path / "a.mp4", 0.5, "320x180")
    w, h, dur = probe_video(p)
    assert (w, h) == (320, 180)
    assert 0.4 < dur < 0.7


def test_normalize_landscape_to_vertical(tmp_path):
    src = _clip(tmp_path / "land.mp4", 0.6, "320x180")
    dst = tmp_path / "out.mp4"
    normalize_clip_sync(src, dst, target_duration=0.4, segment_id=1)
    w, h, dur = probe_video(dst)
    # mutation guard: break the aspect branch → size drifts off 1080x1920
    assert (w, h) == (1080, 1920)
    assert 0.3 < dur < 0.55


def test_normalize_portrait_to_vertical(tmp_path):
    src = _clip(tmp_path / "port.mp4", 0.6, "180x320")
    dst = tmp_path / "out.mp4"
    normalize_clip_sync(src, dst, target_duration=0.4, segment_id=2)
    assert probe_video(dst)[:2] == (1080, 1920)


def test_normalize_short_source_padded(tmp_path):
    # 0.3s source stretched to a 0.6s segment via freeze-pad
    src = _clip(tmp_path / "short.mp4", 0.3, "320x180")
    dst = tmp_path / "out.mp4"
    normalize_clip_sync(src, dst, target_duration=0.6, segment_id=3)
    _w, _h, dur = probe_video(dst)
    assert dur >= 0.55        # tpad brought it up to target


def test_concat_two_clips(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    normalize_clip_sync(_clip(tmp_path / "s1.mp4", 0.4, "320x180"), a,
                        target_duration=0.3, segment_id=1)
    normalize_clip_sync(_clip(tmp_path / "s2.mp4", 0.4, "180x320"), b,
                        target_duration=0.3, segment_id=2)
    out = tmp_path / "cat.mp4"
    concat_clips_sync([a, b], out)
    w, h, dur = probe_video(out)
    assert (w, h) == (1080, 1920)
    assert 0.5 < dur < 0.75   # ≈ 0.3 + 0.3


def test_concat_empty_raises(tmp_path):
    with pytest.raises(VideoError):
        concat_clips_sync([], tmp_path / "x.mp4")


def test_probe_garbage_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    with pytest.raises(VideoError):
        probe_video(bad)
