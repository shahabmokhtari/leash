"""Tests for the user-driven hook lifecycle.

Behavior under test:

* ``HookInstaller.install()`` and ``CopilotHookInstaller.install_user()`` /
  ``_install_to_directory()`` must NOT install the SessionStart bootstrap
  hook.  SessionStart is purely user-toggled via the dashboard.
* Existing user-toggled SessionStart entries must be preserved across
  regular install/uninstall calls.
* App shutdown always removes Claude runtime hooks (preserving SessionStart)
  AND Copilot user-level runtime hooks (also preserving sessionStart).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

from leash.config import ConfigurationManager
from leash.config import create_default_configuration
from leash.services.copilot_hook_installer import CopilotHookInstaller, SCRIPT_MARKER
from leash.services.hook_installer import HOOK_MARKER, HookInstaller


# ---------------------------------------------------------------------------
# Claude HookInstaller
# ---------------------------------------------------------------------------


class TestClaudeInstallSkipsSessionStart:
    def test_install_does_not_add_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = HookInstaller(config_manager=config_mgr, service_url="http://localhost:5050")
        installer.install()

        settings_path = fake_home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        # Regular hooks are present, but SessionStart must NOT be installed
        # automatically by the regular install path.
        assert "PreToolUse" in settings["hooks"]
        assert "SessionStart" not in settings["hooks"]

    def test_install_preserves_existing_user_toggled_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = HookInstaller(config_manager=config_mgr, service_url="http://localhost:5050")

        # Simulate dashboard-toggled SessionStart already being installed.
        installer.install_session_start_only()
        settings_path = fake_home / ".claude" / "settings.json"
        before = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" in before["hooks"]

        # Regular install must not wipe the user-toggled SessionStart.
        installer.install()
        after = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" in after["hooks"]
        ss_cmd = after["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert HOOK_MARKER in ss_cmd

    def test_uninstall_default_preserves_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = HookInstaller(config_manager=config_mgr, service_url="http://localhost:5050")

        installer.install_session_start_only()
        installer.install()

        # Default uninstall (no kwargs) must keep SessionStart in place.
        installer.uninstall()

        settings_path = fake_home / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" in settings["hooks"]
        assert "PreToolUse" not in settings.get("hooks", {})


# ---------------------------------------------------------------------------
# Copilot CopilotHookInstaller
# ---------------------------------------------------------------------------


class TestCopilotInstallSkipsSessionStart:
    def test_install_user_does_not_add_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_mgr)
        installer.install_user()

        hooks_dir = fake_home / ".copilot" / "hooks"
        hooks_json = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        assert "preToolUse" in hooks_json["hooks"]
        assert "sessionStart" not in hooks_json["hooks"]
        # script files for sessionStart should NOT be created by install_user
        assert not (hooks_dir / "sessionStart.sh").exists()
        assert not (hooks_dir / "sessionStart.ps1").exists()

    def test_install_user_preserves_existing_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_mgr)

        installer.install_session_start_only()
        hooks_dir = fake_home / ".copilot" / "hooks"
        before = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        assert "sessionStart" in before["hooks"]

        installer.install_user()
        after = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        assert "sessionStart" in after["hooks"]
        # Script files for sessionStart must still be on disk after a regular install
        ss_entry = after["hooks"]["sessionStart"][0]
        assert SCRIPT_MARKER in ss_entry.get("description", "")

    def test_uninstall_user_preserves_session_start(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config_mgr = ConfigurationManager(config=create_default_configuration())
        installer = CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_mgr)

        installer.install_session_start_only()
        installer.install_user()
        installer.uninstall_user()

        hooks_dir = fake_home / ".copilot" / "hooks"
        hooks_json_path = hooks_dir / "hooks.json"
        # File should still exist because sessionStart is preserved
        assert hooks_json_path.exists()
        hooks_json = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        assert "sessionStart" in hooks_json["hooks"]
        assert "preToolUse" not in hooks_json.get("hooks", {})
        # sessionStart scripts should be preserved
        assert (hooks_dir / "sessionStart.sh").exists()
        assert (hooks_dir / "sessionStart.ps1").exists()


# ---------------------------------------------------------------------------
# Shutdown lifecycle
# ---------------------------------------------------------------------------


class TestShutdownAlwaysRemovesRuntimeHooks:
    def test_shutdown_calls_uninstall_for_claude_and_copilot(self):
        from leash.app import lifespan

        source = inspect.getsource(lifespan)
        yield_idx = source.index("yield")
        shutdown_source = source[yield_idx:]

        assert "hook_installer.uninstall(preserve_session_start=True)" in shutdown_source
        assert "copilot_hook_installer.uninstall_user()" in shutdown_source


# ---------------------------------------------------------------------------
# Auto-start (SessionStart) is dashboard-only — never invoked from startup.
# ---------------------------------------------------------------------------


class TestAutostartHookNotAutoInstalled:
    def test_install_hooks_on_startup_does_not_call_session_start_install(self):
        from leash.app import _install_hooks_on_startup
        from types import SimpleNamespace

        config = SimpleNamespace(
            hooks_user_uninstalled=False,
            copilot_hooks_user_uninstalled=False,
        )
        hook_installer = MagicMock()
        copilot_installer = MagicMock()
        console_svc = MagicMock()

        _install_hooks_on_startup("both", config, hook_installer, copilot_installer, console_svc)

        # Regular install paths are exercised
        hook_installer.install.assert_called_once()
        copilot_installer.install_user.assert_called_once()
        # SessionStart-specific install paths must NEVER be triggered automatically
        hook_installer.install_session_start_only.assert_not_called()
        copilot_installer.install_session_start_only.assert_not_called()
