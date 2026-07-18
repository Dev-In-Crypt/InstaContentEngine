import logging

import pytest
import truststore


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """setup_logging() mutates the root logger's level and handlers process-wide;
    restore it so a test asserting a level can't pass on another test's setup."""
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """slowapi's limiter keeps in-memory per-IP counters. The TestClient always
    presents the same host ('testclient'), so counters accumulate across tests
    and unrelated suites trip the auth limits. Disable globally; the one test
    that asserts 429 re-enables it locally."""
    from api.ratelimit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
def _restore_ssl_context():
    """Undo any TLS injection a test performed.

    setup_tls() replaces ssl.SSLContext process-wide (and urllib3's own reference
    to it). Without this, one test's injection leaks into the next, and a test
    asserting "TLS is set up" passes because an earlier test set it up — the
    exact vacuous pass the TLS tests are written to avoid.
    """
    yield
    truststore.extract_from_ssl()
