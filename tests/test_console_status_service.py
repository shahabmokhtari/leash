"""Tests for ConsoleStatusService."""

from __future__ import annotations

from unittest.mock import MagicMock

from leash.services.console_status_service import ConsoleStatusService, _terminal_size, _trim


class TestTrim:
    def test_short_string_unchanged(self):
        assert _trim("hello", 80) == "hello"

    def test_long_string_trimmed(self):
        result = _trim("a" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_exact_width_unchanged(self):
        assert _trim("hello", 5) == "hello"


class TestTerminalSize:
    def test_returns_tuple(self):
        cols, rows = _terminal_size()
        assert cols >= 40
        assert rows >= 10


class TestConsoleStatusService:
    def _make_service(self, hooks_installed=False):
        enforcement = MagicMock()
        enforcement.mode = "observe"
        svc = ConsoleStatusService(enforcement_service=enforcement, hooks_installed=hooks_installed)
        # Cancel the timer so it doesn't fire during tests
        svc.dispose()
        return svc

    def test_record_event_updates_counters(self):
        svc = self._make_service()
        svc.record_event("auto-approved", "Bash", 95, 100)
        assert svc._total_events == 1
        assert svc._approved == 1
        assert svc._tool_counts["Bash"] == 1

    def test_record_denied_event(self):
        svc = self._make_service()
        svc.record_event("denied", "Write", 30, 200)
        assert svc._denied == 1

    def test_record_passthrough_event(self):
        svc = self._make_service()
        svc.record_event("logged", "Read", None, None)
        assert svc._passthrough == 1

    def test_log_adds_to_buffer(self):
        svc = self._make_service()
        svc.log("Test message 1")
        svc.log("Test message 2")
        assert len(svc._log_lines) == 2
        assert "Test message 1" in svc._log_lines

    def test_set_hooks_installed(self):
        svc = self._make_service(hooks_installed=False)
        assert not svc._hooks_installed
        svc.set_hooks_installed(True)
        assert svc._hooks_installed

    def test_render_does_not_crash(self):
        svc = self._make_service(hooks_installed=True)
        svc.record_event("auto-approved", "Bash", 95, 100)
        svc.log("Some log line")
        # Should not raise
        svc._render()

    def test_multiple_tools_tracked(self):
        svc = self._make_service()
        svc.record_event("auto-approved", "Bash", 95, 100)
        svc.record_event("auto-approved", "Bash", 90, 50)
        svc.record_event("auto-approved", "Write", 98, 120)
        assert svc._tool_counts["Bash"] == 2
        assert svc._tool_counts["Write"] == 1

    def test_score_averaging(self):
        svc = self._make_service()
        svc.record_event("auto-approved", "Bash", 80, 100)
        svc.record_event("auto-approved", "Bash", 100, 100)
        assert svc._scored_events == 2
        assert svc._total_score == 180
