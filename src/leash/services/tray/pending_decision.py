"""Pending decision queue for interactive tray notifications.

Coordinates pending interactive decisions between the HTTP hook request
and the tray notification / web dashboard.  Uses asyncio.Future to hold
the HTTP request open until the user responds or timeout occurs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from leash.models.tray_models import NotificationInfo, PendingDecisionInfo, TrayDecision

logger = logging.getLogger(__name__)


@dataclass
class PendingDecision:
    """Internal state for a single pending decision."""

    id: str
    future: asyncio.Future[TrayDecision | None]
    info: NotificationInfo
    timeout_handle: asyncio.TimerHandle | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PendingDecisionService:
    """Manages pending interactive decisions between hook requests and tray / dashboard UI."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingDecision] = {}

    def create_pending(
        self,
        info: NotificationInfo,
        timeout: float,
    ) -> tuple[str, asyncio.Future[TrayDecision | None]]:
        """Create a pending decision that holds open until resolved or timed out.

        Args:
            info: Notification details for the decision.
            timeout: Seconds before the decision auto-expires (returns None).

        Returns:
            Tuple of (decision_id, future that resolves to the user's choice or None).
        """
        decision_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TrayDecision | None] = loop.create_future()

        def _on_timeout() -> None:
            pending = self._pending.pop(decision_id, None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(None)
                logger.debug("Pending decision %s timed out after %.1fs", decision_id, timeout)

        timeout_handle = loop.call_later(timeout, _on_timeout)

        self._pending[decision_id] = PendingDecision(
            id=decision_id,
            future=future,
            info=info,
            timeout_handle=timeout_handle,
        )
        logger.debug("Created pending decision %s for %s", decision_id, info.tool_name)
        return decision_id, future

    def try_resolve(self, decision_id: str, decision: TrayDecision) -> bool:
        """Resolve a pending decision with the user's choice.

        Returns True if the decision was found and resolved.
        """
        pending = self._pending.pop(decision_id, None)
        if pending is None:
            return False

        if pending.timeout_handle is not None:
            pending.timeout_handle.cancel()
        if not pending.future.done():
            pending.future.set_result(decision)
        logger.debug("Resolved pending decision %s with %s", decision_id, decision)
        return True

    def cancel(self, decision_id: str) -> bool:
        """Cancel a pending decision (returns None to the waiting HTTP request).

        Returns True if the decision was found and cancelled.
        """
        pending = self._pending.pop(decision_id, None)
        if pending is None:
            return False

        if pending.timeout_handle is not None:
            pending.timeout_handle.cancel()
        if not pending.future.done():
            pending.future.set_result(None)
        logger.debug("Cancelled pending decision %s", decision_id)
        return True

    def get_pending(self) -> list[PendingDecisionInfo]:
        """Get all currently pending decisions (for web dashboard fallback)."""
        return [
            PendingDecisionInfo(
                id=p.id,
                info=p.info,
                created_at=p.created_at.isoformat(),
            )
            for p in self._pending.values()
        ]
