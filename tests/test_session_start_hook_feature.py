"""Tests for SessionStart auto-start and messaging behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leash.config import ConfigurationManager, create_default_configuration
from leash.handlers.custom_logic import CustomLogicHandler
from leash.models import Configuration, HandlerConfig, HookEventConfig, HookInput, HookOutput
from leash.routes.claude_hook import router as claude_hook_router
from leash.services.copilot_hook_installer import CopilotHookInstaller
from leash.services.harness.claude import ClaudeHarnessClient
from leash.services.harness.copilot import CopilotHarnessClient
from leash.services.hook_installer import HookInstaller
from leash.session_start_hook import (
    build_autostart_command,
    build_service_url,
    load_launch_metadata,
    persist_launch_metadata,
)


def test_persist_launch_metadata_builds_autostart_command(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    persist_launch_metadata("localhost", 5051, config_path=str(tmp_path / "config.json"))
    metadata = load_launch_metadata()

    assert metadata is not None
    assert metadata["serviceUrl"] == "http://localhost:5051"

    command = build_autostart_command(metadata)
    assert command is not None
    assert "--host" in command
    assert "localhost" in command
    assert "--port" in command
    assert "5051" in command
    assert "--no-browser" in command


def test_build_service_url_wraps_ipv6_addresses():
    assert build_service_url("::1", 5050) == "http://[::1]:5050"


def test_hook_installer_writes_session_start_script(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_manager = ConfigurationManager(config=create_default_configuration())
    installer = HookInstaller(config_manager=config_manager, service_url="http://localhost:5050")

    # SessionStart is user-toggled — only installed via install_session_start_only.
    installer.install_session_start_only()

    settings_path = fake_home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    session_start_entries = settings["hooks"]["SessionStart"]
    assert len(session_start_entries) == 1

    command = session_start_entries[0]["hooks"][0]["command"]
    if os.name == "nt":
        assert "powershell -ExecutionPolicy Bypass" in command
        script_path = fake_home / ".leash" / "hooks" / "claude-session-start.ps1"
    else:
        assert command.startswith("bash ")
        script_path = fake_home / ".leash" / "hooks" / "claude-session-start.sh"

    script = script_path.read_text(encoding="utf-8")
    assert "--run-session-hook" in script
    assert "--hook-provider" in script
    assert "SessionStart" in script


def test_copilot_hook_installer_includes_session_start_script(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_manager = ConfigurationManager(config=create_default_configuration())
    installer = CopilotHookInstaller(service_url="http://localhost:5050", config_manager=config_manager)

    # sessionStart is user-toggled — only installed via install_session_start_only.
    installer.install_session_start_only()

    hooks_json_path = fake_home / ".copilot" / "hooks" / "hooks.json"
    hooks_json = json.loads(hooks_json_path.read_text(encoding="utf-8"))
    assert "sessionStart" in hooks_json["hooks"]

    script_path = fake_home / ".copilot" / "hooks" / "sessionStart.ps1"
    script = script_path.read_text(encoding="utf-8")
    assert "--run-session-hook" in script
    assert "--hook-provider" in script
    assert "sessionStart" in script


async def test_custom_logic_handler_returns_protection_message(monkeypatch):
    monkeypatch.setattr(
        "leash.handlers.custom_logic._load_project_context",
        lambda cwd: "Project type detected: pyproject.toml",
    )
    git_status = AsyncMock(return_value="M README.md")
    monkeypatch.setattr("leash.handlers.custom_logic._get_git_status", git_status)

    handler = CustomLogicHandler()
    output = await handler.handle(
        HookInput(hook_event_name="SessionStart", session_id="session-123", cwd="C:\\r\\leash-py"),
        HandlerConfig(
            mode="custom-logic",
            config={
                "showProtectionMessage": True,
                "loadProjectContext": True,
                "checkGitStatus": True,
            },
        ),
        "",
    )

    assert output.system_message == "Leash protection is active for this session."
    assert output.additional_context is not None
    assert "Project type detected: pyproject.toml" in output.additional_context
    assert "Git status: M README.md" in output.additional_context


def test_claude_harness_formats_session_start_response():
    client = ClaudeHarnessClient()
    output = HookOutput(
        category="session-start",
        system_message="Leash protection is active for this session.",
        additional_context="Project type detected: pyproject.toml",
    )

    response = client.format_response("SessionStart", output)

    assert response["systemMessage"] == "Leash protection is active for this session."
    assert response["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert response["hookSpecificOutput"]["additionalContext"] == "Project type detected: pyproject.toml"


def test_copilot_harness_normalizes_session_start():
    client = CopilotHarnessClient()
    assert client.normalize_event_name("sessionStart") == "SessionStart"


def test_claude_route_runs_custom_logic_when_observe_analysis_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "leash.handlers.custom_logic._load_project_context",
        lambda cwd: "Project type detected: pyproject.toml",
    )
    monkeypatch.setattr(
        "leash.handlers.custom_logic._get_git_status",
        AsyncMock(return_value=""),
    )

    app = FastAPI()
    app.include_router(claude_hook_router)
    app.state.harness_client_registry = SimpleNamespace(get=lambda name: ClaudeHarnessClient())
    app.state.config_manager = ConfigurationManager(
        config=Configuration(
            analyze_in_observe_mode=False,
            hook_handlers={
                "SessionStart": HookEventConfig(
                    enabled=True,
                    handlers=[
                        HandlerConfig(
                            name="session-start",
                            matcher="*",
                            mode="custom-logic",
                            config={"showProtectionMessage": True, "loadProjectContext": True},
                        )
                    ],
                )
            },
        )
    )
    app.state.session_manager = None
    app.state.handler_factory = SimpleNamespace(create=AsyncMock(return_value=(CustomLogicHandler(), None)))
    app.state.enforcement_service = SimpleNamespace(mode="observe")
    app.state.profile_service = None
    app.state.adaptive_threshold_service = None
    app.state.trigger_service = None
    app.state.console_status_service = None
    app.state.notification_service = None
    app.state.pending_decision_service = None

    client = TestClient(app)
    response = client.post(
        "/api/hooks/claude?event=SessionStart",
        json={"sessionId": "session-123", "cwd": str(tmp_path)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["systemMessage"] == "Leash protection is active for this session."
    assert body["hookSpecificOutput"]["hookEventName"] == "SessionStart"
