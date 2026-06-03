"""Session data models for tracking Claude Code sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel


class SessionEvent(BaseModel):
    """A single event within a session."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    decision: str | None = None
    safety_score: int | None = None
    reasoning: str | None = None
    category: str | None = None
    content: str | None = None
    handler_name: str | None = None
    prompt_template: str | None = None
    threshold: int | None = None
    provider: str | None = None
    llm_provider: str | None = None
    elapsed_ms: int | None = None
    response_json: str | None = None


class SessionData(BaseModel):
    """Tracks a Claude Code session and its events."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    session_id: str
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    working_directory: str | None = None
    conversation_history: list[SessionEvent] = Field(default_factory=list)
