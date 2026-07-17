"""make_publisher_for builds the right publisher and gates credentials."""
import pytest

from config import Settings
from services.publishing.base import PublisherError
from services.publishing.factory import make_publisher_for
from services.publishing.instagram_adapter import InstagramPlatformPublisher
from services.publishing.x import XPublisher


def _settings(**kw):
    # Start from all-empty creds so the dev machine's real .env can't leak in and
    # make a "missing credentials" test pass spuriously.
    base = dict(
        instagram_access_token="", instagram_user_id="", imgbb_api_key="",
        x_api_key="", x_api_secret="", x_access_token="", x_access_token_secret="",
    )
    base.update(kw)
    return Settings(**base)


def test_instagram_publisher_built_with_creds():
    s = _settings(instagram_access_token="t", instagram_user_id="u", imgbb_api_key="k")
    assert isinstance(make_publisher_for("instagram", s), InstagramPlatformPublisher)


def test_instagram_missing_creds_raises():
    with pytest.raises(PublisherError, match="Instagram credentials"):
        make_publisher_for("instagram", _settings(imgbb_api_key="k"))


def test_instagram_missing_imgbb_raises():
    s = _settings(instagram_access_token="t", instagram_user_id="u")   # no imgbb
    with pytest.raises(PublisherError, match="IMGBB"):
        make_publisher_for("instagram", s)


def test_x_publisher_built_with_creds():
    s = _settings(x_api_key="a", x_api_secret="b", x_access_token="c", x_access_token_secret="d")
    assert isinstance(make_publisher_for("x", s), XPublisher)


def test_x_missing_creds_raises():
    with pytest.raises(PublisherError, match="X .* credentials"):
        make_publisher_for("x", _settings(x_api_key="a"))   # incomplete


def test_unknown_platform_raises():
    with pytest.raises(PublisherError, match="No publisher"):
        make_publisher_for("myspace", _settings())
