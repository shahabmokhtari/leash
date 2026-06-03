"""Tests for PendingDecisionService."""

from __future__ import annotations

import asyncio
import uuid

from leash.models.tray_models import NotificationInfo, NotificationLevel, TrayDecision

# ---------------------------------------------------------------------------
# PendingDecisionService stub
# ---------------------------------------------------------------------------


class PendingDecisionEntry:
    def __init__(self, id: str, info: NotificationInfo, timeout_seconds: float):
        self.id = id
        self.info = info
        self._future: asyncio.Future[TrayDecision | None] = asyncio.get_event_loop().create_future()
        self._timeout = timeout_seconds
        self._resolved = False

    @property
    def task(self) -> asyncio.Future[TrayDecision | None]:
        return self._future


class PendingDecisionService:
    """Manages pending interactive decisions with timeout."""

    def __init__(self):
        self._pending: dict[str, PendingDecisionEntry] = {}

    def create_pending(
        self, info: NotificationInfo, timeout_seconds: float = 10.0
    ) -> tuple[str, asyncio.Future[TrayDecision | None]]:
        id_ = uuid.uuid4().hex[:8]
        entry = PendingDecisionEntry(id_, info, timeout_seconds)
        self._pending[id_] = entry

        # Schedule timeout
        async def _timeout():
            await asyncio.sleep(timeout_seconds)
            if not entry._future.done():
                entry._future.set_result(None)
                entry._resolved = True

        asyncio.ensure_future(_timeout())
        return id_, entry.task

    def try_resolve(self, id_: str, decision: TrayDecision) -> bool:
        entry = self._pending.get(id_)
        if entry is None:
            return False
        if entry._future.done():
            return False
        entry._future.set_result(decision)
        entry._resolved = True
        return True

    def cancel(self, id_: str) -> bool:
        entry = self._pending.get(id_)
        if entry is None:
            return False
        if entry._future.done():
            return False
        entry._future.set_result(None)
        entry._resolved = True
        return True

    def get_pending(self) -> list[PendingDecisionEntry]:
        return [e for e in self._pending.values() if not e._future.done()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_info(tool: str = "Bash") -> NotificationInfo:
    return NotificationInfo(
        title="Test",
        body="Test body",
        tool_name=tool,
        safety_score=50,
        level=NotificationLevel.WARNING,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPendingDecisionService:
    async def test_create_pending_returns_id_and_task(self):
        service = PendingDecisionService()
        id_, task = service.create_pending(_create_info(), timeout_seconds=10)

        assert id_ is not None
        assert len(id_) > 0
        assert not task.done()

    async def test_resolve_approve(self):
        service = PendingDecisionService()
        id_, task = service.create_pending(_create_info(), timeout_seconds=10)

        resolved = service.try_resolve(id_, TrayDecision.APPROVE)

        assert resolved is True
        assert task.done()
        assert await task == TrayDecision.APPROVE

    async def test_resolve_deny(self):
        service = PendingDecisionService()
        id_, task = service.create_pending(_create_info(), timeout_seconds=10)

        resolved = service.try_resolve(id_, TrayDecision.DENY)

        assert resolved is True
        assert task.done()
        assert await task == TrayDecision.DENY

    async def test_resolve_invalid_id(self):
        service = PendingDecisionService()
        resolved = service.try_resolve("nonexistent", TrayDecision.APPROVE)
        assert resolved is False

    async def test_resolve_already_resolved(self):
        service = PendingDecisionService()
        id_, _ = service.create_pending(_create_info(), timeout_seconds=10)

        service.try_resolve(id_, TrayDecision.APPROVE)
        second = service.try_resolve(id_, TrayDecision.DENY)

        assert second is False

    async def test_cancel_returns_none(self):
        service = PendingDecisionService()
        id_, task = service.create_pending(_create_info(), timeout_seconds=10)

        cancelled = service.cancel(id_)

        assert cancelled is True
        assert task.done()
        assert await task is None

    async def test_cancel_invalid_id(self):
        service = PendingDecisionService()
        assert service.cancel("nonexistent") is False

    async def test_timeout_returns_none(self):
        service = PendingDecisionService()
        _, task = service.create_pending(_create_info(), timeout_seconds=0.05)

        result = await task
        assert result is None

    async def test_get_pending_returns_unresolved(self):
        service = PendingDecisionService()
        service.create_pending(_create_info("Bash"), timeout_seconds=10)
        service.create_pending(_create_info("Read"), timeout_seconds=10)

        pending = service.get_pending()
        assert len(pending) == 2

    async def test_get_pending_excludes_resolved(self):
        service = PendingDecisionService()
        id1, _ = service.create_pending(_create_info("Bash"), timeout_seconds=10)
        service.create_pending(_create_info("Read"), timeout_seconds=10)

        service.try_resolve(id1, TrayDecision.APPROVE)
        pending = service.get_pending()

        assert len(pending) == 1
        assert pending[0].info.tool_name == "Read"
