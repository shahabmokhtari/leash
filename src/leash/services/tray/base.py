"""Base protocols for tray and notification services.

Defines the interfaces that platform-specific implementations must satisfy.
"""

from __future__ import annotations

from typing import Protocol

from leash.models.tray_models import NotificationInfo, TrayDecision


class TrayService(Protocol):
    """Manages the system tray icon or equivalent presence indicator."""

    @property
    def is_available(self) -> bool:
        """Whether the tray service is available on this platform."""
        ...

    async def start(self) -> None:
        """Start the tray icon/service."""
        ...

    def update_status(self, status: str) -> None:
        """Update the tray icon tooltip/status text."""
        ...

    def stop(self) -> None:
        """Stop the tray icon/service and clean up."""
        ...


class NotificationService(Protocol):
    """Shows native OS notifications (passive alerts and interactive approve/deny dialogs)."""

    @property
    def supports_interactive(self) -> bool:
        """Whether the platform supports interactive approve/deny dialogs."""
        ...

    async def show_alert(self, info: NotificationInfo) -> None:
        """Show a passive notification (no user action required)."""
        ...

    async def show_interactive(self, info: NotificationInfo, timeout: float) -> TrayDecision | None:
        """Show an interactive dialog with Approve/Deny buttons.

        Returns the user's choice, or None on timeout/error.
        *timeout* is in seconds.
        """
        ...
