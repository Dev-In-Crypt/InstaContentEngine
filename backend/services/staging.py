"""Where a user's own photos live between "I picked these files" and "generate".

Generation is an SSE endpoint with a JSON body, so files can't ride along with
it — they are uploaded first, land here, and the generate call refers to them by
id. Nothing here is permanent: once a post is built its slides live under
uploads/posts/<post_id>/, and the sweep below clears whatever the user picked and
never generated.

Ids are server-minted uuid4 hex, never a client-supplied name, and every read
re-checks that the resolved path is inside that user's own folder — the same
containment discipline as the export unzip path.
"""
from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

#: backend/services/staging.py -> backend/uploads/staging
STAGING_ROOT = Path(__file__).resolve().parent.parent / "uploads" / "staging"

#: Content type → the extension we store it under. Also the allow-list.
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_STALE_SECONDS = 24 * 3600


class StagingError(Exception):
    """Unknown id, or an id that doesn't belong to this user."""


def user_dir(user_id: str, root: Optional[Path] = None) -> Path:
    """One folder per tenant, so one user's id can never name another's file."""
    return (root or STAGING_ROOT) / str(user_id)


def save(user_id: str, data: bytes, content_type: str,
         root: Optional[Path] = None) -> str:
    """Store the bytes and return the id the client will hand back at generate."""
    ext = EXTENSIONS.get(content_type)
    if ext is None:
        raise StagingError(f"Unsupported content type {content_type!r}")
    upload_id = uuid.uuid4().hex
    directory = user_dir(user_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{upload_id}.{ext}").write_bytes(data)
    return upload_id


def path_for(user_id: str, upload_id: str, root: Optional[Path] = None) -> Path:
    """Resolve an id to a file, refusing anything outside this user's folder.

    The id shape is checked first, so "../../etc/passwd" never reaches the
    filesystem; the containment check is the second lock, for whatever the
    regex might miss.
    """
    if not _ID_RE.match(upload_id or ""):
        raise StagingError("Unknown upload id")
    directory = user_dir(user_id, root).resolve()
    for ext in EXTENSIONS.values():
        candidate = (directory / f"{upload_id}.{ext}").resolve()
        if not candidate.is_relative_to(directory):
            raise StagingError("Unknown upload id")
        if candidate.exists():
            return candidate
    raise StagingError("Unknown upload id")


def read(user_id: str, upload_id: str, root: Optional[Path] = None) -> bytes:
    return path_for(user_id, upload_id, root).read_bytes()


def sweep(root: Optional[Path] = None, older_than_seconds: int = _STALE_SECONDS) -> dict:
    """Delete staged files nobody generated from. Returns {files, bytes}."""
    directory = root or STAGING_ROOT
    if not directory.exists():
        return {"files": 0, "bytes": 0}
    cutoff = time.time() - older_than_seconds
    files = freed = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
            files += 1
    # Drop the per-user folders left empty behind them.
    for child in directory.iterdir():
        if child.is_dir() and not any(child.iterdir()):
            shutil.rmtree(child, ignore_errors=True)
    return {"files": files, "bytes": freed}
