"""Tests for HookInstaller session-start methods and LLMClientProvider model change."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from leash.services.hook_installer import HOOK_MARKER, HookInstaller


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Redirect HookInstaller to a temp settings file."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    def _patch(installer: HookInstaller) -> HookInstaller:
        installer._settings_path = settings_path
        return installer

    return settings_path, _patch


def _make_installer(tmp_settings) -> HookInstaller:
    settings_path, patch = tmp_settings
    config_mgr = MagicMock()
    config_mgr.get_configuration.return_value = MagicMock(hook_handlers={})
    installer = HookInstaller(config_manager=config_mgr)
    return patch(installer)


# ── is_session_start_installed ──


class TestIsSessionStartInstalled:
    def test_returns_false_when_no_file(self, tmp_settings):
        installer = _make_installer(tmp_settings)
        settings_path = tmp_settings[0]
        if settings_path.exists():
            settings_path.unlink()
        assert installer.is_session_start_installed() is False

    def test_returns_false_when_no_hooks(self, tmp_settings):
        installer = _make_installer(tmp_settings)
        tmp_settings[0].write_text("{}", encoding="utf-8")
        assert installer.is_session_start_installed() is False

    def test_returns_false_when_no_session_start(self, tmp_settings):
        installer = _make_installer(tmp_settings)
        doc = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": f"curl {HOOK_MARKER}"}]}]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")
        assert installer.is_session_start_installed() is False

    def test_returns_true_when_session_start_exists(self, tmp_settings):
        installer = _make_installer(tmp_settings)
        doc = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"bash script.sh {HOOK_MARKER}"}]}]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")
        assert installer.is_session_start_installed() is True

    def test_returns_false_for_non_leash_session_start(self, tmp_settings):
        installer = _make_installer(tmp_settings)
        doc = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")
        assert installer.is_session_start_installed() is False


# ── install_session_start_only ──


class TestInstallSessionStartOnly:
    def test_creates_session_start_hook(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        # Stub _write_session_start_script to avoid disk I/O
        monkeypatch.setattr(installer, "_write_session_start_script", lambda: Path("/fake/script.sh"))
        tmp_settings[0].write_text("{}", encoding="utf-8")

        installer.install_session_start_only()

        doc = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert "SessionStart" in doc["hooks"]
        entries = doc["hooks"]["SessionStart"]
        assert len(entries) == 1
        assert HOOK_MARKER in entries[0]["hooks"][0]["command"]

    def test_does_not_touch_other_events(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_write_session_start_script", lambda: Path("/fake/script.sh"))

        # Pre-existing hooks
        doc = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": f"curl {HOOK_MARKER}"}]}]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")

        installer.install_session_start_only()

        result = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert "PreToolUse" in result["hooks"]
        assert len(result["hooks"]["PreToolUse"]) == 1  # Untouched

    def test_idempotent_no_duplicates(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_write_session_start_script", lambda: Path("/fake/script.sh"))
        tmp_settings[0].write_text("{}", encoding="utf-8")

        installer.install_session_start_only()
        installer.install_session_start_only()

        doc = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert len(doc["hooks"]["SessionStart"]) == 1


# ── uninstall_session_start_only ──


class TestUninstallSessionStartOnly:
    def test_removes_session_start_hook(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_remove_session_start_script", lambda: None)

        doc = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"bash {HOOK_MARKER}"}]}]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")

        installer.uninstall_session_start_only()

        result = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert "SessionStart" not in result.get("hooks", {})

    def test_preserves_user_session_start_hooks(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_remove_session_start_script", lambda: None)

        doc = {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "echo user-hook"}]},
            {"hooks": [{"type": "command", "command": f"bash {HOOK_MARKER}"}]},
        ]}}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")

        installer.uninstall_session_start_only()

        result = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert len(result["hooks"]["SessionStart"]) == 1
        assert "user-hook" in result["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    def test_preserves_other_event_hooks(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_remove_session_start_script", lambda: None)

        doc = {"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": f"bash {HOOK_MARKER}"}]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": f"curl {HOOK_MARKER}"}]}],
        }}
        tmp_settings[0].write_text(json.dumps(doc), encoding="utf-8")

        installer.uninstall_session_start_only()

        result = json.loads(tmp_settings[0].read_text(encoding="utf-8"))
        assert "PreToolUse" in result["hooks"]
        assert len(result["hooks"]["PreToolUse"]) == 1

    def test_no_file_is_noop(self, tmp_settings, monkeypatch):
        installer = _make_installer(tmp_settings)
        monkeypatch.setattr(installer, "_remove_session_start_script", lambda: None)
        if tmp_settings[0].exists():
            tmp_settings[0].unlink()
        installer.uninstall_session_start_only()  # Should not raise


# ── LLMClientProvider model change ──


class TestModelChangeDetection:
    @pytest.mark.asyncio
    async def test_model_change_recreates_client(self):
        from leash.models.configuration import Configuration, LlmConfig
        from leash.services.llm_client_provider import LLMClientProvider

        config = Configuration(llm=LlmConfig(provider="claude-cli", model="sonnet"))
        config_mgr = MagicMock()
        config_mgr.get_configuration.return_value = config

        provider = LLMClientProvider(config_manager=config_mgr)
        client_a = await provider.get_client()

        # Change model
        config.llm.model = "opus"
        client_b = await provider.get_client()

        assert client_a is not client_b
        await provider.dispose()

    @pytest.mark.asyncio
    async def test_same_model_reuses_client(self):
        from leash.models.configuration import Configuration, LlmConfig
        from leash.services.llm_client_provider import LLMClientProvider

        config = Configuration(llm=LlmConfig(provider="claude-cli", model="sonnet"))
        config_mgr = MagicMock()
        config_mgr.get_configuration.return_value = config

        provider = LLMClientProvider(config_manager=config_mgr)
        client_a = await provider.get_client()
        client_b = await provider.get_client()

        assert client_a is client_b
        await provider.dispose()


# ── ClaudeCliClient empty model ──


class TestEmptyModel:
    def test_empty_model_does_not_raise(self):
        from leash.models.configuration import LlmConfig
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-cli", model="")
        client = ClaudeCliClient(config=config)  # Should not raise
        assert client is not None

    def test_empty_model_omits_flag(self):
        from leash.models.configuration import LlmConfig
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-cli", model="")
        client = ClaudeCliClient(config=config)
        args = client._build_command_args("test prompt")
        assert "--model" not in args

    def test_nonempty_model_includes_flag(self):
        from leash.models.configuration import LlmConfig
        from leash.services.claude_cli_client import ClaudeCliClient

        config = LlmConfig(provider="claude-cli", model="sonnet")
        client = ClaudeCliClient(config=config)
        args = client._build_command_args("test prompt")
        assert "--model" in args
