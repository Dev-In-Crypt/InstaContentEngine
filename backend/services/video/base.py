"""Video (Reel) generation provider abstraction.

Two implementations:
- KenBurnsVideoProvider — local ffmpeg slideshow from existing slides (default).
- AIVideoProvider — text-to-video (Runway/Kling/Luma), stub for now.
"""

from __future__ import annotations

from typing import Optional, Protocol


class VideoError(Exception):
    pass


class VideoProvider(Protocol):
    async def make_reel(
        self,
        slides: list[bytes],
        overlays: Optional[list[str]] = None,
        duration_per: float | list[float] = 3.0,
        audio_path: Optional[str] = None,
    ) -> bytes:
        """Return H.264 MP4 bytes sized 1080x1920 (9:16 Reels). A list for
        duration_per gives each slide its own length (voiceover sync)."""
        ...


def get_video_provider(name: str = "kenburns") -> VideoProvider:
    name = (name or "kenburns").lower()
    if name == "kenburns":
        from services.video.kenburns import KenBurnsVideoProvider
        return KenBurnsVideoProvider()
    if name == "ai":
        from services.video.ai_provider import AIVideoProvider
        return AIVideoProvider()
    raise VideoError(f"Unknown video provider: {name!r}")
