"""No-op implementations of TrayService and NotificationService.

Used when the tray feature is disabled or the current platform has no
native tray / notification support.
"""

from __future__ import annotations

from leash.models.tray_models import NotificationInfo, TrayDecision


class NullTrayService:
    """No-op tray service -- always reports unavailable."""

    @property
    def is_available(self) -> bool:
        return False

    async def start(self) -> None:
        pass

    def update_status(self, status: str) -> None:
        pass

    def stop(self) -> None:
        pass


class NullNotificationService:
    """No-op notification service.

    ``show_interactive`` returns ``None`` (timeout behaviour -- the AI assistant
    falls through to asking the user normally).
    """

    @property
    def supports_interactive(self) -> bool:
        return False

    async def show_alert(self, info: NotificationInfo) -> None:
        pass

    async def show_interactive(self, info: NotificationInfo, timeout: float) -> TrayDecision | None:
        return None
