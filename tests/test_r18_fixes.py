"""Regression tests for round 18 review fixes."""

from __future__ import annotations

import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestShutdownPreservesAutostart:
    """Verify graceful cleanup keeps the SessionStart bootstrap path intact."""

    def test_app_shutdown_uses_preserve_session_start(self):
        from leash.app import lifespan

        source = inspect.getsource(lifespan)
        assert "preserve_session_start=True" in source

    def test_uninstall_preserve_session_start_keeps_bootstrap_hook(self, tmp_path, monkeypatch):
        from leash.services.hook_installer import HOOK_MARKER, HookInstaller

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        config_mgr = MagicMock()
        config_mgr.get_configuration.return_value = MagicMock(hook_handlers={})
        installer = HookInstaller(config_manager=config_mgr)
        installer._settings_path = settings_path

        remove_script = MagicMock()
        monkeypatch.setattr(installer, "_remove_session_start_script", remove_script)

        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": f"bash boot.sh {HOOK_MARKER}"}]}],
                "PreToolUse": [{"hooks": [{"type": "command", "command": f"curl x {HOOK_MARKER}"}]}],
            }
        }), encoding="utf-8")

        installer.uninstall(preserve_session_start=True)

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" in result["hooks"]
        assert "PreToolUse" not in result["hooks"]
        remove_script.assert_not_called()


class TestSessionStartUrlRefresh:
    """Verify SessionStart uses the freshest persisted service URL."""

    def test_ensure_service_running_checks_metadata_url_first(self, monkeypatch):
        import leash.session_start_hook as mod

        monkeypatch.setattr(mod, "load_launch_metadata", lambda path=None: {
            "serviceUrl": "http://127.0.0.1:9999",
            "launcher": ["python", "-m", "leash"],
            "host": "127.0.0.1",
            "port": 9999,
        })

        seen: list[str] = []

        def fake_health(url: str, timeout_seconds: float = 1.5) -> bool:
            seen.append(url)
            return url == "http://127.0.0.1:9999"

        start_proc = MagicMock(return_value=True)
        monkeypatch.setattr(mod, "is_service_healthy", fake_health)
        monkeypatch.setattr(mod, "start_background_process", start_proc)

        assert mod.ensure_service_running("http://127.0.0.1:5050") is True
        assert "http://127.0.0.1:9999" in seen
        start_proc.assert_not_called()

    def test_run_session_hook_proxy_forwards_to_metadata_url(self, monkeypatch):
        import leash.session_start_hook as mod

        monkeypatch.setattr(mod, "load_launch_metadata", lambda path=None: {
            "serviceUrl": "http://127.0.0.1:9999",
        })
        monkeypatch.setattr(mod, "ensure_service_running", lambda url: url == "http://127.0.0.1:9999")
        monkeypatch.setattr(mod.sys, "stdin", io.StringIO("{}"))

        captured: dict[str, str] = {}

        def fake_forward(service_url: str, provider: str, event: str, raw_input: str) -> str:
            captured["service_url"] = service_url
            captured["provider"] = provider
            captured["event"] = event
            captured["raw_input"] = raw_input
            return "{}"

        monkeypatch.setattr(mod, "forward_hook_request", fake_forward)
        monkeypatch.setattr(mod, "_write_hook_output", lambda payload: None)

        assert mod.run_session_hook_proxy("claude", "SessionStart", "http://127.0.0.1:5050") == 0
        assert captured["service_url"] == "http://127.0.0.1:9999"


class TestConfigUpdateResync:
    """Verify config saves refresh managed Copilot hook installs."""

    def test_update_config_resyncs_installed_copilot_hooks(self, tmp_path: Path):
        from leash.config import ConfigurationManager, create_default_configuration
        from leash.routes.config import router

        app = FastAPI()
        app.include_router(router)
        app.state.config_manager = ConfigurationManager(
            config=create_default_configuration(),
            config_path=tmp_path / "config.json",
        )
        app.state.hook_installer = MagicMock()
        app.state.copilot_hook_installer = MagicMock()
        app.state.copilot_hook_installer.is_user_installed.return_value = True
        app.state.transcript_watcher = None

        with TestClient(app) as client:
            payload = app.state.config_manager.get_configuration().model_dump(by_alias=True)
            response = client.put("/api/config", json=payload)

        assert response.status_code == 200
        app.state.hook_installer.install.assert_called_once()
        app.state.copilot_hook_installer.install_user.assert_called_once()


class TestPreValidationFallbacks:
    """Verify script-driven denies remain denies even without a registered harness."""

    @pytest.mark.asyncio
    async def test_deny_uses_provider_fallback_when_harness_missing(self):
        from leash.routes._pre_validation import _build_response

        response = await _build_response(
            decision="script-denied",
            approve=False,
            reason="blocked by script",
            hook_input=SimpleNamespace(
                hook_event_name="PreToolUse",
                tool_name="Bash",
                tool_input={"command": "rm -rf *"},
                session_id="s1",
                provider="copilot",
            ),
            handler=SimpleNamespace(name="test", threshold=85, prompt_template=None),
            harness_client=None,
            session_manager=None,
            trigger_service=None,
            console_status=None,
            adaptive_service=None,
            mode="enforce",
            event="PreToolUse",
        )

        body = json.loads(response.body)
        assert body["permissionDecision"] == "deny"
        assert "blocked by script" in body["permissionDecisionReason"]


class TestConsoleStatusAndMessaging:
    """Verify hook status and SessionStart messaging are accurate."""

    def test_copilot_startup_marks_hooks_installed(self):
        from leash.app import _install_hooks_on_startup

        config = SimpleNamespace(
            hooks_user_uninstalled=False,
            copilot_hooks_user_uninstalled=False,
        )
        hook_installer = MagicMock()
        copilot_installer = MagicMock()
        console_svc = MagicMock()

        _install_hooks_on_startup("copilot", config, hook_installer, copilot_installer, console_svc)

        console_svc.set_hooks_installed.assert_called_once_with(True)

    def test_session_start_api_message_mentions_copilot_best_effort(self):
        from leash.routes.hooks import router

        app = FastAPI()
        app.include_router(router)
        app.state.hook_installer = MagicMock()
        app.state.copilot_hook_installer = MagicMock()

        with TestClient(app) as client:
            response = client.post("/api/hooks/session-start/install")

        assert response.status_code == 200
        assert "best-effort" in response.json()["message"]


class _DummyProc:
    def __init__(self, output: str):
        self._output = output.encode("utf-8")

    async def communicate(self):
        return self._output, b""


class TestCopilotModelCacheTTL:
    """Verify successful Copilot model discovery refreshes after its TTL expires."""

    @pytest.mark.asyncio
    async def test_positive_cache_expires_and_refetches(self, monkeypatch):
        import leash.routes.config as mod

        monkeypatch.setattr(mod, "_copilot_models_cache", None)
        monkeypatch.setattr(mod, "_copilot_models_cache_expires_at", 0.0)
        monkeypatch.setattr(mod, "_copilot_models_negative_until", 0.0)
        monkeypatch.setattr(mod, "_COPILOT_MODELS_POSITIVE_TTL", 1)
        monkeypatch.setattr(mod.shutil, "which", lambda _: "copilot")

        now = {"value": 0.0}
        monkeypatch.setattr(mod.time, "monotonic", lambda: now["value"])

        outputs = iter([
            '  `model`:\n    - gpt-5.4\n',
            '  `model`:\n    - gpt-5.5\n',
        ])
        calls = {"count": 0}

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls["count"] += 1
            return _DummyProc(next(outputs))

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        models_a = await mod._fetch_copilot_models()
        now["value"] = 2.0
        models_b = await mod._fetch_copilot_models()

        assert any(m["value"] == "gpt-5.4" for m in models_a)
        assert any(m["value"] == "gpt-5.5" for m in models_b)
        assert calls["count"] == 2
