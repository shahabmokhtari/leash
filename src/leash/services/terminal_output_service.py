"""Ring buffer for real-time terminal / LLM subprocess output with SSE event subscribers.

Stores up to 2000 lines and fires callbacks for SSE consumers so they can
stream output in real time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class TerminalLine:
    """A single line of terminal output."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
    level: str = ""
    text: str = ""
    sequence_id: int = 0


# Callback type for real-time line subscribers
LineCallback = Callable[[TerminalLine], None]


class TerminalOutputService:
    """Thread-safe ring buffer for real-time LLM subprocess output.

    Stores up to *capacity* lines and fires events for SSE consumers.
    """

    CAPACITY = 2000

    def __init__(self) -> None:
        self._buffer: list[TerminalLine | None] = [None] * self.CAPACITY
        self._lock = threading.Lock()
        self._head: int = 0
        self._count: int = 0
        self._sequence_counter: int = 0
        self._subscribers: list[LineCallback] = []

    # -- Subscriber management ------------------------------------------------

    def subscribe(self, callback: LineCallback) -> None:
        """Register a callback that fires for each new line."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: LineCallback) -> None:
        """Remove a previously registered callback."""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    # -- Core operations ------------------------------------------------------

    def push(self, source: str, level: str, text: str) -> None:
        """Add a line to the ring buffer and notify subscribers.

        Empty *text* values are silently ignored.
        """
        if not text:
            return

        with self._lock:
            self._sequence_counter += 1
            line = TerminalLine(
                timestamp=datetime.now(timezone.utc),
                source=source,
                level=level,
                text=text,
                sequence_id=self._sequence_counter,
            )
            self._buffer[self._head % self.CAPACITY] = line
            self._head += 1
            if self._count < self.CAPACITY:
                self._count += 1

        # Fire events outside the lock to avoid deadlocks.
        # Snapshot the list to avoid RuntimeError if a callback modifies it.
        for cb in list(self._subscribers):
            try:
                cb(line)
            except Exception:
                pass

    def get_buffer(self) -> list[TerminalLine]:
        """Return all buffered lines in chronological order."""
        with self._lock:
            if self._count == 0:
                return []
            start = 0 if self._count < self.CAPACITY else self._head % self.CAPACITY
            result: list[TerminalLine] = []
            for i in range(self._count):
                item = self._buffer[(start + i) % self.CAPACITY]
                if item is not None:
                    result.append(item)
            return result

    def get_buffer_since(self, after_seq: int) -> list[TerminalLine]:
        """Return buffered lines with sequence_id > *after_seq*."""
        with self._lock:
            if self._count == 0:
                return []
            start = 0 if self._count < self.CAPACITY else self._head % self.CAPACITY
            result: list[TerminalLine] = []
            for i in range(self._count):
                item = self._buffer[(start + i) % self.CAPACITY]
                if item is not None and item.sequence_id > after_seq:
                    result.append(item)
            return result

    def clear(self) -> None:
        """Empty the buffer.  Does **not** reset the sequence counter."""
        with self._lock:
            self._buffer = [None] * self.CAPACITY
            self._head = 0
            self._count = 0
            # Don't reset _sequence_counter -- keeps SSE clients from getting stale data
