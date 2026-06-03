"""LLM analysis handler -- queries an LLM client and applies threshold logic."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from leash.models.handler_config import HandlerConfig
from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput
from leash.models.llm_response import LLMResponse

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# LLM client protocol (implemented by various clients in services/)
# ------------------------------------------------------------------
@runtime_checkable
class LLMClient(Protocol):
    """Minimal protocol for an LLM client that the handler depends on."""

    async def query(self, prompt: str) -> LLMResponse: ...


# ------------------------------------------------------------------
# PromptBuilder stub
# ------------------------------------------------------------------
# The real PromptBuilder lives in leash.services and will be implemented
# by another worker.  We provide a thin local stub so this module can be
# imported and tested independently.
# ------------------------------------------------------------------
try:
    from leash.services.prompt_builder import PromptBuilder  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover

    class PromptBuilder:  # type: ignore[no-redef]
        """Stub PromptBuilder -- returns a simple concatenated prompt."""

        @staticmethod
        def build(
            template_path: str | None,
            tool_name: str | None,
            cwd: str | None,
            tool_input: dict[str, Any] | None,
            session_context: str,
        ) -> str:
            parts = [f"Tool: {tool_name or 'unknown'}"]
            if cwd:
                parts.append(f"CWD: {cwd}")
            if tool_input:
                parts.append(f"Input: {tool_input}")
            if session_context:
                parts.append(f"Context: {session_context}")
            return "\n".join(parts)


class LLMAnalysisHandler:
    """Queries an LLM to score tool-use safety and applies threshold logic."""

    def __init__(self, llm_client: LLMClient, prompt_template: str | None = None) -> None:
        self._llm_client = llm_client
        self._prompt_template = prompt_template

    async def handle(
        self,
        input: HookInput,
        config: HandlerConfig,
        session_context: str,
    ) -> HookOutput:
        prompt = PromptBuilder.build(
            self._prompt_template,
            input.tool_name,
            input.cwd,
            input.tool_input,
            session_context,
        )

        llm_response = await self._llm_client.query(prompt)

        if not llm_response.success:
            return HookOutput(
                auto_approve=False,
                safety_score=0,
                reasoning=llm_response.error or "LLM query failed",
                category="error",
                threshold=config.threshold,
                elapsed_ms=llm_response.elapsed_ms,
            )

        # Clamp safety score to valid range to prevent LLM manipulation
        clamped_score = max(0, min(100, llm_response.safety_score))

        auto_approve = config.auto_approve and clamped_score >= config.threshold

        return HookOutput(
            auto_approve=auto_approve,
            safety_score=clamped_score,
            reasoning=llm_response.reasoning,
            category=llm_response.category,
            threshold=config.threshold,
            interrupt=llm_response.category == "dangerous",
            elapsed_ms=llm_response.elapsed_ms,
        )
