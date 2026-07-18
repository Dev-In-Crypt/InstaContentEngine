"""Transactional email via Resend (verification + password reset).

If resend_api_key is empty (local dev / no provider yet) sending is a no-op that
just logs — the app keeps working, links are only reachable if the user clicks a
logged URL. Wire a real key in cloud for actual delivery.
"""
from __future__ import annotations

import logging

import httpx

from config import get_settings

log = logging.getLogger(__name__)
_RESEND_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html: str) -> bool:
    """Send one email. Returns True if actually dispatched, False if skipped/failed."""
    s = get_settings()
    if not s.resend_api_key:
        log.info("Email skipped (no RESEND_API_KEY): to=%s subject=%r", to, subject)
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {s.resend_api_key}"},
                json={"from": s.email_from, "to": [to], "subject": subject, "html": html},
            )
        if r.status_code >= 400:
            log.warning("Resend send failed (%s): %s", r.status_code, r.text[:300])
            return False
        return True
    except httpx.RequestError as e:
        log.warning("Resend request error: %s", e)
        return False


def _link(path: str, token: str) -> str:
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}{path}?token={token}"


async def send_verify_email(to: str, token: str) -> bool:
    url = _link("/verify", token)
    html = (
        f"<p>Welcome to Content Engine!</p>"
        f"<p>Confirm your email to finish setting up your account:</p>"
        f'<p><a href="{url}">Verify my email</a></p>'
        f"<p>If you didn't sign up, ignore this message.</p>"
    )
    return await send_email(to, "Verify your email · Content Engine", html)


async def send_reset_email(to: str, token: str) -> bool:
    url = _link("/reset", token)
    html = (
        f"<p>Reset your Content Engine password:</p>"
        f'<p><a href="{url}">Choose a new password</a></p>'
        f"<p>This link expires in 1 hour. If you didn't request it, ignore this message.</p>"
    )
    return await send_email(to, "Reset your password · Content Engine", html)
