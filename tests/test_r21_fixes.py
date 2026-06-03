"""Regression tests for round 21 review fixes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leash.config import ConfigurationManager
from leash.models.configuration import Configuration, LlmConfig
from leash.routes.hooks import router


class TestCopilotModelFallback:
    """Copilot providers should not inherit Claude shorthand aliases by default."""

    def test_resolve_model_for_copilot_skips_global_claude_alias(self):
        from leash.services.llm_client_base import resolve_model_for_provider

        config = Configuration(llm=LlmConfig(provider="copilot-cli", model="opus", provider_models={}))

        assert resolve_model_for_provider(config, "copilot-cli") == ""
        assert resolve_model_for_provider(config, "copilot-persistent") == ""

    @pytest.mark.asyncio
    async def test_copilot_cli_omits_invalid_global_alias_model_flag(self, monkeypatch):
        import leash.services.copilot_cli_client as mod
        from leash.services.copilot_cli_client import CopilotCliClient

        cfg = Configuration(llm=LlmConfig(provider="copilot-cli", model="opus", provider_models={}))
        config_mgr = MagicMock()
        config_mgr.get_configuration.return_value = cfg

        captured: dict[str, object] = {}

        async def fake_run(file_name, args, timeout_ms, *rest, **kwargs):
            captured["file_name"] = file_name
            captured["args"] = list(args)
            return SimpleNamespace(output='{"safetyScore": 50, "reasoning": "ok", "category": "safe"}')

        monkeypatch.setattr(mod, "run_cli", fake_run)

        client = CopilotCliClient(config=cfg.llm, config_manager=config_mgr)
        await client._execute_copilot("test prompt", 1000)

        assert "--model" not in captured["args"]

    def test_persistent_copilot_omits_invalid_global_alias_model_flag(self):
        from leash.services.persistent_copilot_client import PersistentCopilotClient

        cfg = Configuration(llm=LlmConfig(provider="copilot-persistent", model="opus", provider_models={}))
        config_mgr = MagicMock()
        config_mgr.get_configuration.return_value = cfg

        client = PersistentCopilotClient(config=cfg.llm, config_manager=config_mgr)
        _cmd, args = client._get_command_and_args()

        assert "--model" not in args


class TestSessionStartLaunchMetadata:
    """Installing SessionStart hooks should also make auto-start metadata available."""

    def _make_app(self, tmp_path):
        app = FastAPI()
        app.include_router(router)
        app.state.hook_installer = MagicMock()
        app.state.copilot_hook_installer = MagicMock()
        app.state.hook_installer.is_session_start_installed.return_value = True
        app.state.copilot_hook_installer.is_session_start_installed.return_value = True
        app.state.config_manager = ConfigurationManager(
            config=Configuration(),
            config_path=tmp_path / "config.json",
        )
        app.state.cli_no_hooks = True
        app.state.cli_hooks_target = "none"
        app.state.cli_host = "127.0.0.1"
        app.state.cli_port = 6060
        app.state.config_path = str(tmp_path / "config.json")
        return app

    def test_session_start_install_persists_metadata_even_when_hooks_skipped(self, monkeypatch, tmp_path):
        import leash.session_start_hook as session_mod

        app = self._make_app(tmp_path)
        captured: dict[str, object] = {}

        def fake_persist(host: str, port: int, config_path: str | None = None):
            captured["host"] = host
            captured["port"] = port
            captured["config_path"] = config_path

        monkeypatch.setattr(session_mod, "persist_launch_metadata", fake_persist)

        with TestClient(app) as client:
            response = client.post("/api/hooks/session-start/install")

        assert response.status_code == 200
        assert captured == {
            "host": "127.0.0.1",
            "port": 6060,
            "config_path": str(tmp_path / "config.json"),
        }

    def test_session_start_install_reports_metadata_write_failure(self, monkeypatch, tmp_path):
        import leash.session_start_hook as session_mod

        app = self._make_app(tmp_path)
        app.state.hook_installer.is_session_start_installed.return_value = True
        app.state.copilot_hook_installer.is_session_start_installed.return_value = False

        def fail_persist(host: str, port: int, config_path: str | None = None):
            raise OSError("disk full")

        monkeypatch.setattr(session_mod, "persist_launch_metadata", fail_persist)

        with TestClient(app) as client:
            response = client.post("/api/hooks/session-start/install")

        assert response.status_code == 500
        body = response.json()
        assert body["installed"] is True
        assert body["claude"] is True
        assert body["copilot"] is False
        assert "Launch metadata" in body["error"]
