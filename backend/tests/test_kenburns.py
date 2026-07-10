import io

import pytest
from PIL import Image

from services.video.base import get_video_provider, VideoError
from services.video.kenburns import KenBurnsVideoProvider


def _slide(color="teal") -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (1080, 1350), color).save(b, format="JPEG")
    return b.getvalue()


@pytest.mark.asyncio
async def test_kenburns_produces_vertical_mp4():
    prov = KenBurnsVideoProvider()
    data = await prov.make_reel([_slide("teal"), _slide("orange")],
                                overlays=["First.", "Second."], duration_per=0.5)
    assert isinstance(data, bytes) and len(data) > 0
    # Verify dimensions via imageio reader
    import imageio.v2 as iio
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mktemp(suffix=".mp4"))
    p.write_bytes(data)
    try:
        r = iio.get_reader(str(p))
        frame = r.get_data(0)
        r.close()
        assert frame.shape == (1920, 1080, 3)   # 9:16 vertical
    finally:
        p.unlink()


@pytest.mark.asyncio
async def test_kenburns_empty_raises():
    with pytest.raises(VideoError):
        await KenBurnsVideoProvider().make_reel([])


@pytest.mark.asyncio
async def test_ai_provider_stub_raises():
    prov = get_video_provider("ai")
    with pytest.raises(VideoError):
        await prov.make_reel([_slide()])


def test_get_provider_unknown():
    with pytest.raises(VideoError):
        get_video_provider("banana")
