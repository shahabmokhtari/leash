"""Adaptive threshold models for learning from user overrides."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel


class ThresholdOverride(BaseModel):
    """Records a user override of an automatic decision."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str = ""
    original_decision: str = ""
    user_action: str = ""
    safety_score: int = 0
    threshold: int = 0
    session_id: str = ""


class ToolThresholdStats(BaseModel):
    """Statistics for threshold adjustments per tool."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    tool_name: str = ""
    total_decisions: int = 0
    override_count: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    suggested_threshold: int | None = None
    average_safety_score: float = 0.0
    confidence_level: float = 0.0


class AdaptiveThresholdData(BaseModel):
    """Persistent data for adaptive threshold learning."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    overrides: list[ThresholdOverride] = []
    tool_stats: dict[str, ToolThresholdStats] = {}
    last_calculated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
