"""ASGI middleware for per-IP sliding-window rate limiting on /api/ paths."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_REQUESTS = 600
DEFAULT_WINDOW_SECONDS = 60
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


class RateLimitingMiddleware:
    """Sliding-window rate limiter.

    Tracks request timestamps per client IP using a :class:`collections.deque`.
    Only ``/api/`` paths are rate-limited; all other paths pass through.
    """

    def __init__(
        self,
        app: Any,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self.app = app
        self.max_requests = max_requests
        self.window_seconds = window_seconds

        # Per-IP tracker: IP -> deque of timestamps (floats, monotonic)
        self._clients: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

        # Background cleanup task (started lazily on first request)
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not path.startswith("/api/") and path != "/api":
            await self.app(scope, receive, send)
            return

        # Ensure the cleanup task is running
        self._ensure_cleanup_task()

        client_ip = self._extract_client_ip(scope)
        allowed = await self._try_acquire(client_ip)

        if not allowed:
            logger.warning("Rate limit exceeded for %s", client_ip)
            await self._send_429(send)
            return

        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_client_ip(self, scope: dict) -> str:
        """Best-effort client IP from the ASGI scope."""
        client = scope.get("client")
        if client:
            return client[0]
        return "unknown"

    async def _try_acquire(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            dq = self._clients.get(client_ip)
            if dq is None:
                dq = deque()
                self._clients[client_ip] = dq

            # Evict expired timestamps
            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= self.max_requests:
                return False

            dq.append(now)
            return True

    async def _send_429(self, send: Callable) -> None:
        body = json.dumps({"error": "Rate limit exceeded. Try again later."}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(self.window_seconds).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------------
    # Periodic cleanup
    # ------------------------------------------------------------------

    def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._cleanup_loop())
            except RuntimeError:
                pass  # No running loop yet; will retry on next call

    async def _cleanup_loop(self) -> None:
        """Remove IP entries whose newest timestamp is older than 2x the window."""
        try:
            while True:
                await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
                await self._cleanup_expired()
        except asyncio.CancelledError:
            pass

    async def _cleanup_expired(self) -> None:
        cutoff = time.monotonic() - (self.window_seconds * 2)
        async with self._lock:
            expired_ips = [
                ip for ip, dq in self._clients.items() if not dq or dq[-1] < cutoff
            ]
            for ip in expired_ips:
                del self._clients[ip]
            if expired_ips:
                logger.debug("Rate limiter cleaned up %d expired IP entries", len(expired_ips))
