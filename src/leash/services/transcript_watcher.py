"""Watches transcript directories for changes and provides project/session discovery.

Monitors ~/.claude/projects/ (and ~/.copilot/session-state/) for transcript changes,
tracks per-file read positions for incremental reads, and fires callbacks for SSE consumers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TranscriptEntry:
    """A single entry from a JSONL transcript file."""

    type: str | None = None
    uuid: str | None = None
    parent_uuid: str | None = None
    session_id: str | None = None
    timestamp: str | None = None
    version: str | None = None
    cwd: str | None = None
    message: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    provider: str | None = None

    def get_message_summary(self) -> str | None:
        """Extract a display-friendly summary of the message content."""
        if self.message is None:
            return None
        try:
            msg = self.message
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list) and len(content) > 0:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        return first["text"]
                role = msg.get("role")
                if role:
                    return f"[{role}]"
            if isinstance(msg, str):
                return msg
        except Exception:
            pass
        return None

    def get_role(self) -> str | None:
        """Extract role from message if present."""
        if not isinstance(self.message, dict):
            return None
        return self.message.get("role")


@dataclass
class ClaudeSession:
    """A transcript session (JSONL file)."""

    session_id: str = ""
    file_path: str = ""
    last_modified: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    size_bytes: int = 0
    provider: str = "claude"
    cwd: str | None = None
    git_root: str | None = None
    branch: str | None = None
    repository: str | None = None
    parent_session_id: str | None = None
    agent_id: str | None = None
    slug: str | None = None
    title: str | None = None


@dataclass
class ClaudeProject:
    """A project directory containing transcript sessions."""

    name: str = ""
    path: str = ""
    provider: str = "claude"
    cwd: str | None = None
    git_root: str | None = None
    branch: str | None = None
    repository: str | None = None
    sessions: list[ClaudeSession] = field(default_factory=list)


@dataclass
class TranscriptEvent:
    """Event fired when new transcript entries are detected."""

    session_id: str
    new_entries: list[TranscriptEntry]


# Callback type for transcript update subscribers
TranscriptCallback = Callable[[TranscriptEvent], None]


# ---------------------------------------------------------------------------
# Path decoding
# ---------------------------------------------------------------------------


def decode_claude_project_path(encoded: str) -> str:
    """Decode a Claude project directory name back to the original filesystem path.

    E.g. "C--Users-alice-source-repos-ClaudeObserver"
      -> "C:\\Users\\alice\\source\\repos\\ClaudeObserver"
    """
    if not encoded:
        return encoded

    # Pattern: drive letter followed by -- (e.g. "C--")
    if len(encoded) >= 3 and encoded[0].isalpha() and encoded[1] == "-" and encoded[2] == "-":
        return encoded[0] + ":\\" + encoded[3:].replace("-", "\\")

    # Fallback: replace dashes with OS separator
    return encoded.replace("-", os.sep)


# ---------------------------------------------------------------------------
# TranscriptWatcher
# ---------------------------------------------------------------------------


class TranscriptWatcher:
    """Watches transcript directories for changes and provides project/session discovery.

    Uses watchfiles for background monitoring. Tracks per-file read positions so
    only new JSONL lines are delivered to subscribers.
    """

    def __init__(self) -> None:
        self._file_positions: dict[str, int] = {}
        self._subscribers: list[TranscriptCallback] = []
        self._watch_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._harness_clients: list[Any] = []  # set via set_harness_clients
        self._projects_cache: list[ClaudeProject] | None = None
        self._resolve_symlinks: bool = False

    # -- Harness integration --------------------------------------------------

    def set_harness_clients(self, clients: list[Any]) -> None:
        """Inject harness clients for multi-client support."""
        self._harness_clients = list(clients)

    def set_resolve_symlinks(self, enabled: bool) -> None:
        """Enable or disable symlink resolution for transcript paths."""
        if self._resolve_symlinks != enabled:
            self._resolve_symlinks = enabled
            self.invalidate_projects_cache()

    # -- Subscriber management ------------------------------------------------

    def subscribe(self, callback: TranscriptCallback) -> None:
        """Register a callback for transcript update events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: TranscriptCallback) -> None:
        """Remove a previously registered callback."""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def _notify(self, event: TranscriptEvent) -> None:
        # Snapshot to avoid RuntimeError if a callback modifies the list.
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception:
                logger.debug("Error in transcript subscriber callback", exc_info=True)

    # -- Project / session discovery ------------------------------------------

    def invalidate_projects_cache(self) -> None:
        """Clear the cached projects so the next get_projects() rescans."""
        self._projects_cache = None

    async def preload_projects(self) -> None:
        """Scan transcript directories in a background thread and cache the result.

        Call this at startup so the first user request to the transcripts page
        doesn't pay the full directory-scan cost.
        """
        try:
            projects = await asyncio.get_running_loop().run_in_executor(
                None, self._discover_projects_sync,
            )
            self._projects_cache = projects
            session_count = sum(len(p.sessions) for p in projects)
            logger.info(
                "Transcript preload complete: %d projects, %d sessions",
                len(projects), session_count,
            )
        except Exception:
            logger.debug("Background transcript preload failed", exc_info=True)

    def _discover_projects_sync(self) -> list[ClaudeProject]:
        """Synchronous project discovery across all harness clients."""
        all_projects: list[ClaudeProject] = []
        for client in self._harness_clients:
            try:
                all_projects.extend(client.discover_projects())
            except Exception:
                logger.debug("Error discovering projects for %s", getattr(client, "name", "unknown"), exc_info=True)
        if self._resolve_symlinks:
            self._apply_symlink_resolution(all_projects)
        return self._merge_projects_by_folder(all_projects)

    @staticmethod
    def _apply_symlink_resolution(projects: list[ClaudeProject]) -> None:
        """Resolve symlinks in cwd, path, and git_root fields."""
        for project in projects:
            for attr in ("cwd", "path", "git_root"):
                original = getattr(project, attr)
                if original:
                    resolved = os.path.realpath(original)
                    if resolved != original:
                        logger.debug("Symlink resolved project.%s: %s -> %s", attr, original, resolved)
                    setattr(project, attr, resolved)
            for session in project.sessions:
                for attr in ("cwd", "git_root"):
                    original = getattr(session, attr)
                    if original:
                        resolved = os.path.realpath(original)
                        if resolved != original:
                            logger.debug("Symlink resolved session.%s: %s -> %s", attr, original, resolved)
                        setattr(session, attr, resolved)

    def get_projects(self) -> list[ClaudeProject]:
        """Return cached projects if available, otherwise discover and cache.

        Callers that need non-blocking behavior should use ``get_projects_async``
        instead to avoid blocking the event loop during the first (uncached) call.
        """
        if self._projects_cache is not None:
            return self._projects_cache
        self._projects_cache = self._discover_projects_sync()
        return self._projects_cache

    async def get_projects_async(self) -> list[ClaudeProject]:
        """Return cached projects, or discover in a thread pool to avoid blocking the event loop."""
        if self._projects_cache is not None:
            return self._projects_cache
        projects = await asyncio.get_running_loop().run_in_executor(
            None, self._discover_projects_sync,
        )
        self._projects_cache = projects
        return self._projects_cache

    def get_transcript(self, session_id: str) -> list[TranscriptEntry]:
        """Read all JSONL entries from the transcript file for a given session."""
        entries: list[TranscriptEntry] = []
        file_path, client = self._find_transcript_file_with_client(session_id)
        if file_path is None or not os.path.isfile(file_path):
            return entries

        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = self._parse_line(line, client)
                        if entry is not None:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            logger.warning("Failed to read transcript for session %s", session_id, exc_info=True)

        return entries

    def find_transcript_file(self, session_id: str) -> str | None:
        """Find the transcript file path for a given session ID."""
        file_path, _ = self._find_transcript_file_with_client(session_id)
        return file_path

    def _find_transcript_file_with_client(self, session_id: str) -> tuple[str | None, Any | None]:
        for client in self._harness_clients:
            try:
                fp = client.find_transcript_file(session_id)
                if fp is not None:
                    return fp, client
            except Exception:
                pass
        return None, None

    # -- Background watching --------------------------------------------------

    def start(self) -> None:
        """Start background file watching in the current event loop."""
        if self._watch_task is not None:
            return
        self._stop_event.clear()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.debug("TranscriptWatcher started")

    def stop(self) -> None:
        """Stop background file watching."""
        self._stop_event.set()
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        logger.debug("TranscriptWatcher stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop using watchfiles."""
        try:
            import watchfiles
        except ImportError:
            logger.warning("watchfiles not installed; transcript watching disabled")
            return

        dirs_to_watch: list[str] = []
        dir_to_client: dict[str, Any] = {}

        for client in self._harness_clients:
            try:
                tdir = client.get_transcript_directory()
                if tdir and os.path.isdir(tdir):
                    dirs_to_watch.append(tdir)
                    dir_to_client[tdir] = client
            except Exception:
                pass

        if not dirs_to_watch:
            logger.debug("No transcript directories to watch")
            return

        logger.debug("Watching transcript directories: %s", dirs_to_watch)

        try:
            async for changes in watchfiles.awatch(*dirs_to_watch, stop_event=self._stop_event):
                for _change_type, path_str in changes:
                    if not path_str.endswith(".jsonl"):
                        continue
                    self._projects_cache = None  # invalidate on any transcript change
                    try:
                        client = self._resolve_client_for_path(path_str, dir_to_client)
                        new_entries = self._read_new_entries(path_str, client)
                        if new_entries:
                            # Determine session_id based on client
                            client_name = getattr(client, "name", "claude") if client else "claude"
                            if client_name == "copilot":
                                session_id = os.path.basename(os.path.dirname(path_str))
                            else:
                                session_id = Path(path_str).stem
                            self._notify(TranscriptEvent(session_id=session_id, new_entries=new_entries))
                    except Exception:
                        logger.debug("Error processing transcript change: %s", path_str, exc_info=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Watch loop ended", exc_info=True)

    def _resolve_client_for_path(self, file_path: str, dir_to_client: dict[str, Any]) -> Any | None:
        """Resolve which harness client owns a given file path."""
        for directory, client in dir_to_client.items():
            # Use os.path for proper directory containment check (avoids
            # false positives like "/foo/bar" matching "/foo/barbaz").
            if file_path.startswith(directory + os.sep) or file_path == directory:
                return client
        # Fallback: first client (typically claude)
        return self._harness_clients[0] if self._harness_clients else None

    def _read_new_entries(self, file_path: str, client: Any | None) -> list[TranscriptEntry]:
        """Read new JSONL lines from file since last known position."""
        entries: list[TranscriptEntry] = []
        last_pos = self._file_positions.get(file_path, 0)

        try:
            with open(file_path, "rb") as f:
                file_size = f.seek(0, 2)  # seek to end
                if file_size <= last_pos:
                    return entries

                f.seek(last_pos)
                raw = f.read()
                self._file_positions[file_path] = f.tell()

            for line in raw.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = self._parse_line(line, client)
                    if entry is not None:
                        entries.append(entry)
                except json.JSONDecodeError:
                    pass
        except OSError:
            logger.debug("Could not read new entries from %s", file_path, exc_info=True)

        return entries

    @staticmethod
    def _parse_line(line: str, client: Any | None) -> TranscriptEntry | None:
        """Parse a JSONL line, optionally delegating to a harness client."""
        if client is not None and hasattr(client, "parse_transcript_line"):
            return client.parse_transcript_line(line)
        # Default: generic JSON parse
        data = json.loads(line)
        return TranscriptEntry(
            type=data.get("type"),
            uuid=data.get("uuid"),
            parent_uuid=data.get("parentUuid"),
            session_id=data.get("sessionId"),
            timestamp=data.get("timestamp"),
            version=data.get("version"),
            cwd=data.get("cwd"),
            message=data.get("message"),
            data=data.get("data"),
            provider=data.get("provider"),
        )

    # -- Merge helpers --------------------------------------------------------

    @staticmethod
    def _merge_projects_by_folder(projects: list[ClaudeProject]) -> list[ClaudeProject]:
        """Merge projects from different clients that share the same working directory."""
        by_folder: dict[str, ClaudeProject] = {}

        for project in projects:
            cwd = project.cwd
            if not cwd and project.provider == "claude":
                # Try to get CWD from the most recent session's JSONL metadata
                for s in project.sessions:
                    if s.cwd:
                        cwd = s.cwd
                        break
            if not cwd and project.provider == "claude":
                cwd = decode_claude_project_path(project.name)
            if not cwd:
                cwd = project.path

            key = cwd.rstrip("\\/")
            if sys.platform == "win32":
                key = key.lower()

            if key in by_folder:
                existing = by_folder[key]
                existing.sessions.extend(project.sessions)
                if not existing.git_root:
                    existing.git_root = project.git_root
                if not existing.branch:
                    existing.branch = project.branch
                if not existing.repository:
                    existing.repository = project.repository
            else:
                folder_name = os.path.basename(key)
                by_folder[key] = ClaudeProject(
                    name=folder_name if folder_name else key,
                    path=project.path,
                    provider=project.provider,
                    cwd=cwd,
                    git_root=project.git_root,
                    branch=project.branch,
                    repository=project.repository,
                    sessions=list(project.sessions),
                )

        # Sort sessions within each merged project by last modified descending
        for project in by_folder.values():
            project.sessions.sort(key=lambda s: s.last_modified, reverse=True)

        return list(by_folder.values())
