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
    args += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-shortest", str(out_path)]
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise VideoError(f"ffmpeg mux failed: {proc.stderr[-400:]}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise VideoError("ffmpeg produced an empty reel")


async def mux_reel(video_path: Path, audio_path: Path, ass_path: Optional[Path],
                   out_path: Path) -> None:
    await asyncio.to_thread(mux_reel_sync, video_path, audio_path, ass_path, out_path)
