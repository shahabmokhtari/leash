"""Configuration CRUD endpoints — GET/PUT /api/config."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from leash.exceptions import ConfigurationException

logger = logging.getLogger(__name__)
router = APIRouter()

# -- Model lists per provider ------------------------------------------------

# Claude providers use shorthand aliases (Claude CLI resolves them internally).
_CLAUDE_MODELS = [
    {"value": "", "label": "(Default — CLI selects)"},
    {"value": "opus", "label": "Opus (Claude Opus)"},
    {"value": "sonnet", "label": "Sonnet (Claude Sonnet)"},
    {"value": "haiku", "label": "Haiku (Claude Haiku)"},
]

# Fallback Copilot model list used when `copilot help config` is unavailable.
_COPILOT_MODELS_FALLBACK = [
    {"value": "", "label": "(Default — Copilot selects)"},
    {"value": "claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
    {"value": "claude-sonnet-4.5", "label": "Claude Sonnet 4.5"},
    {"value": "claude-opus-4.6", "label": "Claude Opus 4.6"},
    {"value": "claude-opus-4.5", "label": "Claude Opus 4.5"},
    {"value": "claude-haiku-4.5", "label": "Claude Haiku 4.5"},
    {"value": "gpt-5.4", "label": "GPT 5.4"},
    {"value": "gpt-5.3-codex", "label": "GPT 5.3 Codex"},
    {"value": "gpt-5.2-codex", "label": "GPT 5.2 Codex"},
    {"value": "gpt-5.1-codex", "label": "GPT 5.1 Codex"},
    {"value": "gpt-5.1", "label": "GPT 5.1"},
    {"value": "gpt-4.1", "label": "GPT 4.1"},
    {"value": "gemini-3-pro-preview", "label": "Gemini 3 Pro (Preview)"},
]

# Cache for dynamically fetched copilot models
_copilot_models_cache: list[dict[str, str]] | None = None
_copilot_models_cache_expires_at: float = 0.0
_copilot_models_negative_until: float = 0.0
_COPILOT_MODELS_POSITIVE_TTL = 3600  # refresh successful discovery hourly
_COPILOT_MODELS_NEGATIVE_TTL = 300  # 5 minutes before retrying failed discovery
_copilot_models_lock: asyncio.Lock | None = None


def _get_models_lock() -> asyncio.Lock:
    """Lazily create the models lock (must be called within a running event loop)."""
    global _copilot_models_lock  # noqa: PLW0603
    if _copilot_models_lock is None:
        _copilot_models_lock = asyncio.Lock()
    return _copilot_models_lock


async def _fetch_copilot_models() -> list[dict[str, str]]:
    """Parse available models from ``copilot help config`` output.

    Returns a list of {"value": ..., "label": ...} dicts.
    Caches successful results for ``_COPILOT_MODELS_POSITIVE_TTL`` seconds.
    Failed discovery is cached for ``_COPILOT_MODELS_NEGATIVE_TTL`` seconds
    to avoid repeated slow subprocess spawns.
    Uses a lock to prevent duplicate subprocess spawns on concurrent requests.
    """
    global _copilot_models_cache, _copilot_models_cache_expires_at, _copilot_models_negative_until
    now = time.monotonic()
    if _copilot_models_cache is not None and now < _copilot_models_cache_expires_at:
        return _copilot_models_cache
    if _copilot_models_cache is not None and now >= _copilot_models_cache_expires_at:
        _copilot_models_cache = None

    # Check negative cache — avoid retrying too soon after a failure
    if now < _copilot_models_negative_until:
        return _COPILOT_MODELS_FALLBACK

    async with _get_models_lock():
        # Re-check after acquiring lock (another coroutine may have populated it)
        now = time.monotonic()
        if _copilot_models_cache is not None and now < _copilot_models_cache_expires_at:
            return _copilot_models_cache
        if _copilot_models_cache is not None and now >= _copilot_models_cache_expires_at:
            _copilot_models_cache = None
        if now < _copilot_models_negative_until:
            return _COPILOT_MODELS_FALLBACK

        copilot_cmd = shutil.which("copilot")
        if copilot_cmd is None:
            copilot_cmd = shutil.which("gh")
        if copilot_cmd is None:
            logger.debug("Neither 'copilot' nor 'gh' found in PATH, using fallback model list")
            _copilot_models_negative_until = time.monotonic() + _COPILOT_MODELS_NEGATIVE_TTL
            return _COPILOT_MODELS_FALLBACK

        try:
            args = ["help", "config"]
            if copilot_cmd.endswith(("gh", "gh.exe", "gh.cmd")):
                args = ["copilot", *args]

            cmd = copilot_cmd
            # On Windows, .cmd files need cmd.exe wrapper
            if sys.platform == "win32" and copilot_cmd.lower().endswith((".cmd", ".bat")):
                cmd = "cmd"
                args = ["/c", copilot_cmd, *args]

            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            except asyncio.TimeoutError:
                logger.debug("copilot model discovery timed out, killing subprocess")
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
                _copilot_models_negative_until = time.monotonic() + _COPILOT_MODELS_NEGATIVE_TTL
                return _COPILOT_MODELS_FALLBACK
            except BaseException:
                # CancelledError is a BaseException — ensure subprocess is
                # killed to prevent leaked processes and blocked pipes.
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception:
                    pass
                raise
            output = stdout.decode("utf-8", errors="replace")

            # Parse the model list from the `model` config section.
            # Format varies: `    - "model-name"` or `    - model-name` (with or without quotes)
            models: list[dict[str, str]] = [{"value": "", "label": "(Default — Copilot selects)"}]
            in_model_section = False
            model_re = re.compile(r'^\s+-\s+"?([^"\s]+)"?')
            for line in output.splitlines():
                if "`model`:" in line or '"model":' in line:
                    in_model_section = True
                    continue
                if in_model_section:
                    m = model_re.match(line)
                    if m:
                        model_id = m.group(1)
                        # Build a human-friendly label
                        label = model_id.replace("-", " ").title()
                        models.append({"value": model_id, "label": label})
                    elif line.strip() and not line.strip().startswith("-"):
                        break  # End of model list

            if len(models) > 1:
                _copilot_models_cache = models
                _copilot_models_cache_expires_at = time.monotonic() + _COPILOT_MODELS_POSITIVE_TTL
                return models
        except Exception:
            logger.debug("Failed to fetch copilot models dynamically", exc_info=True)

        _copilot_models_negative_until = time.monotonic() + _COPILOT_MODELS_NEGATIVE_TTL
        return _COPILOT_MODELS_FALLBACK


# Effort levels — Claude supports low/medium/high, Copilot adds xhigh
_EFFORT_LEVELS = [
    {"value": "", "label": "(Default)"},
    {"value": "low", "label": "Low"},
    {"value": "medium", "label": "Medium"},
    {"value": "high", "label": "High"},
]

_EFFORT_LEVELS_WITH_XHIGH = [
    *_EFFORT_LEVELS,
    {"value": "xhigh", "label": "Extra High"},
]

# Which providers support effort level
_EFFORT_SUPPORTED = frozenset({
    "claude-cli", "claude-persistent", "claude-stream",
    "copilot-cli", "copilot-persistent",
})


def _get_config_manager(request: Request) -> Any:
    return getattr(request.app.state, "config_manager", None)


def _get_hook_installer(request: Request) -> Any:
    return getattr(request.app.state, "hook_installer", None)


def _get_copilot_hook_installer(request: Request) -> Any:
    return getattr(request.app.state, "copilot_hook_installer", None)


@router.get("/api/config")
async def get_config(request: Request) -> JSONResponse:
    """Return the current configuration."""
    config_manager = _get_config_manager(request)
    if config_manager is None:
        return JSONResponse(status_code=503, content={"error": "Configuration manager not available"})

    try:
        config = await config_manager.load()
        return JSONResponse(content=config.model_dump(by_alias=True))
    except Exception as exc:
        logger.error("Failed to load configuration: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load configuration"})


@router.put("/api/config")
async def update_config(request: Request) -> JSONResponse:
    """Update the configuration. Auto-reinstalls hooks after save."""
    config_manager = _get_config_manager(request)
    if config_manager is None:
        return JSONResponse(status_code=503, content={"error": "Configuration manager not available"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    if not body:
        return JSONResponse(status_code=400, content={"error": "Configuration body is required"})

    try:
        from leash.models.configuration import Configuration

        config = Configuration.model_validate(body)
        await config_manager.update(config)
        logger.info("Configuration updated via API")

        # Re-sync hooks after config change (respects user's uninstall decision)
        if not config.hooks_user_uninstalled:
            hook_installer = _get_hook_installer(request)
            if hook_installer is not None:
                try:
                    hook_installer.install()
                except Exception as hook_exc:
                    logger.warning("Failed to reinstall Claude hooks after config update: %s", hook_exc)

        # Resync managed user-level Copilot hooks if they are currently
        # installed. The generated scripts embed enabled events and the
        # callback URL, so leaving them stale after a config update is wrong.
        if not config.copilot_hooks_user_uninstalled:
            copilot_hook_installer = getattr(request.app.state, "copilot_hook_installer", None)
            if copilot_hook_installer is not None:
                try:
                    if copilot_hook_installer.is_user_installed():
                        copilot_hook_installer.install_user()
                except Exception as hook_exc:
                    logger.warning("Failed to resync Copilot hooks after config update: %s", hook_exc)

        # Sync resolve_symlinks setting to transcript watcher
        tw = getattr(request.app.state, "transcript_watcher", None)
        if tw:
            try:
                tw.set_resolve_symlinks(config.resolve_symlinks)
            except Exception as symlink_exc:
                logger.warning("Failed to sync resolve_symlinks to transcript watcher: %s", symlink_exc)

        return JSONResponse(content={"message": "Configuration updated successfully"})
    except ConfigurationException as exc:
        logger.error("Failed to save configuration: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception as exc:
        logger.error("Unexpected error updating configuration: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to update configuration"})


@router.get("/api/config/analyze-in-observe")
async def get_analyze_in_observe(request: Request) -> JSONResponse:
    """Return whether LLM analysis is enabled in observe mode."""
    config_manager = _get_config_manager(request)
    if config_manager is None:
        return JSONResponse(status_code=503, content={"error": "Configuration manager not available"})
    config = config_manager.get_configuration()
    return JSONResponse(content={"analyzeInObserveMode": config.analyze_in_observe_mode})


@router.put("/api/config/analyze-in-observe")
async def set_analyze_in_observe(request: Request) -> JSONResponse:
    """Toggle LLM analysis in observe mode."""
    config_manager = _get_config_manager(request)
    if config_manager is None:
        return JSONResponse(status_code=503, content={"error": "Configuration manager not available"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    enabled = body.get("analyzeInObserveMode")
    if not isinstance(enabled, bool):
        return JSONResponse(status_code=400, content={"error": "analyzeInObserveMode must be a boolean"})

    try:
        config = config_manager.get_configuration()
        config.analyze_in_observe_mode = enabled
        await config_manager.update(config)
        return JSONResponse(content={"analyzeInObserveMode": enabled})
    except Exception as exc:
        logger.error("Failed to update analyze_in_observe_mode: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to update setting"})


@router.get("/api/config/handlers/{hook_event_name}")
async def get_handlers(request: Request, hook_event_name: str) -> JSONResponse:
    """Return handlers for a specific hook event."""
    if not hook_event_name or not hook_event_name.strip():
        return JSONResponse(status_code=400, content={"error": "Hook event name is required"})

    config_manager = _get_config_manager(request)
    if config_manager is None:
        return JSONResponse(status_code=503, content={"error": "Configuration manager not available"})

    handlers = config_manager.get_handlers_for_hook(hook_event_name)
    return JSONResponse(content=[h.model_dump(by_alias=True) for h in handlers])


@router.get("/api/config/provider-models")
async def get_provider_models() -> JSONResponse:
    """Return available models per provider and provider capabilities.

    Used by the config UI to populate model dropdowns and show/hide
    effort level controls.
    """
    copilot_models = await _fetch_copilot_models()

    return JSONResponse(content={
        "models": {
            "anthropic-api": _CLAUDE_MODELS,
            "claude-cli": _CLAUDE_MODELS,
            "claude-persistent": _CLAUDE_MODELS,
            "claude-stream": _CLAUDE_MODELS,
            "copilot-cli": copilot_models,
            "copilot-persistent": copilot_models,
            "generic-rest": [{"value": "", "label": "(Custom — set in body template)"}],
        },
        "effortLevels": {
            "claude-cli": _EFFORT_LEVELS,
            "claude-persistent": _EFFORT_LEVELS,
            "claude-stream": _EFFORT_LEVELS,
            "copilot-cli": _EFFORT_LEVELS_WITH_XHIGH,
            "copilot-persistent": _EFFORT_LEVELS_WITH_XHIGH,
        },
        "effortSupported": sorted(_EFFORT_SUPPORTED),
    })
