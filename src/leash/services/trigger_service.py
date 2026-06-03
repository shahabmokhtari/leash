"""Fire-and-forget webhook triggers on matching hook events."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx

from leash.models.session_data import SessionEvent

if TYPE_CHECKING:
    from leash.config import ConfigurationManager
    from leash.models.configuration import TriggerRule

logger = logging.getLogger(__name__)


class TriggerService:
    """Fires HTTP webhook triggers on matching hook events.

    Triggers run fire-and-forget so they never block the hook response.
    """

    def __init__(self, config_manager: ConfigurationManager) -> None:
        self._config_manager = config_manager

    def fire(self, decision: str, category: str | None, event: SessionEvent) -> None:
        """Match event against configured trigger rules and fire webhooks.

        This method returns immediately; HTTP calls happen in background tasks.
        """
        try:
            config = self._config_manager.get_configuration()
            triggers = config.triggers

            if not triggers.enabled or not triggers.rules:
                return

            matching_rules = [r for r in triggers.rules if self._rule_matches(r, decision, category)]

            if not matching_rules:
                return

            payload = {
                "event": event.type,
                "toolName": event.tool_name,
                "decision": decision,
                "safetyScore": event.safety_score,
                "category": event.category,
                "reasoning": event.reasoning,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "sessionId": None,
            }

            payload_json = json.dumps(payload)

            for rule in matching_rules:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._send_webhook(rule, payload_json))
                except RuntimeError:
                    # No running event loop; log and skip
                    logger.warning("No running event loop for trigger '%s'", rule.name)

        except Exception as e:
            logger.warning("Failed to evaluate trigger rules: %s", e)

    @staticmethod
    def _rule_matches(rule: TriggerRule, decision: str, category: str | None) -> bool:
        """Check if a trigger rule matches the given decision/category."""
        if rule.event == "*":
            return True

        if rule.event.lower() == decision.lower():
            return True

        # "dangerous" matches when category is "dangerous" regardless of decision
        if rule.event.lower() == "dangerous" and category and category.lower() == "dangerous":
            return True

        return False

    @staticmethod
    async def _send_webhook(rule: TriggerRule, payload_json: str) -> None:
        """Send a webhook request with a 5-second timeout."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                method = rule.method.upper()
                if method == "GET":
                    response = await client.get(
                        rule.url,
                        headers={"Content-Type": "application/json"},
                    )
                else:
                    response = await client.post(
                        rule.url,
                        content=payload_json,
                        headers={"Content-Type": "application/json"},
                    )

                logger.debug(
                    "Trigger '%s' fired to %s: %s",
                    rule.name, rule.url, response.status_code,
                )
        except Exception as e:
            logger.warning("Trigger '%s' failed to fire to %s: %s", rule.name, rule.url, e)
