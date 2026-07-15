"""TLS verification must stay on by default.

These clients shipped with verify=False for a long time, which silently accepted
any certificate and exposed the API keys in their headers to anyone able to
intercept the connection. The tests below pin the safe default so a future
refactor can't quietly undo it, and pin the SSL_VERIFY escape hatch so the
documented workaround for HTTPS-inspecting proxies keeps working.
"""
import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from config import Settings
from services.http_utils import ssl_config
from services.openrouter import OpenRouterClient
from services.stock import PexelsClient, UnsplashClient

SERVICES_DIR = Path(__file__).resolve().parents[1] / "services"


def test_settings_verify_tls_by_default():
    assert Settings().ssl_verify is True


def test_ssl_config_verifies_via_os_trust_store():
    """certifi's bundle can't see the roots a local security suite installs, so
    verification has to go through the OS store to survive TLS inspection."""
    ctx = ssl_config(True)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ssl_config_opt_out_returns_false():
    assert ssl_config(False) is False


def test_settings_ssl_verify_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SSL_VERIFY", "false")
    assert Settings().ssl_verify is False


@pytest.mark.parametrize("factory", [
    lambda **kw: OpenRouterClient("key", **kw),
    lambda **kw: UnsplashClient("key", **kw),
    lambda **kw: PexelsClient("key", **kw),
])
def test_client_verifies_against_os_trust_store_by_default(factory):
    with patch("httpx.AsyncClient") as mock_client:
        factory()._get_client()
    verify = mock_client.call_args.kwargs["verify"]
    assert isinstance(verify, ssl.SSLContext)
    assert verify.verify_mode is ssl.CERT_REQUIRED


@pytest.mark.parametrize("factory", [
    lambda **kw: OpenRouterClient("key", **kw),
    lambda **kw: UnsplashClient("key", **kw),
    lambda **kw: PexelsClient("key", **kw),
])
def test_client_honours_ssl_verify_false(factory):
    """The corporate-proxy escape hatch must actually reach httpx."""
    with patch("httpx.AsyncClient") as mock_client:
        factory(ssl_verify=False)._get_client()
    assert mock_client.call_args.kwargs["verify"] is False


def test_deps_wire_settings_ssl_verify_into_clients():
    """The DI factories must forward the setting — a client defaulting to True is
    useless if deps.py never passes the configured value through."""
    from api.deps import _get_openrouter, _get_stock_client

    orc = _get_openrouter("key", "https://localhost", "title", False)
    assert orc._ssl_verify is False

    stock = _get_stock_client("unsplash-key", "pexels-key", False)
    assert stock.unsplash._ssl_verify is False
    assert stock.pexels._ssl_verify is False


def test_no_service_disables_tls_verification():
    """Regression guard: catches verify=False reappearing anywhere in services/,
    including code paths that build a throwaway client inline for downloads."""
    offenders = [
        path.name for path in SERVICES_DIR.rglob("*.py")
        if "verify=False" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
