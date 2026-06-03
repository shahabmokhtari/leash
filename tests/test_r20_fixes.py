"""Regression tests for round 20 review fixes."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leash.routes.hooks import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class TestSessionStartPartialFailureState:
    """Ensure partial failures report the real aggregate installed state."""

    def test_install_partial_failure_reports_actual_state(self):
        app = _make_app()

        claude = MagicMock()
        claude.is_session_start_installed.return_value = True

        copilot = MagicMock()
        copilot.install_session_start_only.side_effect = RuntimeError("boom")
        copilot.is_session_start_installed.return_value = False

        app.state.hook_installer = claude
        app.state.copilot_hook_installer = copilot

        with TestClient(app) as client:
            response = client.post("/api/hooks/session-start/install")

        assert response.status_code == 500
        body = response.json()
        assert body["installed"] is True
        assert body["claude"] is True
        assert body["copilot"] is False
        assert "Partial failure" in body["error"]

    def test_uninstall_partial_failure_reports_actual_state(self):
        app = _make_app()

        claude = MagicMock()
        claude.is_session_start_installed.return_value = False

        copilot = MagicMock()
        copilot.uninstall_session_start_only.side_effect = RuntimeError("boom")
        copilot.is_session_start_installed.return_value = True

        app.state.hook_installer = claude
        app.state.copilot_hook_installer = copilot

        with TestClient(app) as client:
            response = client.post("/api/hooks/session-start/uninstall")

        assert response.status_code == 500
        body = response.json()
        assert body["installed"] is True
        assert body["claude"] is False
        assert body["copilot"] is True
        assert "Partial failure" in body["error"]


class TestCopilotCommandAutodetect:
    """Ensure blank Copilot command really falls back to ``gh`` when needed."""

    def test_cli_client_autodetects_gh_when_copilot_missing(self, monkeypatch):
        from leash.models.configuration import LlmConfig
        from leash.services.copilot_cli_client import CopilotCliClient
        import leash.services.copilot_cli_client as mod

        monkeypatch.setattr(mod.shutil, "which", lambda exe: None if exe == "copilot" else "C:\\gh.exe")

        client = CopilotCliClient(config=LlmConfig(provider="copilot-cli", model="", command=""))
        cmd, add_copilot_arg = client._get_command()

        assert cmd == "gh"
        assert add_copilot_arg is True

    def test_persistent_client_autodetects_gh_when_copilot_missing(self, monkeypatch):
        from leash.models.configuration import LlmConfig
        from leash.services.persistent_copilot_client import PersistentCopilotClient
        import leash.services.copilot_cli_client as mod

        monkeypatch.setattr(mod.shutil, "which", lambda exe: None if exe == "copilot" else "C:\\gh.exe")

        client = PersistentCopilotClient(config=LlmConfig(provider="copilot-persistent", model="", command=""))
        cmd, args = client._get_command_and_args()

        assert cmd == "gh"
        assert args[0] == "copilot"
