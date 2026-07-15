"""Shared HTTP helpers."""

from __future__ import annotations

import ssl

import httpx
import truststore

SSL_HINT = (
    "SSL certificate verification failed. If you are behind a corporate proxy or "
    "antivirus that inspects HTTPS, set SSL_VERIFY=false in backend/.env "
    "(insecure — only do this on a network you trust)."
)


def ssl_config(ssl_verify: bool) -> ssl.SSLContext | bool:
    """What to hand httpx's `verify=`.

    Verifying against certifi's bundle is wrong on the desktop: security suites
    (Avast, Kaspersky, ESET) and corporate proxies terminate TLS and re-sign it
    with their own root, which certifi has never heard of — every call then dies
    with "unable to get local issuer certificate". Their root *is* installed in
    the OS trust store, so validating against that keeps verification on and
    still rejects a certificate no one on this machine trusts.

    Returns False only when the operator has explicitly opted out.
    """
    if not ssl_verify:
        return False
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


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
