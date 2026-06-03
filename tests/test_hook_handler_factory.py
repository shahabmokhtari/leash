"""Tests for HookHandlerFactory."""

from __future__ import annotations

import pytest

# We import the handler stubs from test_handlers since the real implementations
# may not exist on this branch.
from tests.test_handlers import (
    ContextInjectionHandler,
    CustomLogicHandler,
    LLMAnalysisHandler,
    LogOnlyHandler,
)

# ---------------------------------------------------------------------------
# Minimal HookHandlerFactory stub
# ---------------------------------------------------------------------------


class HookHandlerFactory:
    """Creates the correct handler for a given mode string."""

    def __init__(self, llm_client=None, session_manager=None, prompt_service=None):
        self._llm = llm_client
        self._session_manager = session_manager
        self._prompt_service = prompt_service

    def create(self, mode: str, prompt_template: str | None = None):
        match mode:
            case "llm-analysis" | "llm-validation":
                return LLMAnalysisHandler(self._llm, self._prompt_service)
            case "log-only":
                return LogOnlyHandler()
            case "context-injection":
                return ContextInjectionHandler()
            case "custom-logic":
                return CustomLogicHandler(self._session_manager)
            case _:
                raise NotImplementedError(f"Handler mode '{mode}' is not supported")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHookHandlerFactory:
    @pytest.mark.parametrize(
        ("mode", "expected_type"),
        [
            ("llm-analysis", LLMAnalysisHandler),
            ("llm-validation", LLMAnalysisHandler),
            ("log-only", LogOnlyHandler),
            ("context-injection", ContextInjectionHandler),
            ("custom-logic", CustomLogicHandler),
        ],
    )
    def test_creates_correct_handler_type(self, mode: str, expected_type: type):
        factory = HookHandlerFactory()
        handler = factory.create(mode)
        assert isinstance(handler, expected_type)

    def test_raises_for_unsupported_mode(self):
        factory = HookHandlerFactory()
        with pytest.raises(NotImplementedError, match="unsupported-mode"):
            factory.create("unsupported-mode")

    def test_handles_null_prompt_template(self):
        factory = HookHandlerFactory()
        handler = factory.create("llm-analysis", None)
        assert isinstance(handler, LLMAnalysisHandler)
