"""SessionStart hook helpers for auto-starting Leash."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)

_DEFAULT_HEALTH_TIMEOUT_SECONDS = 1.5
_DEFAULT_HOOK_TIMEOUT_SECONDS = 5.0
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 12.0
_DEFAULT_STARTUP_INTERVAL_SECONDS = 0.5


def get_launch_metadata_path() -> Path:
    """Return the persisted launcher metadata path."""
    return Path.home() / ".leash" / "launch.json"


def build_service_url(host: str, port: int) -> str:
    """Build a loopback-friendly service URL for hook callbacks."""
    connect_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    if ":" in connect_host:
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}"


def build_session_hook_command(provider: str, event: str, service_url: str) -> list[str]:
    """Build the hidden CLI invocation used by generated SessionStart hooks."""
    return [
        *resolve_launcher_command(),
        "--run-session-hook",
        "--hook-provider",
        provider,
        "--hook-event",
        event,
        "--service-url",
        service_url,
    ]


def resolve_launcher_command() -> list[str]:
    """Resolve the launcher command that can start Leash again later."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "leash"]


def persist_launch_metadata(host: str, port: int, config_path: str | None = None) -> None:
    """Persist enough launch metadata for SessionStart hooks to restart Leash."""
    metadata_path = get_launch_metadata_path()
    payload = {
        "launcher": resolve_launcher_command(),
        "host": host,
        "port": port,
        "configPath": str(Path(config_path).expanduser().resolve()) if config_path else None,
        "serviceUrl": build_service_url(host, port),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file then rename to avoid corruption on crash
    fd, tmp_path_str = tempfile.mkstemp(dir=metadata_path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        Path(tmp_path_str).replace(metadata_path)
    except BaseException:
        Path(tmp_path_str).unlink(missing_ok=True)
        raise


def load_launch_metadata(metadata_path: Path | None = None) -> dict[str, Any] | None:
    """Load persisted launcher metadata from disk."""
    path = metadata_path or get_launch_metadata_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("No launch metadata available at %s: %s", path, exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid launch metadata at %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Launch metadata at %s is not a JSON object", path)
        return None
    return data


def _resolve_service_url(service_url: str, metadata: dict[str, Any] | None) -> str:
    """Prefer the persisted runtime URL when launch metadata is newer than the hook."""
    if isinstance(metadata, dict):
        metadata_url = metadata.get("serviceUrl")
        if isinstance(metadata_url, str) and metadata_url:
            return metadata_url

        host = metadata.get("host")
        port = metadata.get("port")
        if isinstance(host, str) and host and isinstance(port, int):
            return build_service_url(host, port)

    return service_url


def build_autostart_command(metadata: dict[str, Any]) -> list[str] | None:
    """Build the detached Leash start command from persisted metadata."""
    launcher = metadata.get("launcher")
    host = metadata.get("host")
    port = metadata.get("port")

    if not isinstance(launcher, list) or not launcher or not all(isinstance(item, str) for item in launcher):
        return None
    if not isinstance(host, str) or not host:
        return None
    if not isinstance(port, int):
        return None

    command = [*launcher, "--host", host, "--port", str(port), "--no-browser"]
    config_path = metadata.get("configPath")
    if isinstance(config_path, str) and config_path:
        command.extend(["--config", config_path])
    return command


def run_session_hook_proxy(provider: str, event: str, service_url: str) -> int:
    """Ensure Leash is running, then forward the SessionStart hook payload."""
    raw_input = sys.stdin.read()
    metadata = load_launch_metadata()
    effective_service_url = _resolve_service_url(service_url, metadata)

    if not ensure_service_running(effective_service_url):
        _write_hook_output("{}")
        return 0

    response = forward_hook_request(effective_service_url, provider, event, raw_input)
    _write_hook_output(response or "{}")
    return 0


def ensure_service_running(service_url: str, metadata_path: Path | None = None) -> bool:
    """Start Leash in the background if it is not already healthy."""
    if is_service_healthy(service_url):
        return True

    metadata = load_launch_metadata(metadata_path)
    if metadata is None:
        logger.warning("Cannot auto-start Leash without launch metadata")
        return False

    effective_service_url = _resolve_service_url(service_url, metadata)
    if effective_service_url != service_url and is_service_healthy(effective_service_url):
        return True

    command = build_autostart_command(metadata)
    if command is None:
        logger.warning("Launch metadata is missing a usable command")
        return False

    if not start_background_process(command):
        return False

    deadline = time.monotonic() + _DEFAULT_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_service_healthy(effective_service_url):
            return True
        time.sleep(_DEFAULT_STARTUP_INTERVAL_SECONDS)

    logger.warning("Timed out waiting for Leash to become healthy at %s", effective_service_url)
    return False


def is_service_healthy(service_url: str, timeout_seconds: float = _DEFAULT_HEALTH_TIMEOUT_SECONDS) -> bool:
    """Return True when the Leash health endpoint responds successfully."""
    try:
        with request.urlopen(f"{service_url}/health", timeout=timeout_seconds) as response:
            return getattr(response, "status", 0) == 200
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Service health check failed for %s: %s", service_url, exc)
        return False


def start_background_process(command: list[str]) -> bool:
    """Start Leash detached from the hook process."""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if sys.platform == "win32":
        creationflags = 0
        for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            creationflags |= getattr(subprocess, flag_name, 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(command, **kwargs)
        logger.info("Started Leash in the background: %s", command[0])
        return True
    except OSError as exc:
        logger.warning("Failed to start Leash in the background: %s", exc)
        return False


_VALID_PROVIDERS = {"claude", "copilot"}
_VALID_EVENTS = {
    "SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
    "PreToolUseFailure", "PostToolUseFailure", "PermissionRequest", "UserPromptSubmit", "Stop",
    # Copilot CLI uses camelCase event names
    "sessionStart", "sessionEnd", "preToolUse", "postToolUse",
    "preToolUseFailure", "postToolUseFailure",
}

# Map Copilot camelCase event names to PascalCase for the API endpoint
_EVENT_NORMALIZE: dict[str, str] = {
    "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd",
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "preToolUseFailure": "PreToolUseFailure",
    "postToolUseFailure": "PostToolUseFailure",
}


def forward_hook_request(
    service_url: str,
    provider: str,
    event: str,
    raw_input: str,
    timeout_seconds: float = _DEFAULT_HOOK_TIMEOUT_SECONDS,
) -> str | None:
    """Forward the original SessionStart payload to the running Leash service."""
    if provider not in _VALID_PROVIDERS:
        logger.warning("Invalid hook provider: %s", provider)
        return None
    if event not in _VALID_EVENTS:
        logger.warning("Invalid hook event: %s", event)
        return None

    # Normalize Copilot camelCase to PascalCase for the API endpoint
    event = _EVENT_NORMALIZE.get(event, event)

    url = f"{service_url}/api/hooks/{provider}?event={event}"
    payload = raw_input.encode("utf-8") if raw_input else b"{}"
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Failed to forward %s SessionStart hook to %s: %s", provider, url, exc)
        return None


def _write_hook_output(payload: str) -> None:
    sys.stdout.write(payload)
    if payload and not payload.endswith("\n"):
        sys.stdout.write("\n")
