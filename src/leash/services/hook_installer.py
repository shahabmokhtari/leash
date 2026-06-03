"""Install/uninstall curl hooks in ~/.claude/settings.json."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

from leash.session_start_hook import build_session_hook_command

if TYPE_CHECKING:
    from leash.config import ConfigurationManager

logger = logging.getLogger(__name__)

# Marker used to identify our hooks vs user's own hooks
HOOK_MARKER = "# leash"

_SAFE_URL_RE = re.compile(r"^https?://[\w.\-:\[\]]+(?::\d+)?/?\Z")


def _validate_service_url(url: str) -> str:
    """Validate that the service URL is safe to interpolate into shell scripts."""
    if not _SAFE_URL_RE.match(url):
        raise ValueError(f"Invalid service URL (contains unsafe characters): {url!r}")
    return url


class HookInstaller:
    """Manages Claude hook installation in ~/.claude/settings.json."""

    def __init__(
        self,
        config_manager: ConfigurationManager,
        service_url: str = "http://localhost:5050",
    ) -> None:
        self._config_manager = config_manager
        self._service_url = _validate_service_url(service_url)
        self._settings_path = Path.home() / ".claude" / "settings.json"

    def is_installed(self) -> bool:
        """Check if any leash hooks exist in Claude settings."""
        try:
            if not self._settings_path.exists():
                return False

            doc = self._load_settings()
            hooks = doc.get("hooks")
            if not hooks:
                return False

            return self._contains_our_hooks(hooks)
        except Exception as e:
            logger.warning("Failed to check hook installation status: %s", e)
            return False

    def install(self) -> None:
        """Install hooks derived from the app's hookHandlers config.

        Always removes our old hooks first to prevent duplication.
        User's own hooks (without our marker) are preserved.

        Note: SessionStart is intentionally excluded — it is the auto-start
        bootstrap hook and is only installed when the user explicitly enables
        it via the dashboard (``install_session_start_only``).  Any existing
        user-toggled SessionStart entry is preserved across this call.
        """
        app_config = self._config_manager.get_configuration()
        logger.debug("Syncing Claude hooks from app config (%d event types)", len(app_config.hook_handlers))

        doc = self._load_or_create_settings()
        hooks: dict[str, Any] = doc.get("hooks", {})

        # Step 1: Remove ALL our old hooks (by marker) to prevent duplication.
        # SessionStart is preserved because it is user-toggled separately.
        self._remove_our_hooks(hooks, preserve_keys={"SessionStart"})

        # Step 2: Add one curl hook per enabled event type (excluding SessionStart).
        # Server-side routing handles handler matching, so we only need
        # one hook entry per event type that pipes stdin JSON to our API.
        for event_name, event_config in app_config.hook_handlers.items():
            if event_name == "SessionStart":
                continue  # user-toggled only

            if not event_config.enabled:
                continue

            # Check if there are any enabled handlers for this event
            has_enabled = any(h.enabled for h in event_config.handlers)
            if not has_enabled:
                continue

            # Validate event name to prevent shell injection via config
            if not event_name.isalnum() and not all(c.isalnum() or c == '_' for c in event_name):
                logger.warning("Skipping hook event with invalid name: %s", event_name)
                continue

            arr: list[Any] = hooks.get(event_name, [])
            command = (
                f'curl -sS -X POST "{self._service_url}/api/hooks/claude?event={event_name}" '
                f'-H "Content-Type: application/json" -d @- {HOOK_MARKER}'
            )

            arr.append({
                "hooks": [{"type": "command", "command": command}],
            })

            hooks[event_name] = arr

        doc["hooks"] = hooks
        self._cleanup_empty_hooks(doc)

        self._write_settings(doc)
        logger.debug("Claude hooks synced successfully")

    def uninstall(self, *, preserve_session_start: bool = True) -> None:
        """Remove hooks marked with the leash marker.

        When ``preserve_session_start`` is true, keep the SessionStart bootstrap
        hook and its helper script so Claude can still auto-start Leash later.
        """
        logger.debug("Uninstalling Claude hooks (preserve_session_start=%s)", preserve_session_start)
        if not preserve_session_start:
            self._remove_session_start_script()

        if not self._settings_path.exists():
            logger.debug("Settings file not found, nothing to uninstall")
            return

        try:
            doc = self._load_settings()
            hooks = doc.get("hooks")
            if not hooks:
                return

            preserve_keys = {"SessionStart"} if preserve_session_start else None
            self._remove_our_hooks(hooks, preserve_keys=preserve_keys)
            self._cleanup_empty_hooks(doc)

            self._write_settings(doc)
            logger.debug("Claude hooks uninstalled successfully")
        except Exception as e:
            logger.error("Failed to uninstall hooks from %s: %s", self._settings_path, e)
            raise

    def is_session_start_installed(self) -> bool:
        """Check if a SessionStart hook with our marker exists in settings.json."""
        try:
            if not self._settings_path.exists():
                return False
            doc = self._load_settings()
            hooks = doc.get("hooks")
            if not hooks:
                return False
            for entry in hooks.get("SessionStart", []):
                if self._is_our_hook_entry(entry):
                    return True
            return False
        except Exception as e:
            logger.warning("Failed to check SessionStart installation: %s", e)
            return False

    def install_session_start_only(self) -> None:
        """Install ONLY the SessionStart hook (not other hook types)."""
        doc = self._load_or_create_settings()
        hooks: dict[str, Any] = doc.get("hooks", {})

        # Remove existing SessionStart hooks with our marker
        entries = hooks.get("SessionStart", [])
        if isinstance(entries, list):
            hooks["SessionStart"] = [e for e in entries if not self._is_our_hook_entry(e)]
        else:
            hooks["SessionStart"] = []

        # Add new SessionStart hook
        command = self._build_session_start_command()
        hooks["SessionStart"].append({
            "hooks": [{"type": "command", "command": command}],
        })

        doc["hooks"] = hooks
        self._write_settings(doc)
        logger.info("SessionStart hook installed")

    def uninstall_session_start_only(self) -> None:
        """Remove ONLY the SessionStart hooks with our marker."""
        self._remove_session_start_script()

        if not self._settings_path.exists():
            return

        doc = self._load_settings()
        hooks = doc.get("hooks")
        if not hooks:
            return

        entries = hooks.get("SessionStart", [])
        if isinstance(entries, list):
            hooks["SessionStart"] = [e for e in entries if not self._is_our_hook_entry(e)]

        self._cleanup_empty_hooks(doc)
        self._write_settings(doc)
        logger.info("SessionStart hook uninstalled")

    def _build_session_start_command(self) -> str:
        script_path = self._write_session_start_script()
        if os.name == "nt":
            return (
                f'powershell -ExecutionPolicy Bypass -NoProfile -File "{script_path}" '
                f"{HOOK_MARKER}"
            )
        return f"bash {shlex.quote(str(script_path))} {HOOK_MARKER}"

    def _load_settings(self) -> dict[str, Any]:
        """Load settings.json from disk."""
        raw = self._settings_path.read_text(encoding="utf-8")
        return json.loads(raw)  # type: ignore[no-any-return]

    def _load_or_create_settings(self) -> dict[str, Any]:
        """Load settings.json or return an empty dict."""
        if self._settings_path.exists():
            raw = self._settings_path.read_text(encoding="utf-8")
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}

        # Create parent directory if needed
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        return {}

    def _write_settings(self, doc: dict[str, Any]) -> None:
        """Write settings.json to disk atomically."""
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(doc, indent=2)
        tmp_path = self._settings_path.with_suffix(".tmp")
        tmp_path.write_text(raw, encoding="utf-8")
        tmp_path.replace(self._settings_path)

    def _contains_our_hooks(self, hooks: dict[str, Any]) -> bool:
        """Check if any hook entry contains our marker."""
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if self._is_our_hook_entry(entry):
                    return True
        return False

    def _remove_our_hooks(self, hooks: dict[str, Any], *, preserve_keys: set[str] | None = None) -> None:
        """Remove all hook entries containing our marker.

        ``preserve_keys`` lists event names whose entries should be left alone
        even if they were originally installed by us (used to keep the
        user-toggled SessionStart bootstrap intact across regular installs).
        """
        preserve = preserve_keys or set()
        for key in list(hooks.keys()):
            if key in preserve:
                continue
            entries = hooks[key]
            if not isinstance(entries, list):
                continue

            hooks[key] = [entry for entry in entries if not self._is_our_hook_entry(entry)]

    @staticmethod
    def _is_our_hook_entry(entry: Any) -> bool:
        """Check if a hook entry was installed by us."""
        if not isinstance(entry, dict):
            return False
        inner_hooks = entry.get("hooks")
        if not isinstance(inner_hooks, list):
            return False
        for h in inner_hooks:
            if isinstance(h, dict):
                cmd = h.get("command", "")
                if isinstance(cmd, str) and HOOK_MARKER in cmd:
                    return True
        return False

    @staticmethod
    def _cleanup_empty_hooks(doc: dict[str, Any]) -> None:
        """Remove empty hook arrays and the hooks key if empty."""
        hooks = doc.get("hooks")
        if not isinstance(hooks, dict):
            return

        empty_keys = [k for k, v in hooks.items() if isinstance(v, list) and len(v) == 0]
        for key in empty_keys:
            del hooks[key]

        if not hooks:
            doc.pop("hooks", None)

    def _write_session_start_script(self) -> Path:
        script_path = self._get_session_start_script_path()
        script_path.parent.mkdir(parents=True, exist_ok=True)
        command = build_session_hook_command("claude", "SessionStart", self._service_url)

        if os.name == "nt":
            content = self._build_powershell_session_start_script(command)
        else:
            content = self._build_bash_session_start_script(command)

        script_path.write_text(content, encoding="utf-8")
        if os.name != "nt":
            current_mode = script_path.stat().st_mode
            script_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return script_path

    @staticmethod
    def _build_bash_session_start_script(command: list[str]) -> str:
        return (
            "#!/bin/bash\n"
            f"{HOOK_MARKER}\n"
            "set -euo pipefail\n"
            "INPUT=$(cat)\n"
            f"if ! printf '%s' \"$INPUT\" | {shlex.join(command)}; then\n"
            "  echo '{}'\n"
            "fi\n"
        )

    @staticmethod
    def _build_powershell_session_start_script(command: list[str]) -> str:
        args_literal = ",\n".join(HookInstaller._quote_powershell_arg(arg) for arg in command)
        return (
            f"{HOOK_MARKER}\n"
            "try {\n"
            "    $inputData = [Console]::In.ReadToEnd()\n"
            "    $command = @(\n"
            f"{args_literal}\n"
            "    )\n"
            "    $job = Start-Job -ScriptBlock {\n"
            "        param($cmd, $args_, $input_)\n"
            "        $input_ | & $cmd $args_ | Out-String\n"
            "    } -ArgumentList $command[0], $command[1..($command.Length - 1)], $inputData\n"
            "    $completed = Wait-Job $job -Timeout 20\n"
            "    if ($completed) {\n"
            "        $response = Receive-Job $job\n"
            "        Remove-Job $job -Force\n"
            "        if ([string]::IsNullOrWhiteSpace($response)) {\n"
            "            Write-Output '{}'\n"
            "        } else {\n"
            "            Write-Output $response.TrimEnd()\n"
            "        }\n"
            "    } else {\n"
            "        Stop-Job $job -ErrorAction SilentlyContinue\n"
            "        Remove-Job $job -Force -ErrorAction SilentlyContinue\n"
            "        Write-Output '{}'\n"
            "    }\n"
            "} catch {\n"
            "    Write-Output '{}'\n"
            "}\n"
        )

    @staticmethod
    def _quote_powershell_arg(arg: str) -> str:
        return "        '" + arg.replace("'", "''") + "'"

    @staticmethod
    def _get_session_start_script_path() -> Path:
        suffix = ".ps1" if os.name == "nt" else ".sh"
        return Path.home() / ".leash" / "hooks" / f"claude-session-start{suffix}"

    def _remove_session_start_script(self) -> None:
        script_path = self._get_session_start_script_path()
        if not script_path.exists():
            return
        try:
            raw = script_path.read_text(encoding="utf-8")
            if HOOK_MARKER in raw:
                script_path.unlink()
        except OSError:
            logger.debug("Failed to remove SessionStart script at %s", script_path, exc_info=True)
