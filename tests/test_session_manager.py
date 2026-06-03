"""Tests for SessionManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from leash.models import SessionData, SessionEvent

# ---------------------------------------------------------------------------
# SessionManager stub
#
# The real SessionManager may not exist on this branch yet.
# We create a minimal async implementation for testing purposes.
# ---------------------------------------------------------------------------


class SessionManager:
    """Minimal SessionManager for testing file-based session storage."""

    def __init__(self, storage_dir: str | Path, max_history_per_session: int = 50):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._max_history = max_history_per_session
        self._sessions: dict[str, SessionData] = {}

    def _validate_session_id(self, session_id: str) -> None:
        if ".." in session_id or "/" in session_id or "\\" in session_id:
            raise ValueError(f"Invalid session id: {session_id}")

    def _session_path(self, session_id: str) -> Path:
        return self._storage_dir / f"{session_id}.json"

    async def get_or_create_session(self, session_id: str) -> SessionData:
        self._validate_session_id(session_id)
        if session_id in self._sessions:
            return self._sessions[session_id]
        path = self._session_path(session_id)
        if path.exists():
            data = json.loads(path.read_text())
            session = SessionData.model_validate(data)
        else:
            session = SessionData(session_id=session_id)
        self._sessions[session_id] = session
        return session

    async def record_event(self, session_id: str, event: SessionEvent) -> None:
        self._validate_session_id(session_id)
        session = await self.get_or_create_session(session_id)
        session.conversation_history.append(event)
        # Trim to max
        if len(session.conversation_history) > self._max_history:
            session.conversation_history = session.conversation_history[-self._max_history :]
        # Persist
        path = self._session_path(session_id)
        path.write_text(session.model_dump_json(by_alias=True, indent=2))

    async def build_context(self, session_id: str, max_events: int = 5) -> str:
        session = await self.get_or_create_session(session_id)
        recent = session.conversation_history[-max_events:]
        lines = []
        for evt in recent:
            parts = [f"[{evt.type}]"]
            if evt.tool_name:
                parts.append(f"tool={evt.tool_name}")
            if evt.decision:
                parts.append(f"decision={evt.decision}")
            if evt.content:
                parts.append(evt.content)
            lines.append(" ".join(parts))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionManager:
    async def test_create_and_get_session(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "sessions")
        session = await manager.get_or_create_session("test-123")

        assert session is not None
        assert session.session_id == "test-123"
        assert len(session.conversation_history) == 0

    async def test_record_event_adds_to_history(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "sessions")
        await manager.get_or_create_session("test-123")

        evt = SessionEvent(type="permission-request", tool_name="Bash", decision="auto-approved")
        await manager.record_event("test-123", evt)

        session = await manager.get_or_create_session("test-123")
        assert len(session.conversation_history) == 1
        assert session.conversation_history[0].tool_name == "Bash"

    async def test_build_context_returns_recent_history(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "sessions", max_history_per_session=5)

        for i in range(10):
            await manager.record_event(
                "test-123", SessionEvent(type="test", content=f"Event {i}")
            )

        context = await manager.build_context("test-123", max_events=3)
        assert "Event 9" in context
        assert "Event 8" in context
        assert "Event 7" in context
        assert "Event 0" not in context

    async def test_path_traversal_rejection(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "sessions")

        with pytest.raises(ValueError):
            await manager.get_or_create_session("../../../etc/passwd")

        with pytest.raises(ValueError):
            await manager.record_event("../bad", SessionEvent(type="test"))

    async def test_history_trimming(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "sessions", max_history_per_session=5)

        for i in range(10):
            await manager.record_event(
                "trim-test", SessionEvent(type="test", content=f"Event {i}")
            )

        session = await manager.get_or_create_session("trim-test")
        assert len(session.conversation_history) == 5
        # Should keep the last 5 events
        assert session.conversation_history[0].content == "Event 5"
        assert session.conversation_history[4].content == "Event 9"

    async def test_persistence_across_instances(self, tmp_path: Path):
        """Data recorded by one manager should be loadable by another."""
        storage = tmp_path / "sessions"
        manager1 = SessionManager(storage)
        await manager1.record_event(
            "persist-test", SessionEvent(type="test", content="hello")
        )

        manager2 = SessionManager(storage)
        session = await manager2.get_or_create_session("persist-test")
        assert len(session.conversation_history) == 1
        assert session.conversation_history[0].content == "hello"
