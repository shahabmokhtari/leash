"""Context injection handler -- enriches responses with git branch and error info."""

from __future__ import annotations

import asyncio
import logging

from leash.models.handler_config import HandlerConfig
from leash.models.hook_input import HookInput
from leash.models.hook_output import HookOutput

logger = logging.getLogger(__name__)


class ContextInjectionHandler:
    """Injects contextual information (git branch, recent errors) as a SystemMessage."""

    async def handle(
        self,
        input: HookInput,
        config: HandlerConfig,
        session_context: str,
    ) -> HookOutput:
        context_parts: list[str] = []

        inject_git_branch = _get_config_bool(config, "injectGitBranch", default=False)
        inject_recent_errors = _get_config_bool(config, "injectRecentErrors", default=False)

        if inject_git_branch and input.cwd:
            branch = await _get_git_branch(input.cwd)
            if branch:
                context_parts.append(f"[Git Branch: {branch}]")

        if inject_recent_errors and session_context:
            errors = _extract_recent_errors(session_context)
            if errors:
                context_parts.append(f"[Recent Errors: {errors}]")

        additional_context = " ".join(context_parts) if context_parts else None

        logger.info(
            "Context injection for session %s: %s",
            input.session_id,
            additional_context or "no context injected",
        )

        return HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning="Context injection handler",
            category="context-injection",
            system_message=additional_context,
        )


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

async def _get_git_branch(working_dir: str) -> str | None:
    """Run ``git rev-parse --abbrev-ref HEAD`` and return the branch name."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0 and stdout:
            return stdout.decode().strip()
    except (OSError, asyncio.TimeoutError) as exc:
        logger.debug("Failed to get git branch for %s: %s", working_dir, exc)
    return None


def _extract_recent_errors(session_context: str) -> str | None:
    """Pull the last 3 lines containing 'error' or 'failed' from the session context."""
    error_lines: list[str] = []
    for line in session_context.splitlines():
        lower = line.lower()
        if "error" in lower or "failed" in lower:
            error_lines.append(line.strip())
    # Take only the last 3
    recent = error_lines[-3:]
    return "; ".join(recent) if recent else None


def _get_config_bool(config: HandlerConfig, key: str, *, default: bool = False) -> bool:
    """Read a boolean flag from the handler's extra config dict."""
    value = config.config.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return default
