"""The tenant's brand logo: one file per user, and no reading across tenants."""
import pytest

from services import logo_store
from services.logo_store import LogoError


def test_save_then_read_back(tmp_path):
    logo_store.save("user-1", b"pngbytes", "image/png", root=tmp_path)
    path = logo_store.path_for("user-1", root=tmp_path)
    assert path is not None
    assert path.read_bytes() == b"pngbytes"
    assert path.name == "user-1.png"


def test_reupload_replaces_and_leaves_no_stale_file(tmp_path):
    logo_store.save("user-1", b"old-jpg", "image/jpeg", root=tmp_path)
    logo_store.save("user-1", b"new-png", "image/png", root=tmp_path)
    # Exactly one logo for the user — the old .jpg must be gone.
    files = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert files == ["user-1.png"]
    assert logo_store.path_for("user-1", root=tmp_path).read_bytes() == b"new-png"


def test_no_logo_reads_as_none(tmp_path):
    assert logo_store.path_for("nobody", root=tmp_path) is None


def test_delete_removes_the_file(tmp_path):
    logo_store.save("user-1", b"x", "image/png", root=tmp_path)
    logo_store.delete("user-1", root=tmp_path)
    assert logo_store.path_for("user-1", root=tmp_path) is None


def test_delete_when_absent_is_a_no_op(tmp_path):
    logo_store.delete("ghost", root=tmp_path)   # must not raise


def test_unsupported_type_is_refused(tmp_path):
    with pytest.raises(LogoError):
        logo_store.save("user-1", b"%PDF", "application/pdf", root=tmp_path)


def test_traversal_to_a_real_file_is_refused(tmp_path):
    """A user id that escapes the folder onto a file that IS there must be refused,
    not served — the teeth of the containment check."""
    (tmp_path.parent / "secret.png").write_bytes(b"another tenant")
    with pytest.raises(LogoError):
        logo_store.path_for("../secret", root=tmp_path)
