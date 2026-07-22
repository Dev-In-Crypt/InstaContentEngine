"""Per-user Reel music store (R3) — mirror of the logo store guarantees.

Containment is the mutation target: the path is always built server-side from
the user id and re-checked against the music folder.
"""
import pytest

from services.music_store import MusicError, delete, path_for, save


def test_save_and_path_for_round_trip(tmp_path):
    p = save("u1", b"ID3fake", "audio/mpeg", root=tmp_path)
    assert p.name == "u1.mp3" and p.read_bytes() == b"ID3fake"
    assert path_for("u1", root=tmp_path) == p


def test_resave_with_other_mime_replaces(tmp_path):
    save("u1", b"a", "audio/mpeg", root=tmp_path)
    p2 = save("u1", b"b", "audio/wav", root=tmp_path)
    # mutation guard: skip the delete-first → stale .mp3 shadows the new .wav
    assert path_for("u1", root=tmp_path) == p2
    assert not (tmp_path / "u1.mp3").exists()


def test_unsupported_mime_raises(tmp_path):
    with pytest.raises(MusicError):
        save("u1", b"x", "video/mp4", root=tmp_path)


def test_delete_is_idempotent(tmp_path):
    save("u1", b"a", "audio/mpeg", root=tmp_path)
    delete("u1", root=tmp_path)
    assert path_for("u1", root=tmp_path) is None
    delete("u1", root=tmp_path)   # no error the second time


def test_containment_refuses_escape(tmp_path):
    with pytest.raises(MusicError):
        path_for("../../evil", root=tmp_path)
