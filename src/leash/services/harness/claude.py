"""Claude Code harness client implementation.

Handles Claude-specific input mapping, response formatting, transcript parsing,
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

_PASSTHROUGH_TOOLS: frozenset[str] = frozenset({"AskUserQuestion"})


class ClaudeHarnessClient:
    """Claude Code client implementation."""

    def __init__(self) -> None:
        self._transcript_dir = os.path.join(str(Path.home()), ".claude", "projects")

    @property
    def name(self) -> str:
        return "claude"

    @property
    def display_name(self) -> str:
        return "Claude Code"

    # -- Hook Input / Output --------------------------------------------------

    def map_input(self, raw_input: dict[str, Any], hook_event: str) -> HookInput:
        """Map raw JSON from Claude Code hook into a normalised HookInput."""
        session_id = raw_input.get("sessionId") or raw_input.get("session_id") or ""
        tool_name = raw_input.get("toolName") or raw_input.get("tool_name")
        tool_input = raw_input.get("toolInput") or raw_input.get("tool_input")
        cwd = raw_input.get("cwd")

        return HookInput(
            hook_event_name=hook_event,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            cwd=cwd,
            timestamp=datetime.now(timezone.utc),
            provider=self.name,
        )

    def format_response(self, hook_event: str, output: HookOutput) -> dict[str, Any]:
        """Format a HookOutput into the JSON structure Claude Code expects."""
        if hook_event == "SessionStart":
            return self._format_session_start_response(output)
        if hook_event == "PermissionRequest":
            return self._format_permission_response(output)
        if hook_event == "PreToolUse":
            return self._format_pre_tool_response(output)
        if hook_event == "PostToolUse":
            return self._format_post_tool_response(output)
        return {}

    def format_passthrough(self) -> dict[str, Any]:
        return {}

    def normalize_event_name(self, raw_event: str) -> str:
        """Claude uses PascalCase already -- return as-is."""
        return raw_event

    def is_passthrough_tool(self, tool_name: str) -> bool:
        return tool_name.lower() in {t.lower() for t in _PASSTHROUGH_TOOLS}

    # -- Transcripts ----------------------------------------------------------

    def get_transcript_directory(self) -> str | None:
        return self._transcript_dir if os.path.isdir(self._transcript_dir) else None

    def discover_projects(self) -> list[ClaudeProject]:
        projects: list[ClaudeProject] = []
        if not os.path.isdir(self._transcript_dir):
            return projects
        try:
            for entry in os.scandir(self._transcript_dir):
                if entry.is_dir():
                    sessions = self.get_sessions_for_project(entry.path)
                    # Derive CWD from the most recent session's JSONL metadata
                    cwd = None
                    branch = None
                    for s in sessions:
                        if s.cwd and not cwd:
                            cwd = s.cwd
                        if s.branch and not branch:
                            branch = s.branch
                        if cwd and branch:
                            break
                    projects.append(
                        ClaudeProject(
                            name=entry.name,
                            path=entry.path,
                            provider=self.name,
                            cwd=cwd,
                            branch=branch,
                            sessions=sessions,
                        )
                    )
        except OSError:
            pass
        return projects

    def get_sessions_for_project(self, project_path: str) -> list[ClaudeSession]:
        sessions: list[ClaudeSession] = []
        try:
            for entry in os.scandir(project_path):
                if entry.is_file() and entry.name.endswith(".jsonl"):
                    stat = entry.stat()
                    session = ClaudeSession(
                        session_id=Path(entry.name).stem,
                        file_path=entry.path,
                        last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        size_bytes=stat.st_size,
                        provider=self.name,
                    )
                    self._read_session_metadata(entry.path, session)
                    sessions.append(session)

                # Scan for subagent transcripts in {session-id}/subagents/
                if entry.is_dir():
                    subagents_dir = os.path.join(entry.path, "subagents")
                    if os.path.isdir(subagents_dir):
                        try:
                            for sub_entry in os.scandir(subagents_dir):
                                if sub_entry.is_file() and sub_entry.name.endswith(".jsonl"):
                                    sub_stat = sub_entry.stat()
                                    sub_session = ClaudeSession(
                                        session_id=Path(sub_entry.name).stem,
                                        file_path=sub_entry.path,
                                        last_modified=datetime.fromtimestamp(sub_stat.st_mtime, tz=timezone.utc),
                                        size_bytes=sub_stat.st_size,
                                        provider=self.name,
                                        parent_session_id=entry.name,
                                    )
                                    self._read_session_metadata(sub_entry.path, sub_session)
                                    sessions.append(sub_session)
                        except OSError:
                            pass
        except OSError:
            pass
        sessions.sort(key=lambda s: s.last_modified, reverse=True)
        return sessions

    def find_transcript_file(self, session_id: str) -> str | None:
        if not os.path.isdir(self._transcript_dir):
            return None
        sid_lower = session_id.lower()
        try:
            for project_entry in os.scandir(self._transcript_dir):
                if not project_entry.is_dir():
                    continue
                for file_entry in os.scandir(project_entry.path):
                    # Top-level JSONL files
                    if file_entry.is_file() and file_entry.name.endswith(".jsonl"):
                        if Path(file_entry.name).stem.lower() == sid_lower:
                            return file_entry.path
                    # Subagent directories: {session-id}/subagents/*.jsonl
                    if file_entry.is_dir():
                        subagents_dir = os.path.join(file_entry.path, "subagents")
                        if os.path.isdir(subagents_dir):
                            try:
                                for sub_entry in os.scandir(subagents_dir):
                                    if sub_entry.is_file() and sub_entry.name.endswith(".jsonl"):
                                        if Path(sub_entry.name).stem.lower() == sid_lower:
                                            return sub_entry.path
                            except OSError:
                                pass
        except OSError:
            pass
        return None

    @staticmethod
    def _extract_text_content(content: Any) -> str | None:
        """Extract plain text from Claude message content (str or list of blocks)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        return text
        return None

    @staticmethod
    def _read_session_metadata(file_path: str, session: ClaudeSession) -> None:
        """Scan the beginning of a Claude JSONL (up to 30 lines) to extract session metadata and title."""
        try:
            with open(file_path, encoding="utf-8") as f:
                for _ in range(30):
                    raw_line = f.readline()
                    if not raw_line:
                        break  # EOF
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "cwd" in data and not session.cwd:
                        session.cwd = data["cwd"]
                    if "gitBranch" in data and not session.branch:
                        session.branch = data["gitBranch"]
                    if "agentId" in data and not session.agent_id:
                        session.agent_id = data["agentId"]
                    if "slug" in data and not session.slug:
                        session.slug = data["slug"]
                    # Extract title from first user message
                    if not session.title:
                        msg = data.get("message")
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            text = ClaudeHarnessClient._extract_text_content(msg.get("content"))
                            if text:
                                session.title = text[:250]
                    # Stop early if we have all key fields
                    if session.cwd and session.branch and session.title:
                        break
        except Exception:
            logger.warning("Failed to read session metadata from %s", file_path, exc_info=True)

    def parse_transcript_line(self, json_line: str) -> TranscriptEntry | None:
        """Parse a Claude JSONL line into a TranscriptEntry."""
        data = json.loads(json_line)
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
            provider=self.name,
        )

    # -- Settings & Configuration ---------------------------------------------

    def get_settings_file_path(self) -> str | None:
        return os.path.join(str(Path.home()), ".claude", "settings.json")

    def get_default_prompt_template(self, event_name: str) -> str | None:
        return {
            "PreToolUse": "pre-tool-use-prompt.txt",
            "PostToolUse": "post-tool-validation-prompt.txt",
            "PermissionRequest": "bash-prompt.txt",
        }.get(event_name)

    # -- Private formatting helpers -------------------------------------------

    @staticmethod
    def _format_permission_response(output: HookOutput) -> dict[str, Any]:
        behavior = "allow" if output.auto_approve else "deny"
        decision: dict[str, Any] = {"behavior": behavior}
        if not output.auto_approve:
            reasoning = output.reasoning[:1000] if len(output.reasoning) > 1000 else output.reasoning
            decision["message"] = (
                f"Safety score {output.safety_score} below threshold {output.threshold}. {reasoning}"
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": decision,
            }
        }

    @staticmethod
    def _format_pre_tool_response(output: HookOutput) -> dict[str, Any]:
        # Tray decisions override score-based logic
        if output.tray_decision == "tray-denied":
            permission_decision = "deny"
        elif output.tray_decision == "tray-approved":
            permission_decision = "allow"
        elif output.tray_decision in ("tray-ignored", "tray-timeout"):
            permission_decision = "ask"
        elif output.auto_approve:
            permission_decision = "allow"
        elif output.safety_score >= output.threshold:
            permission_decision = "allow"
        elif output.safety_score < 30:
            permission_decision = "deny"
        else:
            permission_decision = "ask"

        hook_output: dict[str, Any] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission_decision,
        }
        if permission_decision != "allow":
            reasoning = output.reasoning[:1000] if len(output.reasoning) > 1000 else output.reasoning
            hook_output["permissionDecisionReason"] = reasoning

        return {"hookSpecificOutput": hook_output}

    @staticmethod
    def _format_post_tool_response(output: HookOutput) -> dict[str, Any]:
        context = output.additional_context or output.system_message
        if context:
            context = context[:500] if len(context) > 500 else context
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": context,
                }
            }
        return {}

    @staticmethod
    def _format_session_start_response(output: HookOutput) -> dict[str, Any]:
        response: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
            }
        }

        if output.additional_context:
            context = (
                output.additional_context[:500]
                if len(output.additional_context) > 500
                else output.additional_context
            )
            response["hookSpecificOutput"]["additionalContext"] = context

        if output.system_message:
            message = output.system_message[:500] if len(output.system_message) > 500 else output.system_message
            response["systemMessage"] = message

        return response
