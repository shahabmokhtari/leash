"""Shared base class for all LLM client implementations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from leash.models.llm_response import LLMResponse

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import LlmConfig
    from leash.services.terminal_output_service import TerminalOutputService

logger = logging.getLogger(__name__)

MAX_TIMEOUT_MS = 300_000  # 5 minutes
MAX_OUTPUT_SIZE = 1_048_576  # 1MB

# Shared model name mapping: shorthand → full Claude model ID.
# Used by CLI clients and the Anthropic API client alike.
MODEL_MAPPING: dict[str, str] = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus": "claude-opus-4-6-20250918",
    "haiku": "claude-haiku-4-5-20251001",
}


def resolve_model_name(model: str) -> str:
    """Map a shorthand model name to its full Claude model ID.

    Returns the input unchanged if no mapping exists.
    """
    return MODEL_MAPPING.get(model.lower(), model)


def resolve_model_for_provider(config: Any, provider: str | None = None) -> str:
    """Resolve the model for a specific provider.

    Checks ``config.llm.provider_models`` for a per-provider override,
    falling back to the global ``config.llm.model``.

    An explicit empty string override (e.g. provider_models={"copilot-cli": ""})
    is honored and returned as-is, meaning "let the provider use its default".
    Only when the provider key is absent do we fall back to the global model.
    """
    llm = config.llm if hasattr(config, "llm") else config
    target = provider or (llm.provider if hasattr(llm, "provider") else "")
    per_provider = getattr(llm, "provider_models", {}) or {}
    if target in per_provider:
        return per_provider[target]

    model = getattr(llm, "model", "") or ""

    # Copilot expects provider-native model IDs (e.g. ``gpt-5.4`` or
    # ``claude-sonnet-4.6``).  The global default ``opus``/``sonnet`` aliases
    # are Claude-specific shorthands and should not leak into Copilot when the
    # user has not explicitly chosen a Copilot model.
    if isinstance(target, str) and target.startswith("copilot") and model.lower() in MODEL_MAPPING:
        return ""

    return model


class LLMClientBase:
    """Shared infrastructure for all LLM client implementations.

    Provides timeout resolution, error response factories, prompt preview helpers,
    and optional terminal output forwarding.
    """

    def __init__(
        self,
        config_manager: ConfigurationManager | None = None,
        initial_config: LlmConfig | None = None,
        terminal_output: TerminalOutputService | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._initial_config = initial_config
        self._terminal_output = terminal_output

    def _push_terminal(self, source: str, level: str, text: str) -> None:
        """Push a line to the terminal output service if available."""
        if self._terminal_output is not None:
            try:
                self._terminal_output.push(source, level, text)
            except Exception:
                logger.debug("Failed to push terminal output for %s", source, exc_info=True)

    @property
    def current_timeout(self) -> int:
        """Resolve the current timeout from live config, falling back to initial config, then 15000ms.

        Always clamped to [1000, MAX_TIMEOUT_MS].
        """
        timeout: int | None = None
        if self._config_manager is not None:
            try:
                timeout = self._config_manager.get_configuration().llm.timeout
            except Exception:
                pass
        if timeout is None and self._initial_config is not None:
            timeout = self._initial_config.timeout
        if timeout is None:
            timeout = 15000
        return max(1000, min(timeout, MAX_TIMEOUT_MS))

    @staticmethod
    def create_failure_response(error: str, reasoning: str, elapsed_ms: int = 0) -> LLMResponse:
        """Create a failed LLMResponse with the given error and reasoning."""
        return LLMResponse(
            success=False,
            safety_score=0,
            error=error,
            reasoning=reasoning,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def create_timeout_response(
        provider_name: str, max_retries: int, timeout_ms: int, total_elapsed_ms: int
    ) -> LLMResponse:
        """Create a timeout failure response after all retries are exhausted."""
        msg = f"{provider_name} timed out after {max_retries} attempts ({timeout_ms}ms each)"
        return LLMResponse(
            success=False,
            safety_score=0,
            error=msg,
            reasoning=msg,
            elapsed_ms=total_elapsed_ms,
        )

    @staticmethod
    def create_retries_exhausted_response(provider_name: str) -> LLMResponse:
        """Create an exhausted-retries failure response."""
        return LLMResponse(
            success=False,
            safety_score=0,
            error=f"{provider_name} query failed after all retries",
            reasoning="All retry attempts exhausted",
        )

    @staticmethod
    def preview_prompt(prompt: str, max_length: int = 120) -> str:
        """Truncate a prompt for display in logs."""
        if len(prompt) > max_length:
            return prompt[:max_length] + "..."
        return prompt
