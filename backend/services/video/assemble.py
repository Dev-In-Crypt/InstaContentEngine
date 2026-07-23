"""Final Reel assembly — mux the voiceover onto the rendered video and burn the
subtitles in. One ffmpeg pass, recipe proven in the user's shorts-pipeline
(step6): libx264 CRF 20, yuv420p, AAC 192k, +faststart, -shortest.

Kept as small path-pure sync functions wrapped for asyncio — same shape as the
rest of services/video.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from services.tts import ffmpeg_exe
from services.video.base import VideoError


def _ass_filter_path(path: Path) -> str:
    """ffmpeg's filter parser eats ':' and '\\' — forward slashes + escaped colon
    keep a Windows drive path (C:/...) intact inside ass='...'."""
    return str(path).replace("\\", "/").replace(":", r"\:")


def mux_reel_sync(video_path: Path, audio_path: Path, ass_path: Optional[Path],
                  out_path: Path) -> None:
    """video (silent) + audio track [+ burned ASS subs] → out_path (H.264/AAC)."""
    args = [ffmpeg_exe(), "-hide_banner", "-y",
            "-i", str(video_path), "-i", str(audio_path)]
    if ass_path is not None:
        args += ["-vf", f"ass='{_ass_filter_path(ass_path)}'",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    # loudnorm to the Instagram target (~-14 LUFS): without it a voiceover reel
    # lands near -26..-29 LUFS (barely audible on a phone). One pass is plenty for
    # a 15-30s clip. Fixes both the voice-only R1 reel and the ducked music mix.
    args += ["-map", "0:v:0", "-map", "1:a:0",
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-shortest", str(out_path)]
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise VideoError(f"ffmpeg mux failed: {proc.stderr[-400:]}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise VideoError("ffmpeg produced an empty reel")


async def mux_reel(video_path: Path, audio_path: Path, ass_path: Optional[Path],
                   out_path: Path) -> None:
    await asyncio.to_thread(mux_reel_sync, video_path, audio_path, ass_path, out_path)


# ── Cover intro (R3) — the branded slide 1 REPLACES the reel's first 0.5s ────
# Replacing (not prepending) keeps the voice and subtitles at t=0 untouched:
# only the video track shows the cover while the narration already runs.

COVER_DURATION_SEC = 0.5


def _run_ffmpeg(args: list[str], what: str) -> None:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-y", *args],
                          capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise VideoError(f"ffmpeg {what} failed: {proc.stderr[-400:]}")


def render_cover_sync(image_bytes: bytes, dst: Path,
                      duration: float = COVER_DURATION_SEC) -> None:
    """Render a `duration`-second 1080x1920 mp4 from a still (slide 1)."""
    tmp_jpg = dst.with_suffix(".cover.jpg")
    tmp_jpg.write_bytes(image_bytes)
    try:
        # Blur-pad, not crop: a 4:5 slide fitted into 9:16 would lose ~21% off
        # each side with force_original_aspect_ratio=increase+crop, chopping the
        # slide's own text. Instead the full slide sits centred over a blurred,
        # zoomed copy of itself — nothing is cut.
        _run_ffmpeg(
            ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(tmp_jpg),
             "-r", "30", "-filter_complex",
             "split[a][b];"
             "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
             "crop=1080:1920,gblur=sigma=30[bg];"
             "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
             "[bg][fg]overlay=(W-w)/2:(H-h)/2",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-pix_fmt", "yuv420p", str(dst)],
            "cover render")
    finally:
        tmp_jpg.unlink(missing_ok=True)
    if not dst.exists() or dst.stat().st_size == 0:
        raise VideoError("cover render produced an empty file")


def prepend_cover_sync(cover: Path, base: Path, dst: Path, total_dur: float) -> None:
    """Trim the first COVER_DURATION_SEC off `base` and concat `cover` in front —
    total duration stays == total_dur, so voice/subs timing is untouched."""
    trimmed = base.with_name(base.stem + "_trimmed.mp4")
    target_after = max(0.0, total_dur - COVER_DURATION_SEC)
    _run_ffmpeg(["-ss", f"{COVER_DURATION_SEC:.3f}", "-i", str(base),
                 "-t", f"{target_after:.3f}",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-r", "30", "-an", str(trimmed)],
                "cover trim")
    lst = base.with_name(base.stem + "_cover_list.txt")
    lst.write_text(f"file '{cover.resolve().as_posix()}'\n"
                   f"file '{trimmed.resolve().as_posix()}'\n", encoding="utf-8")
    try:
        _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
                     "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                     "-pix_fmt", "yuv420p", "-r", "30", "-an", str(dst)],
                    "cover concat")
    finally:
        lst.unlink(missing_ok=True)
        trimmed.unlink(missing_ok=True)
    if not dst.exists() or dst.stat().st_size == 0:
        raise VideoError("cover concat produced an empty file")


async def render_cover(image_bytes: bytes, dst: Path,
                       duration: float = COVER_DURATION_SEC) -> None:
    await asyncio.to_thread(render_cover_sync, image_bytes, dst, duration)


async def prepend_cover(cover: Path, base: Path, dst: Path, total_dur: float) -> None:
    await asyncio.to_thread(prepend_cover_sync, cover, base, dst, total_dur)
