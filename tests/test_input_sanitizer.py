"""Tests for InputSanitizer security module."""

from __future__ import annotations

import pytest

from leash.security.input_sanitizer import (
    MAX_SESSION_ID_LENGTH,
    MAX_TOOL_INPUT_LENGTH,
    InputSanitizer,
)

# ---------------------------------------------------------------------------
# is_valid_session_id
# ---------------------------------------------------------------------------


class TestIsValidSessionId:
    @pytest.mark.parametrize(
        "session_id",
        [
            "abc123",
            "test-session-456",
            "session_with_underscores",
            "ABC-123-def",
        ],
    )
    def test_accepts_valid_ids(self, session_id: str):
        assert InputSanitizer.is_valid_session_id(session_id) is True

    @pytest.mark.parametrize(
        "session_id",
        [
            None,
            "",
            "   ",
            "../../../etc/passwd",
            "session/with/slashes",
            "session\\with\\backslashes",
            "session with spaces",
            "session;drop table",
            "session<script>alert(1)</script>",
        ],
    )
    def test_rejects_invalid_ids(self, session_id: str | None):
        assert InputSanitizer.is_valid_session_id(session_id) is False

    def test_rejects_overlong_ids(self):
        long_id = "a" * (MAX_SESSION_ID_LENGTH + 1)
        assert InputSanitizer.is_valid_session_id(long_id) is False

    def test_accepts_max_length_id(self):
        max_id = "a" * MAX_SESSION_ID_LENGTH
        assert InputSanitizer.is_valid_session_id(max_id) is True


# ---------------------------------------------------------------------------
# is_valid_tool_name
# ---------------------------------------------------------------------------


class TestIsValidToolName:
    @pytest.mark.parametrize(
        ("tool_name", "expected"),
        [
            (None, True),  # Optional field
            ("", True),  # Optional field
            ("Bash", True),
            ("file.read", True),
            ("mcp:tool-name", True),
            ("Tool_Name-123", True),
        ],
    )
    def test_accepts_valid_names(self, tool_name: str | None, expected: bool):
        assert InputSanitizer.is_valid_tool_name(tool_name) is expected

    @pytest.mark.parametrize(
        "tool_name",
        [
            "tool with spaces",
            "tool;injection",
            "tool<script>",
            "tool/path/traversal",
        ],
    )
    def test_rejects_invalid_names(self, tool_name: str):
        assert InputSanitizer.is_valid_tool_name(tool_name) is False

    def test_rejects_overlong_names(self):
        long_name = "a" * 257
        assert InputSanitizer.is_valid_tool_name(long_name) is False


# ---------------------------------------------------------------------------
# is_valid_hook_event_name
# ---------------------------------------------------------------------------


class TestIsValidHookEventName:
    @pytest.mark.parametrize(
        ("hook_event_name", "expected"),
        [
            ("PermissionRequest", True),
            ("PreToolUse", True),
            ("hook-event-123", True),
        ],
    )
    def test_accepts_valid_names(self, hook_event_name: str, expected: bool):
        assert InputSanitizer.is_valid_hook_event_name(hook_event_name) is expected

    @pytest.mark.parametrize(
        "hook_event_name",
        [
            None,
            "",
            "event with spaces",
            "event;injection",
        ],
    )
    def test_rejects_invalid_names(self, hook_event_name: str | None):
        assert InputSanitizer.is_valid_hook_event_name(hook_event_name) is False


# ---------------------------------------------------------------------------
# is_tool_input_within_limits
# ---------------------------------------------------------------------------


class TestIsToolInputWithinLimits:
    def test_accepts_none(self):
        assert InputSanitizer.is_tool_input_within_limits(None) is True

    def test_accepts_small_input(self):
        assert InputSanitizer.is_tool_input_within_limits({"command": "git status"}) is True

    def test_rejects_very_large_input(self):
        huge = {"data": "x" * (MAX_TOOL_INPUT_LENGTH + 100)}
        assert InputSanitizer.is_tool_input_within_limits(huge) is False


# ---------------------------------------------------------------------------
# sanitize_for_prompt
# ---------------------------------------------------------------------------


class TestSanitizeForPrompt:
    def test_handles_none(self):
        assert InputSanitizer.sanitize_for_prompt(None) == ""

    def test_handles_empty_string(self):
        assert InputSanitizer.sanitize_for_prompt("") == ""

    def test_truncates_long_input(self):
        long_input = "x" * (MAX_TOOL_INPUT_LENGTH + 100)
        result = InputSanitizer.sanitize_for_prompt(long_input)
        assert "TRUNCATED" in result

    def test_preserves_normal_input(self):
        text = "git status"
        assert InputSanitizer.sanitize_for_prompt(text) == text
