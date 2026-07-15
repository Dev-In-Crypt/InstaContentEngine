import pytest
import truststore


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
