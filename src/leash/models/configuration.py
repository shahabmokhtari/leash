"""Configuration models for Leash."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic.alias_generators import to_camel

from leash.models.handler_config import HandlerConfig


class GenericRestConfig(BaseModel):
    """Configuration for a generic REST LLM provider."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    url: str = ""
    headers: dict[str, str] = {}
    body_template: str = ""
    response_path: str = ""


class LlmConfig(BaseModel):
    """LLM provider configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    provider: str = "claude-stream"
    model: str = "opus"
    timeout: int = 30000
    command: str | None = None
    effort_level: str | None = None
    provider_models: dict[str, str] = {}
    max_queries_per_session: int = 100
    max_concurrent_sessions: int = 20
    prompt_prefixes: list[str] = []
    prompt_suffixes: list[str] = []
    system_prompt: str | None = (
        "You are a security analyzer that evaluates the safety of operations. "
        "Always respond ONLY with valid JSON containing safetyScore (0-100), "
        "reasoning (string), and category (safe|cautious|risky|dangerous). "
        "Never include any text outside the JSON object."
    )
    session_idle_timeout_minutes: int = 5
    api_key: str | None = None
    api_base_url: str | None = None
    generic_rest: GenericRestConfig | None = None


class ServerConfig(BaseModel):
    """Web server configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    port: int = 5050
    host: str = "localhost"


class HookEventConfig(BaseModel):
    """Configuration for handlers under a specific hook event type."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    enabled: bool = True
    handlers: list[HandlerConfig] = []


class SessionConfig(BaseModel):
    """Session storage configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    max_history_per_session: int = 50
    storage_dir: str = "~/.leash/sessions"


class SecurityConfig(BaseModel):
    """Security configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    api_key: str | None = None
    rate_limit_per_minute: int = 600


class TriggerRule(BaseModel):
    """A webhook trigger rule."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    name: str = ""
    event: str = "*"
    url: str = ""
    method: str = "POST"


class TriggerConfig(BaseModel):
    """Webhook trigger configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    enabled: bool = False
    rules: list[TriggerRule] = []


class ProfileConfig(BaseModel):
    """Profile configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    active_profile: str = "moderate"
    custom_profiles: dict[str, Any] = {}


class TrayConfig(BaseModel):
    """System tray and notification configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    enabled: bool = True
    show_in_observe: bool = True
    show_in_approve_only: bool = True
    interactive_timeout_seconds: int = 10
    sound: bool = False
    use_large_popup: bool = True


class CopilotConfig(BaseModel):
    """Copilot integration configuration."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    enabled: bool = True
    hook_handlers: dict[str, HookEventConfig] = {}


class Configuration(BaseModel):
    """Root configuration for Leash."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    llm: LlmConfig = LlmConfig()
    server: ServerConfig = ServerConfig()
    hook_handlers: dict[str, HookEventConfig] = {}
    session: SessionConfig = SessionConfig()
    security: SecurityConfig = SecurityConfig()
    profiles: ProfileConfig = ProfileConfig()
    enforcement_enabled: bool = False
    enforcement_mode: str | None = None
    analyze_in_observe_mode: bool = True
    copilot: CopilotConfig = CopilotConfig()
    triggers: TriggerConfig = TriggerConfig()
    tray: TrayConfig = TrayConfig()
    resolve_symlinks: bool = False
    resolve_hook_symlinks: bool = True
    hooks_user_uninstalled: bool = False
    copilot_hooks_user_uninstalled: bool = False
