"""Custom logic handler -- handles SessionStart / SessionEnd events."""

from __future__ import annotations

import asyncio
import glob as glob_mod
import logging
from pathlib import Path

from leash.models.handler_config import HandlerConfig
from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput

logger = logging.getLogger(__name__)

# Project descriptor files used for project-type detection
_PROJECT_FILES: list[str] = [
    "package.json",
    "*.csproj",
    "*.sln",
    "Cargo.toml",
    "pyproject.toml",
    "go.mod",
]


class CustomLogicHandler:
    """Handles SessionStart and SessionEnd events with project-type detection."""

    async def handle(
        self,
        input: HookInput,
        config: HandlerConfig,
        session_context: str,
    ) -> HookOutput:
        match input.hook_event_name:
            case "SessionStart":
                return await self._handle_session_start(input, config)
            case "SessionEnd":
                return await self._handle_session_end(input, config)
            case _:
                return self._handle_default(input)

    # ------------------------------------------------------------------
    # Event-specific handlers
    # ------------------------------------------------------------------

    async def _handle_session_start(
        self,
        input: HookInput,
        config: HandlerConfig,
    ) -> HookOutput:
        logger.info("Session started: %s", input.session_id)
        context_parts: list[str] = []
        protection_message = None

        if _get_config_bool(config, "showProtectionMessage", default=True):
            protection_message = _get_config_str(
                config,
                "protectionMessage",
                default="Leash protection is active for this session.",
            )

        if _get_config_bool(config, "loadProjectContext") and input.cwd:
            project_ctx = _load_project_context(input.cwd)
            if project_ctx:
                context_parts.append(project_ctx)

        if _get_config_bool(config, "checkGitStatus") and input.cwd:
            git_status = await _get_git_status(input.cwd)
            if git_status:
                context_parts.append(f"Git status: {git_status}")

        return HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning="Session initialized",
            category="session-start",
            system_message=protection_message,
            additional_context="\n".join(context_parts) if context_parts else None,
        )

    async def _handle_session_end(
        self,
        input: HookInput,
        config: HandlerConfig,
    ) -> HookOutput:
        logger.info("Session ended: %s", input.session_id)

        if _get_config_bool(config, "archiveSession"):
            logger.info("Archiving session %s", input.session_id)

        return HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning="Session cleanup complete",
            category="session-end",
        )

    @staticmethod
    def _handle_default(input: HookInput) -> HookOutput:
        logger.info(
            "Custom logic handler invoked for %s in session %s",
            input.hook_event_name,
            input.session_id,
        )
        return HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning="Custom logic handler - no specific logic for this event type",
            category="custom",
        )


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

def _load_project_context(working_dir: str) -> str | None:
    """Look for common project descriptor files to detect the project type."""
    try:
        for pattern in _PROJECT_FILES:
            matches = glob_mod.glob(str(Path(working_dir) / pattern))
            if matches:
                filename = Path(matches[0]).name
                return f"Project type detected: {filename}"
    except OSError as exc:
        logger.debug("Failed to load project context for %s: %s", working_dir, exc)
    return None


async def _get_git_status(working_dir: str) -> str | None:
    """Run ``git status --short`` and return the output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--short",
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0 and stdout:
            return stdout.decode().strip()
    except (OSError, asyncio.TimeoutError) as exc:
        logger.debug("Failed to get git status for %s: %s", working_dir, exc)
    return None


def _get_config_bool(config: HandlerConfig, key: str, *, default: bool = False) -> bool:
    """Read a boolean flag from the handler's extra config dict."""
    value = config.config.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return default


def _get_config_str(config: HandlerConfig, key: str, *, default: str) -> str:
    """Read a string value from the handler config."""
    value = config.config.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return default
