"""Tests for CopilotHookInstaller session-start-only methods."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from leash.services.copilot_hook_installer import SCRIPT_MARKER, CopilotHookInstaller


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    """Redirect Path.home() to a temp directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _make_installer(fake_home: Path) -> CopilotHookInstaller:
    config_mgr = MagicMock()
    config_mgr.get_configuration.return_value = MagicMock(hook_handlers={})
    return CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_mgr)


# ── is_session_start_installed ──


class TestIsSessionStartInstalled:
    def test_returns_false_when_no_file(self, fake_home):
        installer = _make_installer(fake_home)
        assert installer.is_session_start_installed() is False

    def test_returns_false_when_no_hooks(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        hooks_json.write_text("{}", encoding="utf-8")
        assert installer.is_session_start_installed() is False

    def test_returns_false_when_no_session_start(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {"preToolUse": [{"type": "command", "description": f"Leash {SCRIPT_MARKER}"}]}}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")
        assert installer.is_session_start_installed() is False

    def test_returns_true_when_session_start_exists(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {"sessionStart": [
            {"type": "command", "description": f"Leash - sessionStart {SCRIPT_MARKER}"}
        ]}}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")
        assert installer.is_session_start_installed() is True

    def test_returns_false_for_non_leash_session_start(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {"sessionStart": [
            {"type": "command", "description": "Some other tool"}
        ]}}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")
        assert installer.is_session_start_installed() is False


# ── install_session_start_only ──


class TestInstallSessionStartOnly:
    def test_creates_session_start_hook(self, fake_home):
        installer = _make_installer(fake_home)
        installer.install_session_start_only()

        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        root = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert "sessionStart" in root["hooks"]
        entries = root["hooks"]["sessionStart"]
        assert len(entries) == 1
        assert SCRIPT_MARKER in entries[0]["description"]

    def test_creates_scripts(self, fake_home):
        installer = _make_installer(fake_home)
        installer.install_session_start_only()

        hooks_dir = fake_home / ".copilot" / "hooks"
        assert (hooks_dir / "sessionStart.sh").exists()
        assert (hooks_dir / "sessionStart.ps1").exists()

        sh_content = (hooks_dir / "sessionStart.sh").read_text(encoding="utf-8")
        assert SCRIPT_MARKER in sh_content
        assert "--run-session-hook" in sh_content

    def test_does_not_touch_other_events(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {"preToolUse": [
            {"type": "command", "description": f"Leash - preToolUse {SCRIPT_MARKER}"}
        ]}}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")

        installer.install_session_start_only()

        result = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert "preToolUse" in result["hooks"]
        assert len(result["hooks"]["preToolUse"]) == 1  # Untouched

    def test_idempotent_no_duplicates(self, fake_home):
        installer = _make_installer(fake_home)
        installer.install_session_start_only()
        installer.install_session_start_only()

        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        root = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert len(root["hooks"]["sessionStart"]) == 1


# ── uninstall_session_start_only ──


class TestUninstallSessionStartOnly:
    def test_removes_session_start_entries(self, fake_home):
        installer = _make_installer(fake_home)
        installer.install_session_start_only()

        # Verify installed
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        assert installer.is_session_start_installed()

        installer.uninstall_session_start_only()

        # hooks.json should have no sessionStart, and may be deleted entirely
        if hooks_json.exists():
            root = json.loads(hooks_json.read_text(encoding="utf-8"))
            assert "sessionStart" not in root.get("hooks", {})

    def test_removes_scripts(self, fake_home):
        installer = _make_installer(fake_home)
        installer.install_session_start_only()

        hooks_dir = fake_home / ".copilot" / "hooks"
        assert (hooks_dir / "sessionStart.sh").exists()

        installer.uninstall_session_start_only()

        assert not (hooks_dir / "sessionStart.sh").exists()
        assert not (hooks_dir / "sessionStart.ps1").exists()

    def test_preserves_other_events(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {
            "preToolUse": [{"type": "command", "description": f"Leash - preToolUse {SCRIPT_MARKER}"}],
            "sessionStart": [{"type": "command", "description": f"Leash - sessionStart {SCRIPT_MARKER}"}],
        }}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")

        installer.uninstall_session_start_only()

        result = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert "preToolUse" in result["hooks"]
        assert "sessionStart" not in result["hooks"]

    def test_preserves_user_session_start_hooks(self, fake_home):
        installer = _make_installer(fake_home)
        hooks_json = fake_home / ".copilot" / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True, exist_ok=True)
        root = {"version": 1, "hooks": {"sessionStart": [
            {"type": "command", "description": "User's own hook"},
            {"type": "command", "description": f"Leash - sessionStart {SCRIPT_MARKER}"},
        ]}}
        hooks_json.write_text(json.dumps(root), encoding="utf-8")

        installer.uninstall_session_start_only()

        result = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert len(result["hooks"]["sessionStart"]) == 1
        assert "User's own hook" in result["hooks"]["sessionStart"][0]["description"]

    def test_no_file_is_noop(self, fake_home):
        installer = _make_installer(fake_home)
        installer.uninstall_session_start_only()  # Should not raise
