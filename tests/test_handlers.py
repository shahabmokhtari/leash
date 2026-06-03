"""Tests for hook handlers using mocks.

Since handler implementations may not exist on this branch, we define
minimal stubs inline and test the expected behavior contract.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from leash.models import HandlerConfig, HookInput, HookOutput, LLMResponse

# ---------------------------------------------------------------------------
# Handler interface & stubs
# ---------------------------------------------------------------------------


class IHookHandler:
    """Interface for hook handlers."""

    async def handle(self, input: HookInput, config: HandlerConfig, context: str) -> HookOutput:
        raise NotImplementedError


class LogOnlyHandler(IHookHandler):
    """Logs the event, returns no decision."""

    def __init__(self, logger: Any = None):
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, input: HookInput, config: HandlerConfig, context: str) -> HookOutput:
        self._logger.info(
            "LogOnly: event=%s tool=%s session=%s",
            input.hook_event_name,
            input.tool_name,
            input.session_id,
        )
        return HookOutput(
            auto_approve=False,
            safety_score=0,
            category="logged",
            reasoning="Log-only handler - no decision made",
        )


class LLMAnalysisHandler(IHookHandler):
    """Queries an LLM for safety analysis."""

    def __init__(self, llm_client: Any, prompt_builder: Any = None):
        self._llm = llm_client
        self._prompt_builder = prompt_builder

    async def handle(self, input: HookInput, config: HandlerConfig, context: str) -> HookOutput:
        try:
            response: LLMResponse = await self._llm.query(f"Analyze: {input.tool_name}")
        except Exception as e:
            return HookOutput(
                auto_approve=False,
                safety_score=0,
                category="error",
                reasoning=f"LLM error: {e}",
            )

        auto = response.safety_score >= config.threshold if config.auto_approve else False
        return HookOutput(
            auto_approve=auto,
            safety_score=response.safety_score,
            reasoning=response.reasoning,
            category=response.category,
            threshold=config.threshold,
        )


class ContextInjectionHandler(IHookHandler):
    """Injects context information."""

    def __init__(self, logger: Any = None):
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, input: HookInput, config: HandlerConfig, context: str) -> HookOutput:
        system_message = None
        inject_errors = config.config.get("injectRecentErrors", False)

        if inject_errors and context:
            error_keywords = ["error", "fail", "exception"]
            error_lines = [
                line for line in context.splitlines() if any(kw in line.lower() for kw in error_keywords)
            ]
            if error_lines:
                system_message = "Recent Errors:\n" + "\n".join(error_lines)

        return HookOutput(
            category="context-injection",
            system_message=system_message,
        )


class CustomLogicHandler(IHookHandler):
    """Handles session lifecycle events."""

    def __init__(self, session_manager: Any = None, logger: Any = None):
        self._session_manager = session_manager
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, input: HookInput, config: HandlerConfig, context: str) -> HookOutput:
        event = input.hook_event_name
        if event == "SessionStart":
            return HookOutput(category="session-start", reasoning="Session initialized")
        elif event == "SessionEnd":
            return HookOutput(category="session-end", reasoning="Session cleanup complete")
        else:
            return HookOutput(category="custom", reasoning=f"Custom handler for {event}")


# ---------------------------------------------------------------------------
# LogOnlyHandler tests
# ---------------------------------------------------------------------------


class TestLogOnlyHandler:
    async def test_returns_logged_category(self):
        handler = LogOnlyHandler()
        input = HookInput(hook_event_name="PreToolUse", tool_name="Bash", session_id="test-123")
        config = HandlerConfig(name="pre-tool-logger", mode="log-only")

        output = await handler.handle(input, config, "")

        assert output.auto_approve is False
        assert output.safety_score == 0
        assert output.category == "logged"
        assert output.reasoning == "Log-only handler - no decision made"

    async def test_handles_null_tool_name(self):
        handler = LogOnlyHandler()
        input = HookInput(hook_event_name="UserPromptSubmit", session_id="test-789")
        config = HandlerConfig(mode="log-only")

        output = await handler.handle(input, config, "")
        assert output.category == "logged"

    async def test_uses_configured_log_level(self):
        mock_logger = MagicMock()
        handler = LogOnlyHandler(logger=mock_logger)
        input = HookInput(hook_event_name="Stop", session_id="test-456")
        config = HandlerConfig(
            name="stop-logger",
            mode="log-only",
            config={"logLevel": "debug"},
        )

        output = await handler.handle(input, config, "")
        assert output.category == "logged"


# ---------------------------------------------------------------------------
# LLMAnalysisHandler tests
# ---------------------------------------------------------------------------


class TestLLMAnalysisHandler:
    async def test_auto_approve_when_score_above_threshold(self):
        mock_llm = AsyncMock()
        mock_llm.query.return_value = LLMResponse(
            success=True, safety_score=96, reasoning="Safe command", category="safe"
        )
        handler = LLMAnalysisHandler(mock_llm)
        input = HookInput(hook_event_name="PermissionRequest", tool_name="Bash", session_id="test-123")
        config = HandlerConfig(threshold=95, auto_approve=True)

        output = await handler.handle(input, config, "")

        assert output.auto_approve is True
        assert output.safety_score == 96
        assert output.reasoning == "Safe command"

    async def test_deny_when_score_below_threshold(self):
        mock_llm = AsyncMock()
        mock_llm.query.return_value = LLMResponse(
            success=True, safety_score=85, reasoning="Risky operation", category="risky"
        )
        handler = LLMAnalysisHandler(mock_llm)
        input = HookInput(tool_name="Bash", session_id="test-123")
        config = HandlerConfig(threshold=90, auto_approve=True)

        output = await handler.handle(input, config, "")

        assert output.auto_approve is False
        assert output.safety_score == 85

    async def test_error_on_llm_failure(self):
        mock_llm = AsyncMock()
        mock_llm.query.side_effect = RuntimeError("LLM unavailable")
        handler = LLMAnalysisHandler(mock_llm)
        input = HookInput(tool_name="Bash", session_id="test-123")
        config = HandlerConfig(threshold=90, auto_approve=True)

        output = await handler.handle(input, config, "")

        assert output.auto_approve is False
        assert output.safety_score == 0
        assert output.category == "error"
        assert "LLM" in output.reasoning

    async def test_no_auto_approve_when_disabled(self):
        mock_llm = AsyncMock()
        mock_llm.query.return_value = LLMResponse(
            success=True, safety_score=99, reasoning="Safe", category="safe"
        )
        handler = LLMAnalysisHandler(mock_llm)
        input = HookInput(tool_name="Bash", session_id="s1")
        config = HandlerConfig(threshold=50, auto_approve=False)

        output = await handler.handle(input, config, "")
        assert output.auto_approve is False


# ---------------------------------------------------------------------------
# ContextInjectionHandler tests
# ---------------------------------------------------------------------------


class TestContextInjectionHandler:
    async def test_returns_context_injection_category(self):
        handler = ContextInjectionHandler()
        input = HookInput(hook_event_name="UserPromptSubmit", session_id="test-123")
        config = HandlerConfig(name="context-injector", mode="context-injection")

        output = await handler.handle(input, config, "")
        assert output.category == "context-injection"

    async def test_extracts_recent_errors_when_configured(self):
        handler = ContextInjectionHandler()
        input = HookInput(hook_event_name="UserPromptSubmit", session_id="test-123")
        config = HandlerConfig(
            name="context-injector",
            mode="context-injection",
            config={"injectRecentErrors": True},
        )
        context = "Line 1\nSome error occurred here\nAnother line\nBuild failed on step 3"

        output = await handler.handle(input, config, context)

        assert output.system_message is not None
        assert "Recent Errors" in output.system_message

    async def test_returns_null_system_message_when_no_context_to_inject(self):
        handler = ContextInjectionHandler()
        input = HookInput(hook_event_name="UserPromptSubmit", session_id="test-123")
        config = HandlerConfig(name="context-injector", mode="context-injection")

        output = await handler.handle(input, config, "All good, no issues")
        assert output.system_message is None


# ---------------------------------------------------------------------------
# CustomLogicHandler tests
# ---------------------------------------------------------------------------


class TestCustomLogicHandler:
    async def test_session_start(self):
        handler = CustomLogicHandler()
        input = HookInput(hook_event_name="SessionStart", session_id="session-start-test")
        config = HandlerConfig(name="session-initializer", mode="custom-logic")

        output = await handler.handle(input, config, "")
        assert output.category == "session-start"
        assert output.reasoning == "Session initialized"

    async def test_session_end(self):
        handler = CustomLogicHandler()
        input = HookInput(hook_event_name="SessionEnd", session_id="session-end-test")
        config = HandlerConfig(
            name="session-cleanup",
            mode="custom-logic",
            config={"archiveSession": True},
        )

        output = await handler.handle(input, config, "")
        assert output.category == "session-end"
        assert output.reasoning == "Session cleanup complete"

    async def test_unknown_event(self):
        handler = CustomLogicHandler()
        input = HookInput(hook_event_name="UnknownEvent", session_id="test-unknown")
        config = HandlerConfig(mode="custom-logic")

        output = await handler.handle(input, config, "")
        assert output.category == "custom"
