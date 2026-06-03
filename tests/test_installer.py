"""Tests for the first-run console installer."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pytest

import leash.__main__ as main_module
import leash.app as app_module
import leash.installer as installer_module
from leash.exceptions import ConfigurationException
from leash.installer import InstallerSelection, run_console_installer, should_run_installer


class TtyStringIO(StringIO):
    """StringIO with a configurable TTY flag."""

    def __init__(self, *, is_tty: bool):
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_should_run_installer_when_config_is_missing_and_interactive(tmp_path):
    config_path = tmp_path / "config.json"

    assert should_run_installer(
        config_path,
        stdin=TtyStringIO(is_tty=True),
        stdout=TtyStringIO(is_tty=True),
    ) is True


def test_should_not_run_installer_when_config_exists(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    assert should_run_installer(
        config_path,
        stdin=TtyStringIO(is_tty=True),
        stdout=TtyStringIO(is_tty=True),
    ) is False


def test_should_not_run_installer_without_interactive_terminal(tmp_path):
    config_path = tmp_path / "config.json"

    assert should_run_installer(
        config_path,
        stdin=TtyStringIO(is_tty=False),
        stdout=TtyStringIO(is_tty=True),
    ) is False


def test_run_console_installer_persists_selected_profile_and_mode(tmp_path):
    config_path = tmp_path / "config.json"
    answers = iter(["2", "3"])
    output = StringIO()

    selection = run_console_installer(
        config_path=config_path,
        no_hooks=False,
        input_func=lambda: next(answers),
        output=output,
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert selection == InstallerSelection(profile_key="permissive", enforcement_mode="enforce")
    assert saved["profiles"]["activeProfile"] == "permissive"
    assert saved["enforcementMode"] == "enforce"
    assert saved["enforcementEnabled"] is True
    assert "Installing hooks..." in output.getvalue()


def test_run_console_installer_uses_defaults_and_respects_no_hooks(tmp_path):
    config_path = tmp_path / "config.json"
    answers = iter(["", ""])
    output = StringIO()

    selection = run_console_installer(
        config_path=config_path,
        no_hooks=True,
        input_func=lambda: next(answers),
        output=output,
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert selection == InstallerSelection(profile_key="moderate", enforcement_mode="observe")
    assert saved["profiles"]["activeProfile"] == "moderate"
    assert saved["enforcementMode"] == "observe"
    assert saved["enforcementEnabled"] is False
    assert "Skipping hook installation (--no-hooks)." in output.getvalue()


def test_run_console_installer_reprompts_for_invalid_input(tmp_path):
    config_path = tmp_path / "config.json"
    answers = iter(["99", "trust", ""])
    output = StringIO()

    selection = run_console_installer(
        config_path=config_path,
        no_hooks=False,
        input_func=lambda: next(answers),
        output=output,
    )

    assert selection.profile_key == "trust"
    assert selection.enforcement_mode == "observe"
    assert "Please enter a valid option number or name." in output.getvalue()


def test_run_console_installer_exits_cleanly_when_save_fails(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    answers = iter(["", ""])
    output = StringIO()

    async def fake_persist_selection(*, config_path, selection):
        raise ConfigurationException(f"Cannot save configuration to {config_path}")

    monkeypatch.setattr(installer_module, "_persist_selection", fake_persist_selection)

    with pytest.raises(SystemExit, match="1"):
        run_console_installer(
            config_path=config_path,
            no_hooks=False,
            input_func=lambda: next(answers),
            output=output,
        )

    assert "Saving configuration..." in output.getvalue()
    assert "Setup failed: Cannot save configuration to" in output.getvalue()


def test_main_runs_installer_before_startup(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    fake_app = SimpleNamespace(state=SimpleNamespace())
    installer_calls: dict[str, object] = {}
    uvicorn_calls: dict[str, object] = {}

    def fake_run_console_installer(
        *,
        config_path,
        no_hooks,
        profile_default,
        enforcement_default,
    ):
        installer_calls["config_path"] = config_path
        installer_calls["no_hooks"] = no_hooks
        installer_calls["profile_default"] = profile_default
        installer_calls["enforcement_default"] = enforcement_default
        return InstallerSelection(profile_key="moderate", enforcement_mode="enforce")

    def fake_create_app(*, config_path):
        uvicorn_calls["config_path"] = config_path
        return fake_app

    def fake_uvicorn_run(app, *, host, port, log_level):
        uvicorn_calls["app"] = app
        uvicorn_calls["host"] = host
        uvicorn_calls["port"] = port
        uvicorn_calls["log_level"] = log_level

    monkeypatch.setattr(
        main_module,
        "should_run_installer",
        lambda config_path, stdin=None, stdout=None: True,
    )
    monkeypatch.setattr(main_module, "run_console_installer", fake_run_console_installer)
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    monkeypatch.setattr(main_module.uvicorn, "run", fake_uvicorn_run)

    main_module.main(["--config", str(config_path), "--enforce", "--host", "127.0.0.1", "--port", "9000"])

    assert installer_calls == {
        "config_path": str(config_path),
        "no_hooks": False,
        "profile_default": "moderate",
        "enforcement_default": "enforce",
    }
    assert fake_app.state.cli_enforce is False
    assert fake_app.state.cli_host == "127.0.0.1"
    assert fake_app.state.cli_port == 9000
    assert uvicorn_calls == {
        "config_path": str(config_path),
        "app": fake_app,
        "host": "127.0.0.1",
        "port": 9000,
        "log_level": "info",
    }
