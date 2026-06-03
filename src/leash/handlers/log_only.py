"""Log-only handler -- logs the hook event and returns no decision."""

from __future__ import annotations

import logging

from leash.models.handler_config import HandlerConfig
from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput

logger = logging.getLogger(__name__)

# Map of config string -> Python logging level
_LOG_LEVEL_MAP: dict[str, int] = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "information": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


class LogOnlyHandler:
    """Logs the hook event without making any approval / deny decision."""

    async def handle(
        self,
        input: HookInput,
        config: HandlerConfig,
        session_context: str,
    ) -> HookOutput:
        level = self._get_log_level(config)

        logger.log(
            level,
            "Hook event %s for tool %s in session %s",
            input.hook_event_name,
            input.tool_name or "N/A",
            input.session_id,
        )

        if input.tool_input:
            logger.log(level, "Tool input: %s", input.tool_input)

        return HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning="Log-only handler - no decision made",
            category="logged",
        )

    @staticmethod
    def _get_log_level(config: HandlerConfig) -> int:
        raw = config.config.get("logLevel")
        if isinstance(raw, str):
            return _LOG_LEVEL_MAP.get(raw.lower(), logging.INFO)
        return logging.INFO
