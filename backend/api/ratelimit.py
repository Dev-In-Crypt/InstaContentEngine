"""Shared slowapi limiter (per-IP). Imported by main (to register the handler +
app.state) and by routers (for @limiter.limit decorators).

headers_enabled is intentionally OFF: with it on, slowapi injects X-RateLimit-*
headers and therefore requires every decorated endpoint to declare a
`response: Response` parameter. Our auth endpoints return Pydantic models, so
enabling headers makes slowapi raise on a None response. 429-on-exceed still
works without the informational headers."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, headers_enabled=False)
