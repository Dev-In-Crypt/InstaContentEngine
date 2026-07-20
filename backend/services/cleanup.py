"""Disk housekeeping: drop upload dirs whose post no longer exists.

Each post keeps its rendered slides, raw backgrounds, and reel under
`uploads/posts/<post_id>/`. When a post is deleted the DB row goes but the files
stay, so a multi-tenant deploy slowly fills its disk with orphans. A daily job
(wired in services/scheduler) reconciles the directory against the live post ids
and removes only the ones with no matching post — never files of a live post, so
overlay-edit (which needs the raw image) is unaffected. The same job sweeps
`uploads/staging`, where a user's own photos wait between being picked and being
generated from.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select

from services import staging

log = logging.getLogger(__name__)

# backend/services/cleanup.py -> backend/uploads/posts
_POSTS_ROOT = Path(__file__).resolve().parent.parent / "uploads" / "posts"


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def find_orphaned_dirs(posts_root: Path, known_ids: Iterable[str]) -> list[Path]:
    """Subdirectories of posts_root whose name isn't a live post id."""
    if not posts_root.exists():
        return []
    known = set(known_ids)
    return [d for d in posts_root.iterdir() if d.is_dir() and d.name not in known]


def cleanup_orphaned_uploads(posts_root: Path, known_ids: Iterable[str]) -> dict:
    """Remove upload dirs with no matching post. Returns {removed, freed_bytes}."""
    removed, freed = 0, 0
    for d in find_orphaned_dirs(posts_root, known_ids):
        size = _dir_size(d)
        try:
            shutil.rmtree(d)
            removed += 1
            freed += size
        except OSError as e:
            log.warning("Cleanup could not remove %s: %s", d, e)
    return {"removed": removed, "freed_bytes": freed}


async def run_upload_cleanup(sessionmaker, posts_root: Path | None = None) -> dict:
    """Load live post ids from the DB and reconcile the uploads dir against them."""
    from models.database import Post

    root = posts_root or _POSTS_ROOT
    async with sessionmaker() as session:
        ids = (await session.execute(select(Post.id))).scalars().all()
    result = cleanup_orphaned_uploads(root, ids)
    if result["removed"]:
        log.info("Upload cleanup removed %d orphaned dir(s), freed %d bytes",
                 result["removed"], result["freed_bytes"])

    # Photos staged for a generation that never happened: the ids are only useful
    # for the few minutes between picking files and hitting Generate.
    staged = staging.sweep()
    if staged["files"]:
        log.info("Staging sweep removed %d file(s), freed %d bytes",
                 staged["files"], staged["bytes"])
    result["staged_removed"] = staged["files"]
    return result
