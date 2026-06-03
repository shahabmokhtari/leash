"""Tests for HookInstaller."""

from __future__ import annotations

import json
from pathlib import Path

from leash.models import Configuration, HandlerConfig, HookEventConfig

# ---------------------------------------------------------------------------
# HookInstaller stub
# ---------------------------------------------------------------------------

LEASH_MARKER = "# leash"


class HookInstaller:
    """Installs/uninstalls curl hooks in a settings JSON file."""

    def __init__(self, settings_path: str | Path, service_url: str, config: Configuration):
        self._settings_path = Path(settings_path)
        self._service_url = service_url
        self._config = config

    def _read_settings(self) -> dict:
        if self._settings_path.exists():
            return json.loads(self._settings_path.read_text())
        return {}

    def _write_settings(self, data: dict) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(data, indent=2))

    def _make_curl_command(self, event: str, matcher: str | None) -> str:
        url = f"{self._service_url}/api/hooks/claude?event={event}"
        return f'curl -sS -X POST "{url}" -H "Content-Type: application/json" -d @- {LEASH_MARKER}'

    def _is_our_hook(self, entry: dict) -> bool:
        hooks = entry.get("hooks", [])
        for h in hooks:
            cmd = h.get("command", "")
            if LEASH_MARKER in cmd:
                return True
        return False

    def install(self) -> None:
        settings = self._read_settings()
        hooks = settings.setdefault("hooks", {})

        for event_name, event_config in self._config.hook_handlers.items():
            if not event_config.enabled:
                # Remove our hooks for disabled events
                if event_name in hooks:
                    hooks[event_name] = [
                        e for e in hooks[event_name] if not self._is_our_hook(e)
                    ]
                    if not hooks[event_name]:
                        del hooks[event_name]
                continue

            # Get existing entries, remove our old ones
            existing = hooks.get(event_name, [])
            user_hooks = [e for e in existing if not self._is_our_hook(e)]

            # Build our new hooks
            our_hooks = []
            for handler in event_config.handlers:
                entry = {
                    "matcher": handler.matcher or "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": self._make_curl_command(event_name, handler.matcher),
                        }
                    ],
                }
                our_hooks.append(entry)

            hooks[event_name] = user_hooks + our_hooks

        # Remove empty hooks dict
        if not hooks:
            settings.pop("hooks", None)

        self._write_settings(settings)

    def uninstall(self) -> None:
        if not self._settings_path.exists():
            return

        settings = self._read_settings()
        hooks = settings.get("hooks", {})

        # Remove all our hooks
        events_to_remove = []
        for event_name, entries in hooks.items():
            hooks[event_name] = [e for e in entries if not self._is_our_hook(e)]
            if not hooks[event_name]:
                events_to_remove.append(event_name)

        for event_name in events_to_remove:
            del hooks[event_name]

        # Remove empty hooks dict
        if not hooks:
            settings.pop("hooks", None)

        self._write_settings(settings)

    def is_installed(self) -> bool:
        if not self._settings_path.exists():
            return False
        settings = self._read_settings()
        hooks = settings.get("hooks", {})
        for entries in hooks.values():
            for entry in entries:
                if self._is_our_hook(entry):
                    return True
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _default_config() -> Configuration:
    return Configuration(
        hook_handlers={
            "PermissionRequest": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(name="bash-analyzer", matcher="Bash", mode="llm-analysis", threshold=95),
                    HandlerConfig(name="file-read-analyzer", matcher="Read", mode="llm-analysis", threshold=93),
                ],
            ),
            "PreToolUse": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(name="pre-tool-logger", matcher="*", mode="log-only"),
                ],
            ),
            "Stop": HookEventConfig(
                enabled=True,
                handlers=[
                    HandlerConfig(name="stop-logger", mode="log-only"),
                ],
            ),
        }
    )


class TestHookInstaller:
    def test_install_creates_hooks(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()

        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        hooks = data["hooks"]

        assert "PermissionRequest" in hooks
        assert len(hooks["PermissionRequest"]) == 2
        assert "PreToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1
        assert "Stop" in hooks
        assert len(hooks["Stop"]) == 1

    def test_install_does_not_duplicate(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()
        installer.install()  # Second call

        data = json.loads(settings_path.read_text())
        assert len(data["hooks"]["PermissionRequest"]) == 2  # Not 4

    def test_install_preserves_user_hooks(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"

        # Write a user hook first
        settings_path.write_text(json.dumps({
            "hooks": {
                "PermissionRequest": [
                    {
                        "matcher": "CustomTool",
                        "hooks": [
                            {"type": "command", "command": "echo 'user hook'"}
                        ],
                    }
                ]
            }
        }))

        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()

        data = json.loads(settings_path.read_text())
        perm_req = data["hooks"]["PermissionRequest"]

        # 1 user hook + 2 from config = 3
        assert len(perm_req) == 3

        # Verify user hook is preserved
        user_cmds = [
            e["hooks"][0]["command"]
            for e in perm_req
            if "echo 'user hook'" in e.get("hooks", [{}])[0].get("command", "")
        ]
        assert len(user_cmds) == 1

    def test_install_preserves_other_settings(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"someOtherSetting": "preserved", "anotherKey": 42}))

        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()

        data = json.loads(settings_path.read_text())
        assert data["someOtherSetting"] == "preserved"
        assert data["anotherKey"] == 42

    def test_install_skips_disabled_events(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        config = _default_config()
        config.hook_handlers["PermissionRequest"].enabled = False

        installer = HookInstaller(settings_path, "http://localhost:5050", config)
        installer.install()

        data = json.loads(settings_path.read_text())
        assert "PermissionRequest" not in data.get("hooks", {})
        assert "PreToolUse" in data["hooks"]
        assert "Stop" in data["hooks"]

    def test_is_installed_false_when_no_file(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        assert installer.is_installed() is False

    def test_is_installed_true_after_install(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()
        assert installer.is_installed() is True

    def test_uninstall_removes_our_hooks_keeps_user(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()

        # Add a user hook
        data = json.loads(settings_path.read_text())
        data["hooks"]["CustomEvent"] = [
            {"hooks": [{"type": "command", "command": "echo 'user'"}]}
        ]
        data["userSetting"] = "keep"
        settings_path.write_text(json.dumps(data))

        installer.uninstall()

        result = json.loads(settings_path.read_text())
        assert result["userSetting"] == "keep"
        assert "CustomEvent" in result.get("hooks", {})
        assert installer.is_installed() is False

    def test_install_then_uninstall_leaves_clean(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:5050", _default_config())
        installer.install()
        assert installer.is_installed() is True

        installer.uninstall()
        assert installer.is_installed() is False

    def test_install_contains_correct_service_url(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        installer = HookInstaller(settings_path, "http://localhost:9999", _default_config())
        installer.install()

        content = settings_path.read_text()
        assert "http://localhost:9999" in content
        assert LEASH_MARKER in content

    def test_install_reflects_config_changes_on_reinstall(self, tmp_path: Path):
        settings_path = tmp_path / "settings.json"
        config = _default_config()
        installer = HookInstaller(settings_path, "http://localhost:5050", config)
        installer.install()

        data = json.loads(settings_path.read_text())
        assert len(data["hooks"]["PermissionRequest"]) == 2

        # Add a third handler
        config.hook_handlers["PermissionRequest"].handlers.append(
            HandlerConfig(name="wildcard", matcher="*", mode="llm-analysis", threshold=85)
        )

        installer2 = HookInstaller(settings_path, "http://localhost:5050", config)
        installer2.install()

        data2 = json.loads(settings_path.read_text())
        assert len(data2["hooks"]["PermissionRequest"]) == 3
