"""Shared HTTP helpers."""

from __future__ import annotations

import httpx

SSL_HINT = (
    "SSL certificate verification failed. If you are behind a corporate proxy or "
    "antivirus that inspects HTTPS, set SSL_VERIFY=false in backend/.env "
    "(insecure — only do this on a network you trust)."
)


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
