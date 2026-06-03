"""Base handler protocol for Leash hook handlers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from leash.models.handler_config import HandlerConfig
from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput


@runtime_checkable
class HookHandler(Protocol):
    """Protocol that all hook handlers must satisfy."""

    async def handle(
        self,
        input: HookInput,
        config: HandlerConfig,
        session_context: str,
    ) -> HookOutput:
        """Process a hook event and return an analysis result."""
        ...
