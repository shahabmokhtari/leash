"""Handler configuration model with regex matching."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)


class HandlerConfig(BaseModel):
    """Configuration for a single hook handler."""

    model_config = {"alias_generator": to_camel, "populate_by_name": True}

    name: str = ""
    enabled: bool = True
    matcher: str | None = None
    mode: str = "log-only"
    prompt_template: str | None = None
    client: str | None = None
    threshold: int = 85
    threshold_strict: int = 95
    threshold_moderate: int = 85
    threshold_permissive: int = 70
    threshold_trust: int = 50
    auto_approve: bool = False
    pre_validation_script: str | None = None
    config: dict[str, Any] = {}

    # Cached compiled regex (not serialized)
    _compiled_regex: re.Pattern[str] | None = None
    _last_matcher: str | None = None

    def get_threshold_for_profile(self, profile: str | None) -> int:
        """Get threshold for the given profile name."""
        match (profile or "").lower():
            case "strict":
                return self.threshold_strict
            case "moderate":
                return self.threshold_moderate
            case "permissive":
                return self.threshold_permissive
            case "trust":
                return self.threshold_trust
            case "lockdown":
                return 101  # Nothing passes
            case _:
                return self.threshold

    def matches(self, tool_name: str) -> bool:
        """Check if this handler's matcher matches the given tool name."""
        if not self.matcher or self.matcher == "*":
            return True

        # Rebuild regex if matcher changed
        if self._compiled_regex is None or self._last_matcher != self.matcher:
            try:
                self._compiled_regex = re.compile(self.matcher, re.IGNORECASE)
                self._last_matcher = self.matcher
            except re.error as e:
                logger.error("Invalid regex pattern '%s': %s", self.matcher, e)
                self._compiled_regex = None
                self._last_matcher = self.matcher

        if self._compiled_regex is not None:
            return bool(self._compiled_regex.match(tool_name))

        # Fall back to case-insensitive literal match
        return self.matcher.lower() == tool_name.lower()
