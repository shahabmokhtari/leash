"""Input sanitization to prevent prompt injection and other attacks."""

from __future__ import annotations

import json
import re

MAX_SESSION_ID_LENGTH = 128
MAX_TOOL_NAME_LENGTH = 256
MAX_HOOK_EVENT_NAME_LENGTH = 128
MAX_TOOL_INPUT_LENGTH = 1_000_000  # 1MB of text

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_.:]+$")
_HOOK_EVENT_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")


class InputSanitizer:
    """Validates and sanitizes user-provided input."""

    @staticmethod
    def is_valid_session_id(session_id: str | None) -> bool:
        if not session_id or not session_id.strip():
            return False
        if len(session_id) > MAX_SESSION_ID_LENGTH:
            return False
        return bool(_SESSION_ID_RE.match(session_id))

    @staticmethod
    def is_valid_tool_name(tool_name: str | None) -> bool:
        if not tool_name:
            return True  # tool_name is optional
        if len(tool_name) > MAX_TOOL_NAME_LENGTH:
            return False
        return bool(_TOOL_NAME_RE.match(tool_name))

    @staticmethod
    def is_valid_hook_event_name(hook_event_name: str | None) -> bool:
        if not hook_event_name or not hook_event_name.strip():
            return False
        if len(hook_event_name) > MAX_HOOK_EVENT_NAME_LENGTH:
            return False
        return bool(_HOOK_EVENT_NAME_RE.match(hook_event_name))

    @staticmethod
    def is_tool_input_within_limits(tool_input: dict | None) -> bool:
        if tool_input is None:
            return True
        raw = json.dumps(tool_input)
        return len(raw) <= MAX_TOOL_INPUT_LENGTH

    @staticmethod
    def sanitize_for_prompt(text: str | None) -> str:
        if not text:
            return ""
        if len(text) > MAX_TOOL_INPUT_LENGTH:
            return text[:MAX_TOOL_INPUT_LENGTH] + "... [TRUNCATED]"
        return text
