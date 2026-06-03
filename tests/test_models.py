"""Tests for Pydantic model serialization/deserialization."""

from __future__ import annotations

from datetime import datetime, timezone

from leash.models import (
    Configuration,
    HandlerConfig,
    HookInput,
    HookOutput,
    SessionData,
    SessionEvent,
)

# ---------------------------------------------------------------------------
# HookInput
# ---------------------------------------------------------------------------


class TestHookInput:
    def test_deserialize_from_camel_case_json(self):
        """HookInput should deserialize from camelCase JSON."""
        data = {
            "hookEventName": "PermissionRequest",
            "sessionId": "abc123",
            "toolName": "Bash",
            "toolInput": {"command": "git status"},
            "cwd": "/home/user/project",
        }
        inp = HookInput.model_validate(data)
        assert inp.hook_event_name == "PermissionRequest"
        assert inp.session_id == "abc123"
        assert inp.tool_name == "Bash"
        assert inp.cwd == "/home/user/project"
        assert inp.tool_input == {"command": "git status"}

    def test_json_round_trip(self):
        """HookInput should round-trip through JSON with camelCase aliases."""
        inp = HookInput(
            hook_event_name="PreToolUse",
            session_id="sess-1",
            tool_name="Read",
            cwd="/tmp",
        )
        dumped = inp.model_dump(by_alias=True)
        assert "hookEventName" in dumped
        assert "sessionId" in dumped
        assert dumped["hookEventName"] == "PreToolUse"

        restored = HookInput.model_validate(dumped)
        assert restored.hook_event_name == "PreToolUse"
        assert restored.session_id == "sess-1"

    def test_default_values(self):
        """HookInput defaults should be sensible."""
        inp = HookInput()
        assert inp.hook_event_name == ""
        assert inp.session_id == ""
        assert inp.tool_name is None
        assert inp.tool_input is None
        assert inp.cwd is None
        assert inp.provider == "claude"
        assert isinstance(inp.timestamp, datetime)

    def test_populate_by_field_name(self):
        """HookInput should accept both camelCase and snake_case."""
        inp = HookInput(hook_event_name="Stop", session_id="s1")
        assert inp.hook_event_name == "Stop"

        inp2 = HookInput.model_validate({"hookEventName": "Stop", "sessionId": "s1"})
        assert inp2.hook_event_name == "Stop"


# ---------------------------------------------------------------------------
# HookOutput
# ---------------------------------------------------------------------------


class TestHookOutput:
    def test_default_values(self):
        """HookOutput defaults should be false/zero."""
        out = HookOutput()
        assert out.auto_approve is False
        assert out.safety_score == 0
        assert out.reasoning == ""
        assert out.category == "unknown"
        assert out.threshold == 0
        assert out.system_message is None
        assert out.additional_context is None
        assert out.interrupt is False
        assert out.elapsed_ms == 0

    def test_serialization(self):
        """HookOutput should serialize to camelCase JSON."""
        out = HookOutput(auto_approve=True, safety_score=95, reasoning="Safe command", category="safe")
        dumped = out.model_dump(by_alias=True)
        assert dumped["autoApprove"] is True
        assert dumped["safetyScore"] == 95

    def test_round_trip(self):
        out = HookOutput(auto_approve=True, safety_score=42, reasoning="test", category="risky", threshold=90)
        dumped = out.model_dump(by_alias=True)
        restored = HookOutput.model_validate(dumped)
        assert restored.auto_approve is True
        assert restored.safety_score == 42
        assert restored.threshold == 90


# ---------------------------------------------------------------------------
# SessionData / SessionEvent
# ---------------------------------------------------------------------------


class TestSessionData:
    def test_initialization_with_session_id(self):
        """SessionData should initialize with session_id and empty history."""
        session = SessionData(session_id="test-session-123")
        assert session.session_id == "test-session-123"
        assert session.conversation_history is not None
        assert len(session.conversation_history) == 0
        assert session.start_time <= datetime.now(timezone.utc)

    def test_session_event_stores_permission_request(self):
        evt = SessionEvent(
            type="permission-request",
            tool_name="Bash",
            decision="auto-approved",
            safety_score=96,
        )
        assert evt.type == "permission-request"
        assert evt.tool_name == "Bash"
        assert evt.safety_score == 96

    def test_session_event_with_content(self):
        evt = SessionEvent(type="test", content="Hello world")
        assert evt.content == "Hello world"

    def test_session_data_with_events(self):
        session = SessionData(
            session_id="s1",
            conversation_history=[
                SessionEvent(type="a"),
                SessionEvent(type="b"),
            ],
        )
        assert len(session.conversation_history) == 2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_deserialization_from_dict(self):
        """Configuration should deserialize from a dict with defaults."""
        data = {
            "llm": {"provider": "claude-cli", "model": "sonnet", "timeout": 30000},
            "server": {"port": 5050, "host": "localhost"},
        }
        config = Configuration.model_validate(data)
        assert config is not None
        assert config.server.port == 5050
        assert config.server.host == "localhost"

    def test_defaults(self):
        config = Configuration()
        assert config.llm.provider == "claude-stream"
        assert config.llm.model == "opus"
        assert config.llm.timeout == 30000
        assert config.server.port == 5050
        assert config.enforcement_enabled is False

    def test_hook_handlers_dict(self):
        from leash.models import HookEventConfig

        config = Configuration(
            hook_handlers={
                "PermissionRequest": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(name="bash-analyzer", matcher="Bash"),
                        HandlerConfig(name="file-read", matcher="Read"),
                    ],
                )
            }
        )
        assert "PermissionRequest" in config.hook_handlers
        assert len(config.hook_handlers["PermissionRequest"].handlers) == 2


# ---------------------------------------------------------------------------
# HandlerConfig
# ---------------------------------------------------------------------------


class TestHandlerConfig:
    def test_matches_exact_tool_name(self):
        handler = HandlerConfig(matcher="Bash")
        assert handler.matches("Bash") is True
        assert handler.matches("Read") is False

    def test_matches_regex_pattern(self):
        handler = HandlerConfig(matcher="Write|Edit")
        assert handler.matches("Write") is True
        assert handler.matches("Edit") is True
        assert handler.matches("Read") is False

    def test_matches_mcp_regex(self):
        handler = HandlerConfig(matcher="mcp__.*")
        assert handler.matches("mcp__context7") is True
        assert handler.matches("mcp__workiq__ask") is True
        assert handler.matches("Bash") is False

    def test_matches_wildcard(self):
        handler = HandlerConfig(matcher="*")
        assert handler.matches("Anything") is True
        assert handler.matches("Bash") is True

    def test_matches_none_matcher(self):
        handler = HandlerConfig(matcher=None)
        assert handler.matches("Anything") is True

    def test_invalid_regex_fallback(self):
        """Invalid regex should fall back to case-insensitive literal match."""
        handler = HandlerConfig(matcher="[invalid")
        # Should not raise, but fall back to literal match
        assert handler.matches("[invalid") is True
        assert handler.matches("Bash") is False

    def test_profile_thresholds(self):
        handler = HandlerConfig(
            threshold=85,
            threshold_strict=95,
            threshold_moderate=85,
            threshold_permissive=70,
        )
        assert handler.get_threshold_for_profile("strict") == 95
        assert handler.get_threshold_for_profile("moderate") == 85
        assert handler.get_threshold_for_profile("permissive") == 70
        assert handler.get_threshold_for_profile("lockdown") == 101
        assert handler.get_threshold_for_profile("unknown") == 85
        assert handler.get_threshold_for_profile(None) == 85
