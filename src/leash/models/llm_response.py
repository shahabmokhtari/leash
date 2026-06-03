"""LLM response model."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic.alias_generators import to_camel


class LLMResponse(BaseModel):
    """Response from an LLM client query."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    safety_score: int = 0
    reasoning: str = ""
    category: str = "unknown"
    success: bool = False
    error: str | None = None
    elapsed_ms: int = 0
