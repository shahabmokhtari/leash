"""ASGI middleware that adds standard HTTP security headers to all responses."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """Add security headers (CSP, X-Frame-Options, cache-control, etc.) to every HTTP response."""

    # Headers are byte-pairs for ASGI compatibility.
    SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-xss-protection", b"1; mode=block"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (
            b"content-security-policy",
            b"default-src 'self'; script-src 'self' 'unsafe-inline'; "
            b"style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            b"font-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        ),
        (b"cache-control", b"no-store, no-cache, must-revalidate"),
        (b"pragma", b"no-cache"),
    ]

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Collect existing header names so we don't duplicate
                existing = {h[0] for h in headers}
                for name, value in self.SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
