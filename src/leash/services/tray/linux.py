"""Linux tray and notification services using notify-send and zenity.

Tray icon uses ``yad --notification`` (if available).
Passive alerts use ``notify-send``.
Interactive approve/deny uses ``zenity --question``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from leash.models.tray_models import NotificationInfo, NotificationLevel, TrayDecision

logger = logging.getLogger(__name__)


class LinuxTrayService:
    """Linux tray service using ``yad --notification`` as a background subprocess.

    Falls back to ``is_available = False`` if yad is not installed.
    """

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    async def start(self) -> None:
        if not shutil.which("yad"):
            logger.info("yad not found -- Linux tray icon not available. Notifications will use notify-send/zenity.")
            return

        try:
            self._process = await asyncio.create_subprocess_exec(
                "yad",
                "--notification",
                "--text=Leash",
                "--image=dialog-information",
                stdin=asyncio.subprocess.PIPE,
            )
            self._available = True
            logger.info("Linux tray icon started via yad (PID: %s)", self._process.pid)
        except Exception:
            logger.debug("Failed to start yad notification icon", exc_info=True)

    def update_status(self, status: str) -> None:
        # yad --notification does not easily support tooltip updates
        pass

    def stop(self) -> None:
        """Stop the tray process."""
        if self._process is not None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            self._process = None
            self._available = False


class LinuxNotificationService:
    """Linux notification service using ``notify-send`` for passive alerts
    and ``zenity`` for interactive approve/deny dialogs.
    """

    def __init__(self) -> None:
        self._zenity_available: bool | None = None

    @property
    def supports_interactive(self) -> bool:
        return self._check_zenity_available()

    async def show_alert(self, info: NotificationInfo) -> None:
        try:
            urgency = {
                NotificationLevel.DANGER: "critical",
                NotificationLevel.WARNING: "normal",
            }.get(info.level, "low")

            proc = await asyncio.create_subprocess_exec(
                "notify-send",
                f"--urgency={urgency}",
                "--app-name=Leash",
                info.title,
                info.body,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        except FileNotFoundError:
            logger.debug("notify-send not found")
        except Exception:
            logger.debug("Failed to show Linux notification via notify-send", exc_info=True)

    async def show_interactive(self, info: NotificationInfo, timeout: float) -> TrayDecision | None:
        if not self._check_zenity_available():
            return None

        try:
            parts: list[str] = []
            if info.tool_name:
                parts.append(f"Tool: {info.tool_name}")
            if info.safety_score is not None:
                parts.append(f"Score: {info.safety_score}")
            if info.reasoning:
                reasoning = info.reasoning[:197] + "..." if len(info.reasoning) > 200 else info.reasoning
                parts.append(reasoning)

            text = "\n".join(parts)
            timeout_sec = int(timeout)

            proc = await asyncio.create_subprocess_exec(
                "zenity",
                "--question",
                f"--title={info.title}",
                f"--text={text}",
                "--ok-label=Approve",
                "--cancel-label=Deny",
                f"--timeout={timeout_sec}",
                stdout=asyncio.subprocess.PIPE,
            )

            try:
                await asyncio.wait_for(proc.communicate(), timeout=timeout + 5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return None

            # zenity exit codes: 0 = OK (Approve), 1 = Cancel (Deny), 5 = Timeout
            if proc.returncode == 0:
                return TrayDecision.APPROVE
            if proc.returncode == 1:
                return TrayDecision.DENY
            return None  # timeout or other
        except FileNotFoundError:
            logger.debug("zenity not found")
            return None
        except Exception:
            logger.debug("Failed to show interactive Linux dialog via zenity", exc_info=True)
            return None

    def _check_zenity_available(self) -> bool:
        if self._zenity_available is not None:
            return self._zenity_available
        self._zenity_available = shutil.which("zenity") is not None
        return self._zenity_available
