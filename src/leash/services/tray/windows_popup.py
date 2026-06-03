"""Custom tkinter popup window for interactive Leash decisions.

Provides a rich, colorful decision dialog with Approve/Deny/Ignore buttons
that runs on a dedicated GUI thread.  Replaces the native Windows toast for
interactive notifications while keeping toasts for passive alerts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leash.models.tray_models import NotificationInfo, TrayDecision

logger = logging.getLogger(__name__)

# ── Colour palette ──────────────────────────────────────────────────────────

_BG = "#1e1e2e"  # dark background
_FG = "#cdd6f4"  # light foreground
_HEADER_BG = "#313244"
_SCORE_GREEN = "#22c55e"
_SCORE_YELLOW = "#f59e0b"
_SCORE_RED = "#ef4444"
_BTN_APPROVE_BG = "#22c55e"
_BTN_APPROVE_FG = "#ffffff"
_BTN_DENY_BG = "#ef4444"
_BTN_DENY_FG = "#ffffff"
_BTN_IGNORE_BG = "#f59e0b"
_BTN_IGNORE_FG = "#1e1e2e"
_LABEL_FG = "#a6adc8"  # muted label colour
_VALUE_FG = "#cdd6f4"
_BADGE_BG = "#45475a"
_COUNTDOWN_BG = "#45475a"
_COUNTDOWN_FG = "#89b4fa"

# Category badge colours
_CATEGORY_COLORS: dict[str, str] = {
    "filesystem": "#89b4fa",
    "network": "#cba6f7",
    "process": "#f38ba8",
    "code_execution": "#fab387",
    "configuration": "#a6e3a1",
    "ai_tool": "#94e2d5",
}


@dataclass
class _PopupRequest:
    """Request queued to the popup thread."""

    info: Any  # NotificationInfo
    timeout: float
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[Any]


class DecisionPopup:
    """A single decision popup window built with tkinter."""

    def __init__(self, root: tk.Tk, request: _PopupRequest) -> None:
        self._root = root
        self._req = request
        self._remaining = int(request.timeout)
        self._resolved = False
        self._timer_id: str | None = None

        self._build_window()
        self._start_countdown()

    def _build_window(self) -> None:
        info = self._req.info
        root = self._root

        root.title("Leash Decision")
        root.configure(bg=_BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.overrideredirect(False)

        # Window size and position (lower-right corner with padding)
        w, h = 620, 460
        sx = root.winfo_screenwidth()
        sy = root.winfo_screenheight()
        pad = 16  # padding from screen edges
        x = sx - w - pad
        y = sy - h - pad - 48  # 48px extra for taskbar
        root.geometry(f"{w}x{h}+{x}+{y}")

        # Prevent closing via X button (force button click or timeout)
        root.protocol("WM_DELETE_WINDOW", self._on_ignore)

        # ── Header ──────────────────────────────────────────────────────
        header = tk.Frame(root, bg=_HEADER_BG, padx=16, pady=10)
        header.pack(fill="x")

        title_text = f"LEASH: {info.tool_name or 'unknown'} needs review"
        tk.Label(
            header,
            text=title_text,
            bg=_HEADER_BG,
            fg=_FG,
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(side="left")

        if info.category:
            badge_bg = _CATEGORY_COLORS.get(info.category, _BADGE_BG)
            tk.Label(
                header,
                text=f"  {info.category}  ",
                bg=badge_bg,
                fg="#1e1e2e",
                font=("Segoe UI", 9, "bold"),
                padx=6,
                pady=2,
            ).pack(side="right")

        # ── Main content frame ──────────────────────────────────────────
        content = tk.Frame(root, bg=_BG, padx=20, pady=10)
        content.pack(fill="both", expand=True)

        # ── Score gauge ─────────────────────────────────────────────────
        score = info.safety_score
        threshold = info.threshold
        if score is not None:
            score_frame = tk.Frame(content, bg=_BG)
            score_frame.pack(fill="x", pady=(0, 8))

            if threshold is not None:
                score_color = _SCORE_GREEN if score >= threshold else (_SCORE_RED if score <= 0 else _SCORE_YELLOW)
            else:
                score_color = _SCORE_YELLOW

            score_text = str(score)
            if threshold is not None:
                score_text += f" / {threshold}"

            tk.Label(
                score_frame,
                text=score_text,
                bg=_BG,
                fg=score_color,
                font=("Segoe UI", 22, "bold"),
                anchor="w",
            ).pack(side="left")

            tk.Label(
                score_frame,
                text="  score / threshold",
                bg=_BG,
                fg=_LABEL_FG,
                font=("Segoe UI", 10),
                anchor="w",
            ).pack(side="left", pady=(6, 0))

            # Progress bar
            bar_frame = tk.Frame(content, bg=_COUNTDOWN_BG, height=8)
            bar_frame.pack(fill="x", pady=(0, 10))
            bar_frame.pack_propagate(False)
            if threshold and threshold > 0:
                fill_pct = max(0, min(1, score / threshold))
            else:
                fill_pct = 0.5
            fill = tk.Frame(bar_frame, bg=score_color, width=int(580 * fill_pct), height=8)
            fill.pack(side="left", fill="y")

        # ── Detail rows ─────────────────────────────────────────────────
        details_frame = tk.Frame(content, bg=_BG)
        details_frame.pack(fill="x", pady=(0, 4))

        def _add_row(label: str, value: str, mono: bool = False) -> None:
            row = tk.Frame(details_frame, bg=_BG)
            row.pack(fill="x", pady=2)
            tk.Label(
                row,
                text=label,
                bg=_BG,
                fg=_LABEL_FG,
                font=("Segoe UI", 10),
                width=10,
                anchor="w",
            ).pack(side="left")
            font = ("Consolas", 10) if mono else ("Segoe UI", 10)
            tk.Label(
                row,
                text=value,
                bg=_BG,
                fg=_VALUE_FG,
                font=font,
                anchor="w",
                wraplength=460,
                justify="left",
            ).pack(side="left", fill="x", expand=True)

        if info.tool_name:
            _add_row("Tool", info.tool_name)

        cmd = info.tool_input_summary or info.command_preview
        if cmd:
            _add_row("Command", cmd[:200], mono=True)

        if info.cwd:
            folder = os.path.basename(info.cwd.rstrip("\\/")) or info.cwd
            _add_row("Folder", folder)

        if info.provider:
            _add_row("Provider", info.provider)

        # ── Reasoning (scrollable) ──────────────────────────────────────
        if info.reasoning:
            tk.Label(
                content,
                text="Reasoning",
                bg=_BG,
                fg=_LABEL_FG,
                font=("Segoe UI", 10),
                anchor="w",
            ).pack(fill="x", pady=(6, 2))

            reason_frame = tk.Frame(content, bg="#313244", padx=8, pady=6)
            reason_frame.pack(fill="x")

            reason_text = tk.Text(
                reason_frame,
                bg="#313244",
                fg=_VALUE_FG,
                font=("Segoe UI", 9),
                wrap="word",
                height=3,
                bd=0,
                highlightthickness=0,
                insertbackground=_FG,
            )
            reason_text.insert("1.0", info.reasoning[:500])
            reason_text.configure(state="disabled")
            reason_text.pack(fill="x")

        # ── Countdown timer ─────────────────────────────────────────────
        timer_frame = tk.Frame(content, bg=_BG, pady=8)
        timer_frame.pack(fill="x")

        self._timer_bar_bg = tk.Frame(timer_frame, bg=_COUNTDOWN_BG, height=6)
        self._timer_bar_bg.pack(fill="x")
        self._timer_bar_bg.pack_propagate(False)

        self._timer_bar = tk.Frame(self._timer_bar_bg, bg=_COUNTDOWN_FG, height=6)
        self._timer_bar.pack(side="left", fill="y")
        self._update_timer_bar()

        self._timer_label = tk.Label(
            timer_frame,
            text=f"Expires in {self._remaining}s",
            bg=_BG,
            fg=_LABEL_FG,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self._timer_label.pack(fill="x", pady=(4, 0))

        # ── Buttons ─────────────────────────────────────────────────────
        btn_frame = tk.Frame(content, bg=_BG, pady=6)
        btn_frame.pack(fill="x")

        btn_font = ("Segoe UI", 12, "bold")
        btn_pad = {"padx": 20, "pady": 8}

        approve_btn = tk.Button(
            btn_frame,
            text="APPROVE",
            bg=_BTN_APPROVE_BG,
            fg=_BTN_APPROVE_FG,
            activebackground="#16a34a",
            activeforeground=_BTN_APPROVE_FG,
            font=btn_font,
            bd=0,
            cursor="hand2",
            command=self._on_approve,
            **btn_pad,
        )
        approve_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        deny_btn = tk.Button(
            btn_frame,
            text="DENY",
            bg=_BTN_DENY_BG,
            fg=_BTN_DENY_FG,
            activebackground="#dc2626",
            activeforeground=_BTN_DENY_FG,
            font=btn_font,
            bd=0,
            cursor="hand2",
            command=self._on_deny,
            **btn_pad,
        )
        deny_btn.pack(side="left", expand=True, fill="x", padx=6)

        ignore_btn = tk.Button(
            btn_frame,
            text="IGNORE",
            bg=_BTN_IGNORE_BG,
            fg=_BTN_IGNORE_FG,
            activebackground="#d97706",
            activeforeground=_BTN_IGNORE_FG,
            font=btn_font,
            bd=0,
            cursor="hand2",
            command=self._on_ignore,
            **btn_pad,
        )
        ignore_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

    # ── Timer ───────────────────────────────────────────────────────────

    def _update_timer_bar(self) -> None:
        timeout = int(self._req.timeout)
        if timeout > 0:
            pct = max(0, self._remaining / timeout)
        else:
            pct = 0
        bar_width = int(580 * pct)
        self._timer_bar.configure(width=max(0, bar_width))

    def _start_countdown(self) -> None:
        self._tick()

    def _tick(self) -> None:
        if self._resolved:
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._resolve(None)
            return
        self._timer_label.configure(text=f"Expires in {self._remaining}s")
        self._update_timer_bar()
        self._timer_id = self._root.after(1000, self._tick)

    # ── Resolve helpers ─────────────────────────────────────────────────

    def _resolve(self, decision: TrayDecision | None) -> None:
        if self._resolved:
            return
        self._resolved = True

        if self._timer_id is not None:
            try:
                self._root.after_cancel(self._timer_id)
            except Exception:
                pass

        req = self._req
        req.loop.call_soon_threadsafe(_safe_set_result, req.future, decision)

        try:
            self._root.destroy()
        except Exception:
            pass

    def _on_approve(self) -> None:
        from leash.models.tray_models import TrayDecision

        self._resolve(TrayDecision.APPROVE)

    def _on_deny(self) -> None:
        from leash.models.tray_models import TrayDecision

        self._resolve(TrayDecision.DENY)

    def _on_ignore(self) -> None:
        from leash.models.tray_models import TrayDecision

        self._resolve(TrayDecision.IGNORE)


def _safe_set_result(future: asyncio.Future[Any], value: Any) -> None:
    if not future.done():
        future.set_result(value)


class PopupThread:
    """Manages a dedicated thread that shows tkinter decision popups.

    Thread-safe: callers post requests via :meth:`request_decision` from any
    thread (including the asyncio event loop thread).  Results are delivered
    back to the asyncio loop via ``loop.call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[_PopupRequest | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="PopupThread")
        self._thread.start()

    def _run(self) -> None:
        """Thread entry: wait for popup requests, show each one sequentially."""
        while True:
            req = self._queue.get()
            if req is None:
                break  # shutdown sentinel
            try:
                root = tk.Tk()
                DecisionPopup(root, req)
                root.mainloop()
            except Exception:
                logger.debug("Popup thread error", exc_info=True)
                req.loop.call_soon_threadsafe(_safe_set_result, req.future, None)

    async def request_decision(self, info: Any, timeout: float) -> Any:
        """Post a popup request and await the result.

        Returns a ``TrayDecision`` or ``None`` on timeout.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._queue.put(_PopupRequest(info=info, timeout=timeout, loop=loop, future=future))
        try:
            return await asyncio.wait_for(future, timeout=timeout + 2)
        except asyncio.TimeoutError:
            return None

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._queue.put(None)  # shutdown sentinel
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
