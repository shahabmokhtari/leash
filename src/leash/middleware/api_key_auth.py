"""ASGI middleware for optional API key authentication on /api/ paths."""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

_API_KEY_HEADER = b"x-api-key"


class ApiKeyAuthMiddleware:
    """Validate ``X-Api-Key`` header on ``/api/`` endpoints.

    * If no key is configured (env var ``LEASH_API_KEY`` is empty/unset and
      *api_key* constructor param is ``None``), all requests are allowed.
    * Non-API paths (static dashboard files) are never protected.
    * Comparison uses :func:`hmac.compare_digest` to prevent timing attacks.
    """

    def __init__(self, app: Any, api_key: str | None = None) -> None:
        self.app = app
        # Prefer environment variable, then fall back to explicit param
        self._api_key: str | None = os.environ.get("LEASH_API_KEY") or api_key or None

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not path.startswith("/api/") and path != "/api":
            await self.app(scope, receive, send)
            return

        # No key configured — allow everything (local/dev mode)
        if not self._api_key:
            await self.app(scope, receive, send)
            return

        # Extract the X-Api-Key header from ASGI scope
        headers = dict(scope.get("headers", []))
        provided_key_bytes = headers.get(_API_KEY_HEADER)

        if provided_key_bytes is None:
            client = scope.get("client", ("unknown",))
            logger.warning(
                "API request rejected: missing X-Api-Key header from %s",
                client[0] if client else "unknown",
            )
            await self._send_error(send, 401, "API key is required. Provide it via the X-Api-Key header.")
            return

        provided_key = provided_key_bytes.decode("utf-8", errors="replace")

        if not hmac.compare_digest(self._api_key, provided_key):
            client = scope.get("client", ("unknown",))
            logger.warning(
                "API request rejected: invalid API key from %s",
                client[0] if client else "unknown",
            )
            await self._send_error(send, 403, "Invalid API key.")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_error(send: Callable, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
