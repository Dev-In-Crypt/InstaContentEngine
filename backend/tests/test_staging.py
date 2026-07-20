"""Staged photos: ids are minted here, and one tenant can never read another's."""
import time

import pytest

from services import staging
from services.staging import StagingError


def test_save_returns_an_id_that_reads_back(tmp_path):
    upload_id = staging.save("user-1", b"jpeg-bytes", "image/jpeg", root=tmp_path)
    assert staging.read("user-1", upload_id, root=tmp_path) == b"jpeg-bytes"


def test_id_is_server_minted_not_the_filename(tmp_path):
    """The client never names a file on our disk."""
    upload_id = staging.save("user-1", b"x", "image/png", root=tmp_path)
    assert len(upload_id) == 32 and upload_id.isalnum()
    assert (tmp_path / "user-1" / f"{upload_id}.png").exists()


def test_another_users_id_does_not_resolve(tmp_path):
    """The whole isolation story: same id, different tenant, no read."""
    upload_id = staging.save("user-1", b"private", "image/jpeg", root=tmp_path)
    with pytest.raises(StagingError):
        staging.read("user-2", upload_id, root=tmp_path)


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd",
    "..\\..\\windows\\win.ini",
    "abc",                       # too short to be a uuid
    "",
    "g" * 32,                    # right length, not hex
])
def test_malformed_ids_are_refused(tmp_path, bad):
    staging.save("user-1", b"x", "image/jpeg", root=tmp_path)
    with pytest.raises(StagingError):
        staging.path_for("user-1", bad, root=tmp_path)


def test_traversal_to_a_file_that_really_exists_is_refused(tmp_path):
    """The teeth of the path check: an id that escapes the user folder and lands
    on a file that IS there must still be refused, not served."""
    (tmp_path / "secret.jpg").write_bytes(b"another tenant's photo")
    staging.save("user-1", b"mine", "image/jpeg", root=tmp_path)
    with pytest.raises(StagingError):
        staging.read("user-1", "../secret", root=tmp_path)


def test_unsupported_content_type_is_refused(tmp_path):
    with pytest.raises(StagingError):
        staging.save("user-1", b"%PDF", "application/pdf", root=tmp_path)


def test_sweep_removes_stale_files_and_keeps_fresh_ones(tmp_path):
    fresh = staging.save("user-1", b"new", "image/jpeg", root=tmp_path)
    stale = staging.save("user-1", b"old", "image/jpeg", root=tmp_path)
    stale_path = staging.path_for("user-1", stale, root=tmp_path)
    old = time.time() - 48 * 3600
    import os
    os.utime(stale_path, (old, old))

    result = staging.sweep(root=tmp_path)

    assert result["files"] == 1
    assert staging.read("user-1", fresh, root=tmp_path) == b"new"
    with pytest.raises(StagingError):
        staging.read("user-1", stale, root=tmp_path)


def test_sweep_on_a_missing_root_is_a_no_op(tmp_path):
    assert staging.sweep(root=tmp_path / "nope") == {"files": 0, "bytes": 0}
