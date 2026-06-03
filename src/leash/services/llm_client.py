"""LLM client protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from leash.models.llm_response import LLMResponse

__all__ = ["LLMClient", "LLMResponse"]


@runtime_checkable
class LLMClient(Protocol):
    """Protocol that all LLM client implementations must satisfy."""

    async def query(self, prompt: str) -> LLMResponse:
        """Send a prompt to the LLM and return a structured response."""
        ...
