"""GitHub Copilot CLI harness client implementation.

Handles Copilot-specific input mapping, response formatting, transcript parsing,
and settings paths.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput
from leash.services.transcript_watcher import ClaudeProject, ClaudeSession, TranscriptEntry

logger = logging.getLogger(__name__)

_EVENT_MAP: dict[str, str] = {
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "preToolUseFailure": "PreToolUse",
    "postToolUseFailure": "PostToolUseFailure",
    "sessionStart": "SessionStart",
}


class CopilotHarnessClient:
    """GitHub Copilot CLI client implementation."""

    def __init__(self) -> None:
        self._transcript_dir = os.path.join(str(Path.home()), ".copilot", "session-state")

    @property
    def name(self) -> str:
        return "copilot"

    @property
    def display_name(self) -> str:
        return "GitHub Copilot CLI"

    # -- Hook Input / Output --------------------------------------------------

    def map_input(self, raw_input: dict[str, Any], hook_event: str) -> HookInput:
        """Map raw JSON from Copilot hook into a normalised HookInput."""
        normalized_event = self.normalize_event_name(hook_event)

        session_id = raw_input.get("sessionId") or raw_input.get("session_id") or ""
        tool_name = raw_input.get("toolName") or raw_input.get("tool_name")
        cwd = raw_input.get("cwd")

        # Copilot sends toolArgs as a JSON string
        tool_input: dict[str, Any] | None = None
        tool_args = raw_input.get("toolArgs")
        if tool_args is not None:
            if isinstance(tool_args, str):
                try:
                    tool_input = json.loads(tool_args)
                except json.JSONDecodeError:
                    tool_input = {"command": tool_args}
            elif isinstance(tool_args, dict):
                tool_input = tool_args
        else:
            tool_input = raw_input.get("toolInput") or raw_input.get("tool_input")

        # Copilot may send epoch milliseconds for timestamp
        timestamp = datetime.now(timezone.utc)
        ts_raw = raw_input.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            try:
                timestamp = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                pass

        return HookInput(
            hook_event_name=normalized_event,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            cwd=cwd,
            timestamp=timestamp,
            provider=self.name,
        )

    def format_response(self, hook_event: str, output: HookOutput) -> dict[str, Any]:
        """Format a HookOutput into the JSON structure Copilot expects.

        permissionDecision: allow | deny | ask
        """
        normalized = self.normalize_event_name(hook_event)
        if normalized == "PreToolUse":
            # Tray decisions override score-based logic
            if output.tray_decision == "tray-denied":
                decision = "deny"
            elif output.tray_decision in ("tray-ignored", "tray-timeout"):
                decision = "ask"
            elif output.tray_decision == "tray-approved" or output.auto_approve:
                decision = "allow"
            elif output.safety_score >= output.threshold:
                decision = "allow"
            else:
                decision = "deny"

            response: dict[str, Any] = {"permissionDecision": decision}
            if decision != "allow":
                reasoning = output.reasoning[:1000] if len(output.reasoning) > 1000 else output.reasoning
                response["permissionDecisionReason"] = reasoning
            return response

        return {}

    def format_passthrough(self) -> dict[str, Any]:
        return {}

    def normalize_event_name(self, raw_event: str) -> str:
        """Normalise Copilot camelCase event names to PascalCase."""
        if raw_event in _EVENT_MAP:
            return _EVENT_MAP[raw_event]
        # Fallback: capitalise first letter
        if raw_event:
            return raw_event[0].upper() + raw_event[1:]
        return raw_event

    def is_passthrough_tool(self, tool_name: str) -> bool:
        """Copilot has no passthrough tools."""
        return False

    # -- Transcripts ----------------------------------------------------------

    def get_transcript_directory(self) -> str | None:
        return self._transcript_dir if os.path.isdir(self._transcript_dir) else None

    def discover_projects(self) -> list[ClaudeProject]:
        projects: list[ClaudeProject] = []
        if not os.path.isdir(self._transcript_dir):
            return projects

        sessions = self._get_copilot_sessions()
        if not sessions:
            return projects

        # Group sessions by cwd
        by_cwd: dict[str, list[ClaudeSession]] = {}
        for session in sessions:
            key = session.cwd or "Unknown"
            by_cwd.setdefault(key, []).append(session)

        for cwd, group_sessions in by_cwd.items():
            with_git = next((s for s in group_sessions if s.git_root), group_sessions[0])
            folder_name = os.path.basename(cwd.rstrip("\\/")) if cwd != "Unknown" else "Unknown"

            projects.append(
                ClaudeProject(
                    name=folder_name or cwd,
                    path=cwd if cwd != "Unknown" else self._transcript_dir,
                    provider=self.name,
                    cwd=None if cwd == "Unknown" else cwd,
                    git_root=with_git.git_root,
                    branch=with_git.branch,
                    repository=with_git.repository,
                    sessions=sorted(group_sessions, key=lambda s: s.last_modified, reverse=True),
                )
            )

        return projects

    def get_sessions_for_project(self, project_path: str) -> list[ClaudeSession]:
        """For Copilot, sessions are directories under the session-state root."""
        return self._get_copilot_sessions()

    def find_transcript_file(self, session_id: str) -> str | None:
        if not os.path.isdir(self._transcript_dir):
            return None
        events_file = os.path.join(self._transcript_dir, session_id, "events.jsonl")
        return events_file if os.path.isfile(events_file) else None

    def parse_transcript_line(self, json_line: str) -> TranscriptEntry | None:
        """Parse a Copilot events.jsonl line into a TranscriptEntry."""
        data = json.loads(json_line)
        entry = TranscriptEntry(
            provider=self.name,
            type=data.get("type"),
            uuid=data.get("id"),
            parent_uuid=data.get("parentId"),
            timestamp=data.get("timestamp"),
            data=data.get("data"),
        )

        # Map Copilot data into message for display compatibility
        entry_data = data.get("data")
        if isinstance(entry_data, dict):
            content = entry_data.get("content")
            if entry.type == "user.message" and content is not None:
                entry.message = {"role": "user", "content": content}
            elif entry.type == "assistant.message" and content is not None:
                entry.message = {"role": "assistant", "content": content}

        return entry

    # -- Settings & Configuration ---------------------------------------------

    def get_settings_file_path(self) -> str | None:
        return os.path.join(str(Path.home()), ".copilot", "hooks", "hooks.json")

    def get_default_prompt_template(self, event_name: str) -> str | None:
        return {
            "PreToolUse": "pre-tool-use-prompt.txt",
            "PostToolUse": "post-tool-validation-prompt.txt",
        }.get(event_name)

    # -- Private helpers ------------------------------------------------------

    def _get_copilot_sessions(self) -> list[ClaudeSession]:
        sessions: list[ClaudeSession] = []
        if not os.path.isdir(self._transcript_dir):
            return sessions

        try:
            for entry in os.scandir(self._transcript_dir):
                if not entry.is_dir():
                    continue
                events_file = os.path.join(entry.path, "events.jsonl")
                if not os.path.isfile(events_file):
                    continue
                try:
                    stat = os.stat(events_file)
                    session = ClaudeSession(
                        session_id=entry.name,
                        file_path=events_file,
                        last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        size_bytes=stat.st_size,
                        provider=self.name,
                    )
                    self._read_session_start_metadata(events_file, session)
                    sessions.append(session)
                except OSError:
                    pass
        except OSError:
            pass

        sessions.sort(key=lambda s: s.last_modified, reverse=True)
        return sessions

    @staticmethod
    def _parse_simple_yaml(path: str) -> dict[str, str]:
        """Minimal top-level key: value YAML parser (avoids pyyaml dependency)."""
        result: dict[str, str] = {}
        try:
            if os.path.getsize(path) > 64 * 1024:
                logger.debug("Skipping oversized YAML file (>64KB): %s", path)
                return result
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n\r")
                    if not line or line.startswith("#") or line[0] in (" ", "\t"):
                        continue
                    colon = line.find(":")
                    if colon < 1:
                        continue
                    key = line[:colon].strip()
                    value = line[colon + 1:].strip()
                    # Strip optional quotes
                    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                        value = value[1:-1]
                    if key and value:
                        result[key] = value
        except (OSError, UnicodeDecodeError):
            logger.debug("Failed to parse YAML from %s", path, exc_info=True)
        return result

    @staticmethod
    def _read_session_start_metadata(events_file: str, session: ClaudeSession) -> None:
        """Read session metadata from events.jsonl and workspace.yaml."""
        try:
            with open(events_file, encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line:
                data = json.loads(first_line)
                if data.get("type") == "session.start":
                    ctx = data.get("data", {}).get("context", {})
                    session.cwd = ctx.get("cwd")
                    session.git_root = ctx.get("gitRoot")
                    session.branch = ctx.get("branch")
                    session.repository = ctx.get("repository")
        except Exception:
            logger.debug("Failed to read Copilot session metadata from %s", events_file, exc_info=True)

        # Try to extract title from workspace.yaml in the same session directory
        session_dir = os.path.dirname(events_file)
        workspace_yaml = os.path.join(session_dir, "workspace.yaml")
        if os.path.isfile(workspace_yaml):
            ws = CopilotHarnessClient._parse_simple_yaml(workspace_yaml)
            if ws.get("summary") and not session.title:
                session.title = ws["summary"][:250]
            if ws.get("repository") and not session.repository:
                session.repository = ws["repository"]
            if ws.get("branch") and not session.branch:
                session.branch = ws["branch"]
            if ws.get("cwd") and not session.cwd:
                session.cwd = ws["cwd"]
