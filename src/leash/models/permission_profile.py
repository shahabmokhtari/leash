"""Permission profile models."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic.alias_generators import to_camel


class PermissionProfile(BaseModel):
    """A permission profile with threshold settings."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    name: str = ""
    description: str = ""
    default_threshold: int = 85
    auto_approve_enabled: bool = True
    threshold_overrides: dict[str, int] = {}


BUILTIN_PROFILES: dict[str, PermissionProfile] = {
    "strict": PermissionProfile(
        name="Strict",
        description="High security - only the safest operations are auto-approved",
        default_threshold=95,
        auto_approve_enabled=True,
        threshold_overrides={"Bash": 98, "Write": 96, "Edit": 95, "Read": 90},
    ),
    "moderate": PermissionProfile(
        name="Moderate",
        description="Balanced security - reasonable operations are auto-approved",
        default_threshold=85,
        auto_approve_enabled=True,
        threshold_overrides={"Bash": 90, "Write": 88, "Edit": 85, "Read": 75},
    ),
    "permissive": PermissionProfile(
        name="Permissive",
        description="Low friction - most operations are auto-approved",
        default_threshold=70,
        auto_approve_enabled=True,
        threshold_overrides={"Bash": 80, "Write": 75, "Edit": 70, "Read": 60},
    ),
    "trust": PermissionProfile(
        name="Trust",
        description="Minimal friction - only blocks clearly dangerous operations (score <= 50)",
        default_threshold=50,
        auto_approve_enabled=True,
        threshold_overrides={"Bash": 55, "Write": 50, "Edit": 50, "Read": 30},
    ),
    "lockdown": PermissionProfile(
        name="Lockdown",
        description="Maximum security - nothing is auto-approved",
        default_threshold=100,
        auto_approve_enabled=False,
        threshold_overrides={},
    ),
}
