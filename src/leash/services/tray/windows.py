"""Windows tray and notification services.

Uses pystray for the system tray icon and windows-toasts for rich toast
notifications with interactive approve/deny buttons.

Both libraries are optional — the module imports cleanly without them and
falls back gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from typing import Any

from leash.models.tray_models import NotificationInfo, NotificationLevel, TrayDecision

logger = logging.getLogger(__name__)

# --- Optional dependency: pystray + Pillow (tray icon) ---
try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

# --- Optional dependency: windows-toasts (rich toast notifications) ---
try:
    from windows_toasts import (
        InteractableWindowsToaster,
        Toast,
        ToastActivatedEventArgs,
        ToastAudio,
        ToastButton,
        ToastDuration,
    )

    HAS_TOASTS = True
except ImportError:
    HAS_TOASTS = False


def _create_default_icon() -> Any:
    """Create a small blue circle icon with a white checkmark."""
    if not HAS_PYSTRAY:
        return None
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, 14, 14], fill=(59, 130, 246, 255))
    draw.line([(4, 8), (7, 11), (12, 5)], fill="white", width=2)
    return img


class WindowsTrayService:
    """Windows system tray icon using pystray on a background thread."""

    def __init__(self, dashboard_url: str = "http://localhost:5050") -> None:
        self._dashboard_url = dashboard_url
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._disposed = False

    @property
    def is_available(self) -> bool:
        return HAS_PYSTRAY and self._started and not self._disposed and self._icon is not None

    async def start(self) -> None:
        if not HAS_PYSTRAY or self._started or self._disposed:
            return
        if sys.platform != "win32":
            return

        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def _run_tray() -> None:
            try:
                image = _create_default_icon()
                menu = pystray.Menu(
                    pystray.MenuItem("Open Dashboard", self._open_dashboard),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Exit", self._exit_tray),
                )
                self._icon = pystray.Icon("leash", image, "Leash", menu)
                self._started = True
                loop.call_soon_threadsafe(ready.set)
                self._icon.run()
            except Exception:
                logger.debug("Failed to start Windows tray icon", exc_info=True)
                loop.call_soon_threadsafe(ready.set)

        self._thread = threading.Thread(target=_run_tray, daemon=True, name="TrayIconThread")
        self._thread.start()
        await ready.wait()

    def update_status(self, status: str) -> None:
        if not self.is_available:
            return
        try:
            text = f"Leash - {status}"
            self._icon.title = text[:63] if len(text) > 63 else text
        except Exception:
            pass

    def _open_dashboard(self) -> None:
        import webbrowser

        try:
            webbrowser.open(self._dashboard_url)
        except Exception:
            pass

    def _exit_tray(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def stop(self) -> None:
        """Stop the tray icon and clean up."""
        if self._disposed:
            return
        self._disposed = True
        self._started = False
        self._exit_tray()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None


def _build_toast_body(info: NotificationInfo) -> str:
    """Build a detailed multi-line body for the toast notification."""
    lines: list[str] = []

    # Score + suggested action (most important, first line)
    if info.safety_score is not None:
        threshold_str = f"/{info.threshold}" if info.threshold is not None else ""
        action_labels = {"approve": "APPROVE", "deny": "DENY", "review": "NEEDS REVIEW"}
        action = action_labels.get(info.suggested_action or "", "")
        score_line = f"Score: {info.safety_score}{threshold_str}"
        if action:
            score_line += f"  |  {action}"
        lines.append(score_line)

    # Tool + request summary
    if info.tool_name:
        tool_line = f"Tool: {info.tool_name}"
        if info.tool_input_summary:
            tool_line += f" - {info.tool_input_summary[:120]}"
        lines.append(tool_line)
    elif info.tool_input_summary:
        lines.append(f"Request: {info.tool_input_summary[:150]}")

    # Folder/repo
    if info.cwd:
        folder = os.path.basename(info.cwd.rstrip("\\/")) or info.cwd
        lines.append(f"Folder: {folder}")

    # LLM reasoning
    if info.reasoning:
        lines.append(f"Reason: {info.reasoning[:250]}")

    # Timeout
    if info.timeout_seconds:
        lines.append(f"Expires in {info.timeout_seconds}s")

    return "\n".join(lines) if lines else info.body


class WindowsNotificationService:
    """Windows notification service using windows-toasts for rich notifications.

    Supports:
    - Passive alerts (informational, no buttons) via native toasts
    - Interactive decisions via custom tkinter popup with colored buttons
    """

    def __init__(self, tray_service: WindowsTrayService, use_large_popup: bool = True) -> None:
        self._tray = tray_service
        self._toaster: Any | None = None
        self._popup_thread: Any | None = None
        self._use_large_popup = use_large_popup
        if HAS_TOASTS:
            try:
                self._toaster = InteractableWindowsToaster("Leash")
            except Exception:
                logger.debug("Failed to create Windows toaster", exc_info=True)
        # Start the custom popup thread for interactive decisions
        if use_large_popup:
            try:
                from leash.services.tray.windows_popup import PopupThread

                self._popup_thread = PopupThread()
                self._popup_thread.start()
            except Exception:
                logger.debug("Failed to start popup thread", exc_info=True)

    @property
    def supports_interactive(self) -> bool:
        return self._popup_thread is not None or self._toaster is not None

    def _make_audio(self, info: NotificationInfo) -> Any:
        """Create ToastAudio based on notification sound setting."""
        if not HAS_TOASTS:
            return None
        return ToastAudio(silent=not info.sound)

    async def show_alert(self, info: NotificationInfo) -> None:
        """Show a passive toast notification (no buttons)."""
        if self._toaster is not None:
            try:
                body = _build_toast_body(info)
                toast = Toast(
                    [info.title[:200], body[:500]],
                    audio=self._make_audio(info),
                    duration=ToastDuration.Short,
                )
                self._toaster.show_toast(toast)
                return
            except Exception:
                logger.debug("Toast alert failed, trying pystray fallback", exc_info=True)

        # Fallback to pystray balloon
        if self._tray.is_available and self._tray._icon is not None:
            try:
                self._tray._icon.notify(
                    title=info.title[:63],
                    message=(info.reasoning or info.body)[:255],
                )
            except Exception:
                logger.debug("Failed to show Windows notification", exc_info=True)

    async def show_interactive(self, info: NotificationInfo, timeout: float) -> TrayDecision | None:
        """Show a custom popup with colored Approve/Deny/Ignore buttons.

        Uses a tkinter popup on a dedicated thread for full control over layout
        and button colours.  Falls back to native toast if the popup thread is
        unavailable.
        """
        # Prefer the custom popup (colourful, larger, richer)
        if self._popup_thread is not None:
            try:
                return await self._popup_thread.request_decision(info, timeout)
            except Exception:
                logger.debug("Custom popup failed, trying toast fallback", exc_info=True)

        # Fallback: native toast (no custom colours, but functional)
        if self._toaster is None:
            return None

        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[TrayDecision | None] = loop.create_future()

        def _on_activated(event_args: ToastActivatedEventArgs) -> None:
            try:
                args = event_args.arguments
                if "action=approve" in args:
                    loop.call_soon_threadsafe(_set_result, TrayDecision.APPROVE)
                elif "action=deny" in args:
                    loop.call_soon_threadsafe(_set_result, TrayDecision.DENY)
                elif "action=ignore" in args:
                    loop.call_soon_threadsafe(_set_result, TrayDecision.IGNORE)
                # Body click (no action= arg): do nothing, keep waiting
            except Exception:
                pass

        def _on_dismissed(_: Any) -> None:
            # User swiped away or toast expired — treat as timeout, not a decision
            if not result_future.done():
                loop.call_soon_threadsafe(_set_result, None)

        def _on_failed(_: Any) -> None:
            if not result_future.done():
                loop.call_soon_threadsafe(_set_result, None)

        def _set_result(value: TrayDecision | None) -> None:
            if not result_future.done():
                result_future.set_result(value)

        try:
            body = _build_toast_body(info)
            toast = Toast(
                [info.title[:200], body[:500]],
                audio=self._make_audio(info),
                duration=ToastDuration.Long,
            )
            toast.AddAction(ToastButton("Approve", "action=approve"))
            toast.AddAction(ToastButton("Deny", "action=deny"))
            toast.AddAction(ToastButton("Ignore", "action=ignore"))
            toast.on_activated = _on_activated
            toast.on_dismissed = _on_dismissed
            toast.on_failed = _on_failed

            self._toaster.show_toast(toast)

            return await asyncio.wait_for(result_future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except Exception:
            logger.debug("Failed to show interactive Windows toast", exc_info=True)
            return None

    def stop(self) -> None:
        """Shut down the popup thread."""
        if self._popup_thread is not None:
            try:
                self._popup_thread.stop()
            except Exception:
                pass
