"""The restore ZIP extractor must not write outside uploads/.

A backup ZIP is attacker-controlled input (POST /api/admin/restore). The original
guard only ran its containment check when the destination escaped *above*
backend/, so an entry named `uploads/../main.py` — which lands inside backend/ —
skipped the check and overwrote source files. That is code execution on the next
restart. This pins the extractor to uploads/ only.
"""
import io
import zipfile

import api.routes.admin as admin


def _zip(entries: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_extract_uploads_ignores_traversal_into_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "_BACKEND_DIR", tmp_path)
    zf = _zip({
        "uploads/ok.txt": b"legit",
        "uploads/../pwned.txt": b"escaped into backend/",
        "uploads/../../pwned2.txt": b"escaped above backend/",
        "uploads/sub/nested.txt": b"legit nested",
    })

    admin._extract_uploads(zf)

    # Legit entries land inside uploads/.
    assert (tmp_path / "uploads" / "ok.txt").read_bytes() == b"legit"
    assert (tmp_path / "uploads" / "sub" / "nested.txt").exists()

    # Nothing escapes uploads/.
    assert not (tmp_path / "pwned.txt").exists()
    assert not (tmp_path.parent / "pwned2.txt").exists()
    # And uploads/ holds only the two legit files.
    written = {p.name for p in (tmp_path / "uploads").rglob("*") if p.is_file()}
    assert written == {"ok.txt", "nested.txt"}
