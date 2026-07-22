"""The tenant's background-music track for voiceover Reels (R3) — one file per user.

We deliberately ship NO music library: in a multi-tenant SaaS the only clean
licensing position is that the tenant uploads a track THEY have the rights to.
Mirrors logo_store: keyed by user id, path always built server-side, every read
re-checks containment — a client never names a file on our disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

#: backend/services/music_store.py -> backend/uploads/music
MUSIC_ROOT = Path(__file__).resolve().parent.parent / "uploads" / "music"

#: Content type → stored extension. Also the allow-list.
EXTENSIONS = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


class MusicError(Exception):
    """Unsupported type, or a path that escapes the music folder."""


def _root(root: Optional[Path] = None) -> Path:
    return (root or MUSIC_ROOT).resolve()


def save(user_id: str, data: bytes, content_type: str,
         root: Optional[Path] = None) -> Path:
    """Store the tenant's track, replacing any previous one; return its path."""
    ext = EXTENSIONS.get(content_type)
    if ext is None:
        raise MusicError(f"Unsupported content type {content_type!r}")
    directory = _root(root)
    directory.mkdir(parents=True, exist_ok=True)
    delete(user_id, root)   # a new .mp3 must not sit next to an old .wav
    target = directory / f"{user_id}.{ext}"
    target.write_bytes(data)
    return target


def path_for(user_id: str, root: Optional[Path] = None) -> Optional[Path]:
    """The tenant's track, or None. Refuses anything outside the music folder."""
    directory = _root(root)
    for ext in set(EXTENSIONS.values()):
        candidate = (directory / f"{user_id}.{ext}").resolve()
        if not candidate.is_relative_to(directory):
            raise MusicError("Invalid music path")
        if candidate.exists():
            return candidate
    return None


def delete(user_id: str, root: Optional[Path] = None) -> None:
    """Remove the tenant's track if present. No error if there is none."""
    existing = path_for(user_id, root)
    if existing:
        existing.unlink(missing_ok=True)
