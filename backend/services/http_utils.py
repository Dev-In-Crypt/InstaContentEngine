"""Shared HTTP helpers."""

from __future__ import annotations

import httpx
import truststore

SSL_HINT = (
    "SSL certificate verification failed. If you are behind a corporate proxy or "
    "antivirus that inspects HTTPS, set SSL_VERIFY=false in backend/.env "
    "(insecure — only do this on a network you trust)."
)


def setup_tls() -> None:
    """Verify certificates against the OS trust store instead of certifi's bundle.

    certifi ships Mozilla's roots. A security suite (Avast, Kaspersky, ESET) or a
    corporate proxy terminates TLS and re-signs it with a root certifi has never
    heard of, so every outbound call dies with "unable to get local issuer
    certificate". That root *is* installed in the OS trust store, so validating
    against it keeps verification on and still rejects a certificate nobody on
    this machine trusts.

    Patches ssl.SSLContext process-wide, which is what reaches clients we don't
    construct ourselves — python-telegram-bot builds its own httpx client and
    takes no verify= argument. Wiring this per-client is how the previous attempt
    covered 3 of 9 clients and silently left publishing broken.

    Idempotent, and unconditional by design: skipping it when SSL_VERIFY=false
    would drop the clients that ignore that flag back onto certifi, so turning
    verification *off* would cause *more* failures. SSL_VERIFY stays orthogonal —
    this decides how we verify, that decides whether the clients taking an
    ssl_verify argument verify at all.

    ANY new process entry point must call this before constructing HTTP clients.
    """
    truststore.inject_into_ssl()


def is_ssl_error(exc: Exception) -> bool:
    """True when a httpx request error was caused by TLS verification."""
    text = str(exc).lower()
    return isinstance(exc, httpx.ConnectError) and (
        "certificate" in text or "ssl" in text or "verify failed" in text
    )


def describe_request_error(exc: Exception, service: str) -> str:
    """Human-readable message for a failed HTTP request."""
    if is_ssl_error(exc):
        return f"{service}: {SSL_HINT} (original error: {exc})"
    return f"{service} network error: {exc}"
