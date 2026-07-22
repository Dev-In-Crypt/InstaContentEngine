"""Reel music settings endpoints (R3) — upload/round-trip/delete + guards."""
import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.deps import get_db
from main import app
from models.database import Base
from services import music_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(music_store, "MUSIC_ROOT", tmp_path / "music")
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'music.db'}")

    async def _create():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())
    SM = async_sessionmaker(eng, expire_on_commit=False)

    async def override_db():
        async with SM() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.state.sessionmaker = SM
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    asyncio.run(eng.dispose())


def _upload(client, data=b"ID3fakemp3", mime="audio/mpeg"):
    return client.post("/api/settings/music",
                       files={"file": ("track.mp3", io.BytesIO(data), mime)})


def test_round_trip(client):
    assert client.get("/api/settings/music").json() == {"set": False}
    assert _upload(client).json() == {"set": True}
    assert client.get("/api/settings/music").json() == {"set": True}
    assert client.delete("/api/settings/music").json() == {"set": False}
    assert client.get("/api/settings/music").json() == {"set": False}


def test_wrong_mime_415(client):
    r = _upload(client, mime="video/mp4")
    assert r.status_code == 415


def test_empty_file_400(client):
    assert _upload(client, data=b"").status_code == 400


def test_oversize_413(client):
    big = b"x" * (20 * 1024 * 1024 + 1)
    assert _upload(client, data=big).status_code == 413
