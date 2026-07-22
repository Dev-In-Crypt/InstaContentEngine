"""ElevenLabs TTS client + ffmpeg audio toolbox (Reels R1).

The HTTP client is tested against a mock transport (no network); the wav/concat
helpers run the real bundled ffmpeg on a generated test tone — same spirit as
test_kenburns, which also encodes for real.
"""
import asyncio
import subprocess
import wave
from pathlib import Path

import httpx
import pytest

from services.tts import (
    ElevenLabsTTS, TTSError, concat_wavs_sync, ffmpeg_exe, mp3_to_wav_sync,
)


def _client_with(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_synthesize_returns_audio_bytes(monkeypatch):
    def handler(request):
        assert request.headers["xi-api-key"] == "k"
        assert "/text-to-speech/voice1" in str(request.url)
        return httpx.Response(200, content=b"ID3fakemp3")

    transport = _client_with(handler)
    orig = httpx.AsyncClient

    def patched(*a, **kw):
        kw.pop("verify", None)
        return orig(transport=transport, timeout=kw.get("timeout"))
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    out = await ElevenLabsTTS("k").synthesize("hi", voice_id="voice1")
    assert out == b"ID3fakemp3"


@pytest.mark.asyncio
async def test_bad_key_is_actionable(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"detail": "nope"})
    transport = _client_with(handler)
    orig = httpx.AsyncClient

    def patched(*a, **kw):
        kw.pop("verify", None)
        return orig(transport=transport, timeout=kw.get("timeout"))
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    with pytest.raises(TTSError) as e:
        await ElevenLabsTTS("bad").synthesize("hi", voice_id="v")
    assert "key" in str(e.value).lower()


def test_empty_key_rejected_upfront():
    with pytest.raises(TTSError):
        ElevenLabsTTS("")


def _tone_mp3(path: Path, seconds: float) -> bytes:
    """Generate a real tiny MP3 tone with the bundled ffmpeg."""
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
                   capture_output=True, check=True)
    return path.read_bytes()


def test_mp3_to_wav_measures_duration(tmp_path):
    mp3 = _tone_mp3(tmp_path / "tone.mp3", 0.5)
    wav = tmp_path / "tone.wav"
    dur = mp3_to_wav_sync(mp3, wav)
    assert wav.exists()
    assert 0.4 < dur < 0.7            # mp3 padding makes it slightly over 0.5


def test_concat_wavs_builds_track_of_expected_length(tmp_path):
    durs = []
    paths = []
    for i, sec in enumerate((0.3, 0.5)):
        mp3 = _tone_mp3(tmp_path / f"t{i}.mp3", sec)
        wav = tmp_path / f"t{i}.wav"
        durs.append(mp3_to_wav_sync(mp3, wav))
        paths.append(wav)
    out = tmp_path / "voice.m4a"
    total = concat_wavs_sync(paths, out, gap_sec=0.2)
    assert out.exists() and out.stat().st_size > 0
    # reported total = sum(durations + gap); mutation guard: drop the gap → fails
    assert abs(total - (sum(durs) + 0.4)) < 1e-6


def test_concat_empty_raises(tmp_path):
    with pytest.raises(TTSError):
        concat_wavs_sync([], tmp_path / "x.m4a")


def test_async_wrappers_run(tmp_path):
    mp3 = _tone_mp3(tmp_path / "a.mp3", 0.3)
    from services.tts import concat_wavs, mp3_to_wav

    async def _run():
        wav = tmp_path / "a.wav"
        d = await mp3_to_wav(mp3, wav)
        total = await concat_wavs([wav], tmp_path / "a.m4a", gap_sec=0.1)
        return d, total
    d, total = asyncio.run(_run())
    assert abs(total - (d + 0.1)) < 1e-6
    with wave.open(str(tmp_path / "a.wav"), "rb") as w:
        assert w.getnchannels() == 1
