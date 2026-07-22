"""Ken Burns slideshow → vertical Reel MP4, fully local (no external API).

Each slide is fitted onto a 1080x1920 (9:16) canvas and animated with a slow
zoom/pan. Optional per-slide text overlay is drawn at the bottom. Frames are
encoded to H.264 via imageio-ffmpeg (which bundles the ffmpeg binary).
"""

from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from services.video.base import VideoError

REEL_W, REEL_H = 1080, 1920
FPS = 30


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _fit_cover(img: Image.Image, target: tuple[int, int], scale: float) -> Image.Image:
    """Resize-cover the image to target * scale, so we can crop a moving window."""
    tw, th = int(target[0] * scale), int(target[1] * scale)
    iw, ih = img.size
    ratio = max(tw / iw, th / ih)
    return img.resize((max(1, int(iw * ratio)), max(1, int(ih * ratio))), Image.LANCZOS)


def _wrap(draw, text, font, max_w) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def _render_slide_frames(slide_bytes: bytes, overlay: Optional[str], n_frames: int):
    """Yield n_frames RGB frames (1080x1920) with a Ken Burns zoom on this slide."""
    base = Image.open(io.BytesIO(slide_bytes)).convert("RGB")
    # Zoom from 1.15x → 1.30x over the slide's frames.
    z0, z1 = 1.15, 1.30
    big = _fit_cover(base, (REEL_W, REEL_H), z1)   # largest we need
    bw, bh = big.size
    for f in range(n_frames):
        t = f / max(1, n_frames - 1)
        scale = z0 + (z1 - z0) * t
        cw, ch = int(REEL_W * scale), int(REEL_H * scale)
        # pan slightly diagonally
        max_x, max_y = bw - cw, bh - ch
        left = int(max_x * (0.3 + 0.4 * t)) if max_x > 0 else 0
        top = int(max_y * (0.2 + 0.5 * t)) if max_y > 0 else 0
        crop = big.crop((left, top, left + cw, top + ch)).resize((REEL_W, REEL_H), Image.LANCZOS)

        if overlay:
            draw = ImageDraw.Draw(crop, "RGBA")
            font = _load_font(54)
            lines = _wrap(draw, overlay, font, REEL_W - 160)
            lh = draw.textbbox((0, 0), "Ag", font=font)[3] + 14
            box_h = lh * len(lines) + 48
            y0 = REEL_H - box_h - 180
            draw.rectangle([0, y0, REEL_W, y0 + box_h], fill=(0, 0, 0, 140))
            y = y0 + 24
            for ln in lines:
                w = draw.textbbox((0, 0), ln, font=font)[2]
                draw.text(((REEL_W - w) // 2, y), ln, fill=(255, 255, 255), font=font)
                y += lh
        yield crop


class KenBurnsVideoProvider:
    async def make_reel(
        self,
        slides: list[bytes],
        overlays: Optional[list[str]] = None,
        duration_per: float | list[float] = 3.0,
        audio_path: Optional[str] = None,
    ) -> bytes:
        if not slides:
            raise VideoError("No slides to build a reel from")
        return await asyncio.to_thread(
            self._render_sync, slides, overlays or [], duration_per, audio_path
        )

    @staticmethod
    def _render_sync(slides, overlays, duration_per, audio_path) -> bytes:
        import imageio.v2 as imageio
        import numpy as np

        # A list gives each slide its own length (voiceover: slide i stays up for
        # exactly its narration segment); a scalar keeps the old uniform pacing.
        if isinstance(duration_per, (list, tuple)):
            durations = list(duration_per) + [3.0] * (len(slides) - len(duration_per))
        else:
            durations = [float(duration_per)] * len(slides)
        tmp = Path(tempfile.mkdtemp()) / "reel.mp4"
        try:
            writer = imageio.get_writer(
                str(tmp), fps=FPS, codec="libx264", quality=7,
                macro_block_size=None, ffmpeg_log_level="error",
            )
        except Exception as e:
            raise VideoError(f"ffmpeg writer init failed: {e}") from e
        try:
            for i, sb in enumerate(slides):
                ov = overlays[i] if i < len(overlays) else None
                n_per = max(1, int(float(durations[i]) * FPS))
                for frame in _render_slide_frames(sb, ov, n_per):
                    writer.append_data(np.asarray(frame))
        finally:
            writer.close()
        data = tmp.read_bytes()
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass
        if not data:
            raise VideoError("ffmpeg produced an empty file")
        return data
