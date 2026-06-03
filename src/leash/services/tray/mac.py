"""macOS tray and notification services using osascript (AppleScript).

The tray icon itself is a no-op (a true menubar icon requires a Cocoa application
bundle which is not feasible from a Python process).  Notifications work via
``osascript`` subprocess calls.
"""

from __future__ import annotations

import asyncio
import logging

from leash.models.tray_models import NotificationInfo, TrayDecision

logger = logging.getLogger(__name__)


class MacTrayService:
    """macOS tray service stub.

    A true menubar icon requires a Cocoa bundle, so this reports unavailable.
    Notifications still work via ``MacNotificationService``.
    """

    @property
    def is_available(self) -> bool:
        return False

    async def start(self) -> None:
        logger.info(
            "macOS menubar icon not available (requires Cocoa bundle). "
            "Notifications will still work via osascript."
        )

    def update_status(self, status: str) -> None:
        pass

    def stop(self) -> None:
        pass


class MacNotificationService:
    """macOS notification service using osascript (AppleScript).

    Passive: ``display notification``.
    Interactive: ``display dialog`` with Approve/Deny buttons and a ``giving up after`` timeout.
    """

    @property
    def supports_interactive(self) -> bool:
        return True

    async def show_alert(self, info: NotificationInfo) -> None:
        try:
            body = _escape_applescript(info.body)
            title = _escape_applescript(info.title)
            script = f'display notification "{body}" with title "{title}"'
            await _run_osascript(script, timeout_ms=5000)
        except Exception:
            logger.debug("Failed to show macOS notification", exc_info=True)

    async def show_interactive(self, info: NotificationInfo, timeout: float) -> TrayDecision | None:
        try:
            parts: list[str] = []
            if info.tool_name:
                parts.append(f"Tool: {info.tool_name}")
            if info.safety_score is not None:
                parts.append(f"Score: {info.safety_score}")
            if info.reasoning:
                reasoning = info.reasoning[:197] + "..." if len(info.reasoning) > 200 else info.reasoning
                parts.append(f"\n{reasoning}")

            body = _escape_applescript("   ".join(parts))
            title = _escape_applescript(info.title)
            giving_up = int(timeout)

            script = (
                f'display dialog "{body}" with title "{title}" '
                f'buttons {{"Deny","Approve"}} default button "Approve" '
                f"giving up after {giving_up}"
            )

            result = await _run_osascript(script, timeout_ms=int(timeout * 1000) + 5000)
            if result is None:
                return None

            lower = result.lower()
            if "button returned:approve" in lower:
                return TrayDecision.APPROVE
            if "button returned:deny" in lower:
                return TrayDecision.DENY
            if "gave up:true" in lower:
                return None

            return None
        except Exception:
            logger.debug("Failed to show interactive macOS dialog", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_osascript(script: str, timeout_ms: int) -> str | None:
    """Run an osascript command and return stdout, or None on failure/timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_ms / 1000.0)
            return stdout.decode("utf-8", errors="replace") if stdout else None
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
    except FileNotFoundError:
        return None


def _escape_applescript(s: str) -> str:
    """Escape a string for safe embedding in an AppleScript literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
