"""TLS must be verified against the OS trust store, at every entry point.

Two defects these tests exist to prevent, both of which actually shipped:

1. verify=False (accepts any certificate, exposing the API keys in the request
   headers to anyone who can intercept the connection).
2. Verifying against certifi's bundle, which fails outright behind an
   HTTPS-inspecting antivirus or corporate proxy. That is the whole reason
   setup_tls() exists, so the tests below assert the un-injected state FIRST —
   otherwise they pass whether or not the production code calls it.

The autouse fixture in conftest.py undoes injection between tests.
"""
import ssl
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import truststore

from config import Settings
from services.http_utils import setup_tls
from services.openrouter import OpenRouterClient
from services.stock import PexelsClient, UnsplashClient

SERVICES_DIR = Path(__file__).resolve().parents[1] / "services"


def _default_httpx_ssl_context() -> ssl.SSLContext:
    """The context httpx builds for a plain verify=True client."""
    return httpx.AsyncClient()._transport._pool._ssl_context


# ── the hook itself ─────────────────────────────────────────────────────────

def test_setup_tls_makes_default_httpx_client_use_os_trust_store():
    truststore.extract_from_ssl()
    # Precondition: without the hook, httpx verifies against certifi. If this
    # assert ever fails, the test below proves nothing.
    assert not isinstance(_default_httpx_ssl_context(), truststore.SSLContext)

    setup_tls()

    assert isinstance(_default_httpx_ssl_context(), truststore.SSLContext)


def test_setup_tls_is_idempotent():
    """lifespan and the bot entry point can both run in one process."""
    setup_tls()
    setup_tls()
    assert isinstance(_default_httpx_ssl_context(), truststore.SSLContext)


def test_ssl_verify_false_still_disables_verification_when_injected():
    """The escape hatch has to survive the global hook.

    truststore honours CERT_NONE, which is what lets setup_tls() run
    unconditionally: skipping injection for SSL_VERIFY=false would drop the
    clients that ignore that flag back onto certifi, so turning verification off
    would cause more failures than leaving it on.
    """
    setup_tls()
    ctx = httpx.AsyncClient(verify=False)._transport._pool._ssl_context
    assert ctx.verify_mode is ssl.CERT_NONE


# ── entry points ────────────────────────────────────────────────────────────

async def test_lifespan_sets_up_tls_before_creating_clients(tmp_path, monkeypatch):
    """Covers `uvicorn main:app` (Docker/Render) and InstaContentEngine.pyw,
    which imports `main` and runs uvicorn in a thread."""
    import main

    monkeypatch.setattr(
        main, "settings",
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'ls.db'}"),
    )
    monkeypatch.setattr("services.scheduler.init_scheduler", lambda *a, **kw: None)
    monkeypatch.setattr("services.scheduler.shutdown_scheduler", lambda *a, **kw: None)

    truststore.extract_from_ssl()
    assert not isinstance(_default_httpx_ssl_context(), truststore.SSLContext)

    async with main.lifespan(main.app):
        assert isinstance(_default_httpx_ssl_context(), truststore.SSLContext)


def test_run_bot_sets_up_tls_before_building_telegram_client(monkeypatch):
    """Covers `python -m bot.run_bot`, which never imports main.py and so never
    runs its lifespan.

    Asserts ORDER, not just presence: python-telegram-bot builds its httpx
    client inside Application.builder()...build(), which InstaBot calls from its
    constructor. A hook installed after that line would be useless.
    """
    from bot import run_bot

    injected_at_construction = {}

    class FakeBot:
        def __init__(self, token, engine):
            injected_at_construction["value"] = isinstance(
                _default_httpx_ssl_context(), truststore.SSLContext
            )

        def run(self):
            pass

    monkeypatch.setattr(run_bot, "InstaBot", FakeBot)
    monkeypatch.setattr(run_bot, "build_engine", lambda settings: object())
    monkeypatch.setattr(run_bot, "get_settings", lambda: Settings(telegram_bot_token="t"))

    truststore.extract_from_ssl()
    run_bot.main()

    assert injected_at_construction["value"] is True


# ── SSL_VERIFY setting and its wiring ───────────────────────────────────────

def test_settings_verify_tls_by_default():
    assert Settings().ssl_verify is True


def test_settings_ssl_verify_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SSL_VERIFY", "false")
    assert Settings().ssl_verify is False


@pytest.mark.parametrize("factory", [
    lambda **kw: OpenRouterClient("key", **kw),
    lambda **kw: UnsplashClient("key", **kw),
    lambda **kw: PexelsClient("key", **kw),
])
def test_client_verifies_by_default(factory):
    with patch("httpx.AsyncClient") as mock_client:
        factory()._get_client()
    assert mock_client.call_args.kwargs["verify"] is True


@pytest.mark.parametrize("factory", [
    lambda **kw: OpenRouterClient("key", **kw),
    lambda **kw: UnsplashClient("key", **kw),
    lambda **kw: PexelsClient("key", **kw),
])
def test_client_honours_ssl_verify_false(factory):
    with patch("httpx.AsyncClient") as mock_client:
        factory(ssl_verify=False)._get_client()
    assert mock_client.call_args.kwargs["verify"] is False


def test_deps_wire_settings_ssl_verify_into_clients():
    """Through the PUBLIC factories: a client defaulting to verify=True is no
    use if deps.py never forwards the configured value."""
    from api.deps import _get_openrouter, _get_stock_client, get_openrouter, get_stock

    _get_openrouter.cache_clear()      # @lru_cache leaks across tests
    _get_stock_client.cache_clear()

    settings = Settings(ssl_verify=False, unsplash_access_key="u", pexels_api_key="p")
    assert get_openrouter(settings)._ssl_verify is False

    stock = get_stock(settings)
    assert stock.unsplash._ssl_verify is False
    assert stock.pexels._ssl_verify is False

    _get_openrouter.cache_clear()
    _get_stock_client.cache_clear()


def test_run_bot_forwards_ssl_verify(monkeypatch):
    """The bot builds its own clients, bypassing deps.py."""
    from bot import run_bot

    engine = run_bot.build_engine(
        Settings(ssl_verify=False, unsplash_access_key="u", pexels_api_key="p")
    )
    assert engine.image_router.openrouter._ssl_verify is False
    assert engine.image_router.stock.unsplash._ssl_verify is False
    assert engine.image_router.stock.pexels._ssl_verify is False


# ── regression guard ────────────────────────────────────────────────────────

def test_no_service_hardcodes_verify_false():
    """Catches a hard-coded opt-out reappearing.

    Deliberately narrow: since setup_tls() makes httpx's default correct, a
    client that omits verify= is now fine, so there is no string to grep for the
    failure this suite really cares about. The entry-point tests above are what
    guard that.
    """
    offenders = [
        path.name for path in SERVICES_DIR.rglob("*.py")
        if "verify=False" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
