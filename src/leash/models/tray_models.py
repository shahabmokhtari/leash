"""Tray and notification models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel
from pydantic.alias_generators import to_camel


class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"


class TrayDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    IGNORE = "ignore"


class NotificationInfo(BaseModel):
    """Information for a tray notification."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    title: str
    body: str
    tool_name: str | None = None
    safety_score: int | None = None
    threshold: int | None = None
    reasoning: str | None = None
    suggested_action: str | None = None
    category: str | None = None
    decision_id: str | None = None
    provider: str | None = None
    cwd: str | None = None
    tool_input_summary: str | None = None
    command_preview: str | None = None
    timeout_seconds: int | None = None
    sound: bool = False
    level: NotificationLevel = NotificationLevel.INFO


class PendingDecisionInfo(BaseModel):
    """Serializable info about a pending tray decision (for API responses)."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    id: str
    info: NotificationInfo
    created_at: str
