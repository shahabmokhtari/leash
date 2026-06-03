"""Hook output model - represents analysis results returned to Claude Code."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic.alias_generators import to_camel


class HookOutput(BaseModel):
    """Analysis result returned from hook processing."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    auto_approve: bool = False
    safety_score: int = 0
    reasoning: str = ""
    category: str = "unknown"
    threshold: int = 0
    system_message: str | None = None
    additional_context: str | None = None
    interrupt: bool = False
    elapsed_ms: int = 0
    tray_decision: str | None = None
