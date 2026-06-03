"""Session management with per-session locking and JSON file storage."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from leash.exceptions import StorageException
from leash.models.session_data import SessionData, SessionEvent
from leash.security import InputSanitizer

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages Claude Code sessions with async file I/O and per-session locks."""

    def __init__(self, storage_dir: str = "~/.leash/sessions", max_history_size: int = 50) -> None:
        self._storage_dir = Path(storage_dir).expanduser().resolve()
        self._max_history_size = max_history_size
        self._session_cache: dict[str, SessionData] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise StorageException(
                f"Cannot create session storage directory '{self._storage_dir}': Permission denied. "
                "Ensure the application has write access to this location or configure a different StorageDir."
            ) from e
        except OSError as e:
            raise StorageException(
                f"Cannot create session storage directory '{self._storage_dir}': {e}"
            ) from e

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock.

        Uses setdefault for atomic insert-or-get to avoid TOCTOU races
        when two coroutines request the same new session_id concurrently.
        """
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _validate_session_id(self, session_id: str) -> None:
        """Validate session ID to prevent path traversal and injection."""
        if not session_id or not session_id.strip():
            raise ValueError("Session ID cannot be empty")
        if not InputSanitizer.is_valid_session_id(session_id):
            raise ValueError(f"Session ID contains invalid characters: {session_id}")

    def _get_session_file_path(self, session_id: str) -> Path:
        """Get the file path for a session, with path traversal protection."""
        self._validate_session_id(session_id)

        file_path = self._storage_dir / f"{session_id}.json"
        resolved = file_path.resolve()

        # Defense in depth: verify resolved path is within storage directory
        try:
            resolved.relative_to(self._storage_dir)
        except ValueError:
            raise ValueError(f"Path traversal detected in session ID: {session_id}")

        return resolved

    async def get_or_create_session(self, session_id: str) -> SessionData:
        """Get an existing session or create a new one."""
        self._validate_session_id(session_id)

        # Fast path: check cache
        if session_id in self._session_cache:
            return self._session_cache[session_id]

        # Slow path: load from disk or create
        async with self._global_lock:
            # Double-check after acquiring lock
            if session_id in self._session_cache:
                return self._session_cache[session_id]

            file_path = self._get_session_file_path(session_id)

            if file_path.exists():
                try:
                    async with aiofiles.open(file_path, "r") as f:
                        raw = await f.read()
                    data = json.loads(raw)
                    session = SessionData.model_validate(data)

                    if session.session_id != session_id:
                        logger.error(
                            "Session file %s has mismatched SessionId: expected %s, found %s",
                            file_path, session_id, session.session_id,
                        )
                        raise StorageException("Session file corruption detected: SessionId mismatch")

                    if session.conversation_history is None:
                        logger.warning("Session %s has null ConversationHistory, initializing empty list", session_id)
                        session.conversation_history = []

                except json.JSONDecodeError as e:
                    logger.error("Failed to parse session file %s for session %s", file_path, session_id)
                    raise StorageException(
                        f"Cannot load session {session_id}: Session file is corrupted. "
                        f"Session history has been lost. File: {file_path}"
                    ) from e
                except OSError as e:
                    logger.error("Failed to read session file %s for session %s", file_path, session_id)
                    raise StorageException(
                        f"Cannot load session {session_id}: File system error. "
                        f"Check disk health and permissions. File: {file_path}"
                    ) from e
            else:
                session = SessionData(session_id=session_id)
                await self._save_session_internal(session)

            self._session_cache[session_id] = session
            return session

    async def record_event(self, session_id: str, event: SessionEvent) -> None:
        """Record a session event, trimming history to max size."""
        session = await self.get_or_create_session(session_id)
        lock = self._get_lock(session_id)

        async with lock:
            session.conversation_history.append(event)
            session.last_activity = datetime.now(timezone.utc)

            # Trim history if exceeds max size
            while len(session.conversation_history) > self._max_history_size:
                session.conversation_history.pop(0)

            await self._save_session_internal(session)

    async def build_context(self, session_id: str, max_events: int = 10) -> str:
        """Build a text summary of recent session events."""
        session = await self.get_or_create_session(session_id)
        recent_events = session.conversation_history[-max_events:]

        lines: list[str] = ["RECENT SESSION HISTORY:"]

        for evt in recent_events:
            lines.append(f"[{evt.timestamp.strftime('%H:%M:%S')}] {evt.type}")

            if evt.tool_name:
                lines.append(f"  Tool: {evt.tool_name}")

            if evt.decision:
                lines.append(f"  Decision: {evt.decision} (Score: {evt.safety_score})")

            if evt.content:
                lines.append(f"  Content: {evt.content}")

        return "\n".join(lines) + "\n"

    async def get_all_sessions(self) -> list[SessionData]:
        """Read all session JSON files from storage directory."""
        sessions: list[SessionData] = []

        if not self._storage_dir.exists():
            return sessions

        for file_path in self._storage_dir.glob("*.json"):
            try:
                async with aiofiles.open(file_path, "r") as f:
                    raw = await f.read()
                data = json.loads(raw)
                session = SessionData.model_validate(data)
                sessions.append(session)
            except Exception as e:
                logger.warning("Failed to load session file %s: %s", file_path, e)

        return sessions

    async def clear_all_sessions(self) -> int:
        """Delete all session files and return the count deleted."""
        if not self._storage_dir.exists():
            return 0

        deleted = 0
        for file_path in self._storage_dir.glob("*.json"):
            try:
                file_path.unlink()
                deleted += 1
            except Exception as e:
                logger.warning("Failed to delete session file %s: %s", file_path, e)

        # Clear caches
        self._session_cache.clear()

        logger.info("Cleared %d session files", deleted)
        return deleted

    async def _save_session(self, session: SessionData) -> None:
        """Save session with per-session locking."""
        lock = self._get_lock(session.session_id)
        async with lock:
            await self._save_session_internal(session)

    async def _save_session_internal(self, session: SessionData) -> None:
        """Write session data to disk atomically."""
        file_path = self._get_session_file_path(session.session_id)

        try:
            data = session.model_dump(by_alias=True, mode="json")
            raw = json.dumps(data, indent=2)
            # Atomic write: write to temp file then rename to avoid corruption
            tmp_path = file_path.with_suffix(".tmp")
            async with aiofiles.open(tmp_path, "w") as f:
                await f.write(raw)
            tmp_path.replace(file_path)
        except PermissionError as e:
            logger.error("Failed to save session %s to %s: Permission denied", session.session_id, file_path)
            raise StorageException(
                f"Failed to save session {session.session_id}: Permission denied. Check file system permissions."
            ) from e
        except OSError as e:
            logger.error("Failed to save session %s to %s: %s", session.session_id, file_path, e)
            raise StorageException(
                f"Failed to save session {session.session_id}: {e}"
            ) from e
