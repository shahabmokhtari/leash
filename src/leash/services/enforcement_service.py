"""Enforcement mode management with 3-state cycling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leash.config import ConfigurationManager

logger = logging.getLogger(__name__)

VALID_MODES = {"observe", "approve-only", "enforce"}


class EnforcementService:
    """Manages the 3-state enforcement mode: observe, approve-only, enforce."""

    def __init__(self, config_manager: ConfigurationManager) -> None:
        self._config_manager = config_manager
        self._mode = "observe"

        # Resolve initial mode from config
        config = config_manager.get_configuration()
        if config.enforcement_mode and config.enforcement_mode in VALID_MODES:
            self._mode = config.enforcement_mode
        else:
            self._mode = "enforce" if config.enforcement_enabled else "observe"

    @property
    def mode(self) -> str:
        """Current enforcement mode: 'observe', 'approve-only', or 'enforce'."""
        return self._mode

    @property
    def is_enforced(self) -> bool:
        """Backward-compatible property. True when mode is 'enforce'."""
        return self._mode == "enforce"

    async def set_mode(self, mode: str) -> None:
        """Set the enforcement mode, validating and persisting to config."""
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid enforcement mode: {mode}. Valid: {', '.join(sorted(VALID_MODES))}"
            )

        self._mode = mode
        config = self._config_manager.get_configuration()
        config.enforcement_mode = mode
        config.enforcement_enabled = mode == "enforce"  # keep bool in sync
        await self._config_manager.update(config)
        logger.info("Enforcement mode changed to %s", mode)

    async def set_enforced(self, enforced: bool) -> None:
        """Backward-compatible setter."""
        await self.set_mode("enforce" if enforced else "observe")

    async def cycle_mode(self) -> None:
        """Cycle through modes: observe -> approve-only -> enforce -> observe."""
        next_mode = {
            "observe": "approve-only",
            "approve-only": "enforce",
            "enforce": "observe",
        }.get(self._mode, "observe")
        await self.set_mode(next_mode)

    async def toggle(self) -> None:
        """Backward-compatible toggle (now cycles 3 modes)."""
        await self.cycle_mode()
