"""AI text-to-video provider (Runway / Kling / Luma) — not implemented yet.

Kept as a stub so the VideoProvider abstraction is complete and wiring/tests
exist. A real implementation would: submit shot_list prompts to the provider,
poll for the rendered clips, stitch them, and return MP4 bytes.
"""

from __future__ import annotations

from typing import Optional

from services.video.base import VideoError


class AIVideoProvider:
    async def make_reel(
        self,
        slides: list[bytes],
        overlays: Optional[list[str]] = None,
        duration_per: float = 3.0,
        audio_path: Optional[str] = None,
    ) -> bytes:
        raise VideoError(
            "AI text-to-video is not implemented yet. Set VIDEO_PROVIDER=kenburns "
            "to build Reels from your slides locally."
        )
