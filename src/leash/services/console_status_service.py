"""Rich ANSI console UI with status header, stats, tools, scrolling logs, and footer."""

from __future__ import annotations

import collections
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING

from leash import __version__

if TYPE_CHECKING:
    from leash.services.enforcement_service import EnforcementService

# Fixed layout lines: header(1) + stats(1) + tools(1) + separator(1) + footer(1) = 5
_FIXED_LINES = 5


def _enable_ansi_on_windows() -> None:
    """Enable ANSI escape sequence processing on Windows 10+.

    Without this, raw escape codes are printed as visible text in cmd.exe
    and PowerShell.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(-11)
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass  # Pre-Win10 or no console attached


def _terminal_size() -> tuple[int, int]:
    """Return (columns, rows) of the terminal, with safe fallbacks."""
    try:
        cols, rows = os.get_terminal_size()
        return max(cols, 40), max(rows, 10)
    except (OSError, ValueError):
        return 80, 24


def _trim(text: str, width: int) -> str:
    """Trim a single-line string to fit within *width* columns, adding '...' if truncated."""
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


class ConsoleStatusService:
    """Full-screen ANSI console UI updated in-place on a 500 ms debounce.

    Layout (top to bottom):
        Header:    Leash | OBSERVE | Hooks: installed
        Stats:     OBSERVE : 8 events | approved: 4 | denied: 1 | ignored: 3
        Tools:     Tools: Bash 10 | Write 5 | Edit 3 ...
        Separator: ─────────────────────────
        Logs:      (scrolling, fills remaining height)
        Footer:    Leash v0.1.0 — press Ctrl+C to exit
    """

    def __init__(
        self,
        enforcement_service: EnforcementService,
        *,
        hooks_installed: bool = False,
    ) -> None:
        _enable_ansi_on_windows()

        self._enforcement = enforcement_service
        self._hooks_installed = hooks_installed

        self._write_lock = threading.Lock()
        self._dirty = False
        self._rendered_lines = 0  # how many lines were last written

        # Counters
        self._total_events = 0
        self._approved = 0
        self._denied = 0
        self._passthrough = 0
        self._scored_events = 0
        self._total_score = 0

        self._tool_counts: dict[str, int] = {}
        self._counts_lock = threading.Lock()

        # Log ring buffer (store raw strings, no ANSI)
        self._log_lines: collections.deque[str] = collections.deque(maxlen=500)
        self._log_lock = threading.Lock()

        # Handle terminal resize
        self._cols, self._rows = _terminal_size()
        if hasattr(signal, "SIGWINCH"):
            try:
                signal.signal(signal.SIGWINCH, self._on_resize)
            except (OSError, ValueError):
                pass  # not in main thread or not a TTY

        # Render timer
        self._timer: threading.Timer | None = None
        self._schedule_render()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_hooks_installed(self, installed: bool) -> None:
        self._hooks_installed = installed
        self._dirty = True

    def record_event(
        self,
        decision: str | None,
        tool_name: str | None,
        score: int | None,
        elapsed_ms: int | None,
    ) -> None:
        """Update stat counters."""
        self._total_events += 1

        if decision in ("auto-approved", "script-approved", "tray-approved"):
            self._approved += 1
        elif decision in ("denied", "script-denied", "tray-denied"):
            self._denied += 1
        else:
            self._passthrough += 1

        if score is not None:
            self._scored_events += 1
            self._total_score += score

        tool = tool_name or "other"
        with self._counts_lock:
            self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1

        self._dirty = True

    def log(self, message: str) -> None:
        """Append a log line to the scrolling section."""
        with self._log_lock:
            self._log_lines.append(message)
        self._dirty = True

    def dispose(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_resize(self, signum: int, frame: object) -> None:
        self._cols, self._rows = _terminal_size()
        self._dirty = True

    def _schedule_render(self) -> None:
        self._timer = threading.Timer(0.5, self._flush_render)
        self._timer.daemon = True
        self._timer.start()

    def _flush_render(self) -> None:
        try:
            if self._dirty:
                self._dirty = False
                self._render()
        finally:
            self._schedule_render()

    def _render(self) -> None:
        self._cols, self._rows = _terminal_size()
        cols, rows = self._cols, self._rows
        mode = self._enforcement.mode.upper()
        hooks_label = "Hooks: installed" if self._hooks_installed else "Hooks: NOT installed"

        # ── Header ──
        header = _trim(f"  Leash | {mode} | {hooks_label}", cols)

        # ── Stats ──
        total = self._total_events
        approved = self._approved
        denied = self._denied
        passthrough = self._passthrough
        scored = self._scored_events
        avg_score = self._total_score // scored if scored > 0 else 0

        stats = f"  {mode} : {total} events | approved: {approved} | denied: {denied} | ignored: {passthrough}"
        if scored > 0:
            stats += f" | avg-score: {avg_score}"
        stats = _trim(stats, cols)

        # ── Tools ──
        with self._counts_lock:
            tools_sorted = sorted(self._tool_counts.items(), key=lambda kv: kv[1], reverse=True)

        tools_parts: list[str] = []
        for name, count in tools_sorted:
            tools_parts.append(f"{name} {count}")
        tools_line = "  Tools: " + " | ".join(tools_parts) if tools_parts else "  Tools: (none)"
        tools_line = _trim(tools_line, cols)

        # ── Separator ──
        separator = "  " + "\u2500" * max(cols - 4, 10)

        # ── Footer ──
        footer = _trim(f"  Leash v{__version__} \u2014 press Ctrl+C to exit", cols)

        # ── Log section (fills remaining height) ──
        log_height = max(rows - _FIXED_LINES, 1)
        with self._log_lock:
            recent = list(self._log_lines)

        # Wrap long log lines: each log line can occupy multiple rows
        wrapped: list[str] = []
        for line in recent:
            while len(line) > cols:
                wrapped.append(line[:cols])
                line = line[cols:]
            wrapped.append(line)

        # Take the last log_height lines
        visible_logs = wrapped[-log_height:]
        # Pad with empty lines if not enough logs
        while len(visible_logs) < log_height:
            visible_logs.append("")

        # ── Assemble and write ──
        all_lines = [header, stats, tools_line, separator] + visible_logs + [footer]

        with self._write_lock:
            output = ""

            # Move cursor up to overwrite previous block
            if self._rendered_lines > 0:
                output += f"\x1b[{self._rendered_lines}A"

            for line in all_lines:
                output += "\x1b[2K"  # clear entire line
                output += line + "\n"

            self._rendered_lines = len(all_lines)
            try:
                sys.stdout.write(output)
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                pass
