"""Hook management endpoints — install, uninstall, enforce, status."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_ENFORCEMENT_MODES = {"observe", "approve-only", "enforce"}


def _get_hook_installer(request: Request) -> Any:
    return getattr(request.app.state, "hook_installer", None)


def _get_copilot_hook_installer(request: Request) -> Any:
    return getattr(request.app.state, "copilot_hook_installer", None)


def _get_enforcement_service(request: Request) -> Any:
    return getattr(request.app.state, "enforcement_service", None)


def _persist_session_start_metadata(request: Request) -> str | None:
    """Persist launch metadata used by SessionStart bootstrap hooks."""
    from leash.session_start_hook import persist_launch_metadata

    try:
        config_mgr = getattr(request.app.state, "config_manager", None)
        bind_host = getattr(request.app.state, "cli_host", None)
        bind_port = getattr(request.app.state, "cli_port", None)

        if config_mgr is not None:
            config = config_mgr.get_configuration()
            if bind_host is None:
                bind_host = getattr(getattr(config, "server", None), "host", None)
            if bind_port is None:
                bind_port = getattr(getattr(config, "server", None), "port", None)

        bind_host = bind_host or "localhost"
        bind_port = bind_port or 5050
        config_path = getattr(request.app.state, "config_path", None)
        persist_launch_metadata(bind_host, int(bind_port), config_path=config_path)
        return None
    except Exception as exc:
        logger.error("Failed to persist SessionStart launch metadata: %s", exc)
        return f"Launch metadata: {exc}"


def _get_session_start_states(hook_installer: Any, copilot_installer: Any) -> tuple[bool, bool]:
    """Best-effort readback of the real SessionStart install state."""
    claude_installed = False
    copilot_installed = False

    if hook_installer is not None:
        try:
            claude_installed = bool(hook_installer.is_session_start_installed())
        except Exception:
            logger.warning("Failed to read Claude SessionStart state", exc_info=True)

    if copilot_installer is not None:
        try:
            copilot_installed = bool(copilot_installer.is_session_start_installed())
        except Exception:
            logger.warning("Failed to read Copilot SessionStart state", exc_info=True)

    return claude_installed, copilot_installed


@router.get("/api/hooks/status")
async def get_status(request: Request) -> JSONResponse:
    """Return hook installation and enforcement status."""
    hook_installer = _get_hook_installer(request)
    copilot_installer = _get_copilot_hook_installer(request)
    enforcement_svc = _get_enforcement_service(request)

    installed = False
    if hook_installer is not None:
        try:
            installed = hook_installer.is_installed()
        except Exception:
            logger.warning("Failed to check hook installation status", exc_info=True)

    enforced = False
    enforcement_mode = "observe"
    if enforcement_svc is not None:
        enforced = getattr(enforcement_svc, "is_enforced", False)
        enforcement_mode = getattr(enforcement_svc, "mode", "observe")

    copilot_user_installed = False
    if copilot_installer is not None:
        try:
            copilot_user_installed = copilot_installer.is_user_installed()
        except Exception:
            logger.warning("Failed to check copilot installation status", exc_info=True)

    hooks_user_uninstalled = False
    copilot_hooks_user_uninstalled = False
    config_mgr = getattr(request.app.state, "config_manager", None)
    if config_mgr is not None:
        try:
            app_config = config_mgr.get_configuration()
            hooks_user_uninstalled = app_config.hooks_user_uninstalled
            copilot_hooks_user_uninstalled = app_config.copilot_hooks_user_uninstalled
        except Exception:
            logger.warning("Failed to read config for hook status", exc_info=True)

    return JSONResponse(
        content={
            "installed": installed,
            "enforced": enforced,
            "enforcementMode": enforcement_mode,
            "hooksUserUninstalled": hooks_user_uninstalled,
            "copilot": {
                "userInstalled": copilot_user_installed,
                "hooksUserUninstalled": copilot_hooks_user_uninstalled,
            },
        }
    )


@router.post("/api/hooks/enforce")
async def toggle_enforcement(
    request: Request,
    mode: str | None = Query(default=None, description="Enforcement mode to set"),
) -> JSONResponse:
    """Toggle or set enforcement mode."""
    enforcement_svc = _get_enforcement_service(request)
    if enforcement_svc is None:
        return JSONResponse(status_code=503, content={"error": "Enforcement service not available"})

    if mode is not None and mode.strip():
        if mode not in VALID_ENFORCEMENT_MODES:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid mode: {mode}. Valid: {', '.join(sorted(VALID_ENFORCEMENT_MODES))}"},
            )
        await enforcement_svc.set_mode(mode)
    else:
        await enforcement_svc.cycle_mode()

    current_mode = getattr(enforcement_svc, "mode", "observe")
    logger.info("Enforcement mode set to %s", current_mode)

    messages = {
        "observe": "Observe-only mode - hooks log but do not decide",
        "approve-only": "Approve-only mode - safe requests auto-approved, uncertain ones fall through to user",
        "enforce": "Full enforcement - hooks return approve/deny decisions",
    }
    message = messages.get(current_mode, f"Mode: {current_mode}")

    return JSONResponse(
        content={
            "enforced": getattr(enforcement_svc, "is_enforced", False),
            "enforcementMode": current_mode,
            "message": message,
        }
    )


@router.post("/api/hooks/install")
async def install_hooks(request: Request) -> JSONResponse:
    """Install Claude hooks to settings.json."""
    hook_installer = _get_hook_installer(request)
    if hook_installer is None:
        return JSONResponse(status_code=503, content={"error": "Hook installer not available"})

    try:
        hook_installer.install()

        # Clear the user-uninstalled flag so hooks auto-install on next startup
        config_mgr = getattr(request.app.state, "config_manager", None)
        if config_mgr is not None:
            config = config_mgr.get_configuration()
            if config.hooks_user_uninstalled:
                config.hooks_user_uninstalled = False
                await config_mgr.update(config)

        return JSONResponse(content={"installed": True, "message": "Hooks installed successfully"})
    except Exception as exc:
        logger.error("Failed to install hooks: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to install hooks: {exc}"})


@router.post("/api/hooks/uninstall")
async def uninstall_hooks(request: Request) -> JSONResponse:
    """Remove Claude hooks from settings.json."""
    hook_installer = _get_hook_installer(request)
    if hook_installer is None:
        return JSONResponse(status_code=503, content={"error": "Hook installer not available"})

    try:
        hook_installer.uninstall()

        # Remember the user's decision so hooks stay uninstalled on next startup
        config_mgr = getattr(request.app.state, "config_manager", None)
        if config_mgr is not None:
            config = config_mgr.get_configuration()
            config.hooks_user_uninstalled = True
            await config_mgr.update(config)

        return JSONResponse(content={"installed": False, "message": "Hooks uninstalled successfully"})
    except Exception as exc:
        logger.error("Failed to uninstall hooks: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to uninstall hooks: {exc}"})


@router.get("/api/hooks/session-start/status")
async def session_start_status(request: Request) -> JSONResponse:
    """Check if SessionStart hooks are installed (Claude and Copilot)."""
    hook_installer = _get_hook_installer(request)
    copilot_installer = _get_copilot_hook_installer(request)
    claude_installed, copilot_installed = _get_session_start_states(hook_installer, copilot_installer)

    return JSONResponse(content={
        "installed": claude_installed or copilot_installed,
        "claude": claude_installed,
        "copilot": copilot_installed,
    })


@router.post("/api/hooks/session-start/install")
async def install_session_start(request: Request) -> JSONResponse:
    """Install only the SessionStart hook (Claude and Copilot)."""
    errors: list[str] = []

    metadata_error = _persist_session_start_metadata(request)
    if metadata_error:
        errors.append(metadata_error)

    hook_installer = _get_hook_installer(request)
    if hook_installer is not None:
        try:
            hook_installer.install_session_start_only()
        except Exception as exc:
            logger.error("Failed to install Claude SessionStart hook: %s", exc)
            errors.append(f"Claude: {exc}")

    copilot_installer = _get_copilot_hook_installer(request)
    if copilot_installer is not None:
        try:
            copilot_installer.install_session_start_only()
        except Exception as exc:
            logger.error("Failed to install Copilot SessionStart hook: %s", exc)
            errors.append(f"Copilot: {exc}")

    if hook_installer is None and copilot_installer is None:
        return JSONResponse(status_code=503, content={"error": "No hook installers available"})

    claude_installed, copilot_installed = _get_session_start_states(hook_installer, copilot_installer)
    if errors:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Partial failure: {'; '.join(errors)}",
                "installed": claude_installed or copilot_installed,
                "claude": claude_installed,
                "copilot": copilot_installed,
            },
        )

    return JSONResponse(
        content={
            "installed": claude_installed or copilot_installed,
            "claude": claude_installed,
            "copilot": copilot_installed,
            "message": "SessionStart hook installed for Claude and Copilot (best-effort for Copilot user-level hooks)",
        }
    )


@router.post("/api/hooks/session-start/uninstall")
async def uninstall_session_start(request: Request) -> JSONResponse:
    """Remove only the SessionStart hook (Claude and Copilot)."""
    errors: list[str] = []

    hook_installer = _get_hook_installer(request)
    if hook_installer is not None:
        try:
            hook_installer.uninstall_session_start_only()
        except Exception as exc:
            logger.error("Failed to uninstall Claude SessionStart hook: %s", exc)
            errors.append(f"Claude: {exc}")

    copilot_installer = _get_copilot_hook_installer(request)
    if copilot_installer is not None:
        try:
            copilot_installer.uninstall_session_start_only()
        except Exception as exc:
            logger.error("Failed to uninstall Copilot SessionStart hook: %s", exc)
            errors.append(f"Copilot: {exc}")

    if hook_installer is None and copilot_installer is None:
        return JSONResponse(status_code=503, content={"error": "No hook installers available"})

    claude_installed, copilot_installed = _get_session_start_states(hook_installer, copilot_installer)
    if errors:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Partial failure: {'; '.join(errors)}",
                "installed": claude_installed or copilot_installed,
                "claude": claude_installed,
                "copilot": copilot_installed,
            },
        )

    return JSONResponse(
        content={
            "installed": claude_installed or copilot_installed,
            "claude": claude_installed,
            "copilot": copilot_installed,
            "message": "SessionStart hook removed for Claude and Copilot",
        }
    )


@router.post("/api/hooks/copilot/install")
async def install_copilot_hooks(request: Request) -> JSONResponse:
    """Install Copilot hooks at user level.

    Note: Copilot CLI only reads hooks from ``.github/hooks/`` in each
    repository.  User-level installation (``~/.copilot/hooks/``) is
    provided as a convenience fallback but may not be read by the CLI.
    Prefer ``install_repo(path)`` for effective hook installation.
    """
    copilot_installer = _get_copilot_hook_installer(request)
    if copilot_installer is None:
        return JSONResponse(status_code=503, content={"error": "Copilot hook installer not available"})

    try:
        copilot_installer.install_user()

        # Clear the user-uninstalled flag
        config_mgr = getattr(request.app.state, "config_manager", None)
        if config_mgr is not None:
            config = config_mgr.get_configuration()
            if config.copilot_hooks_user_uninstalled:
                config.copilot_hooks_user_uninstalled = False
                await config_mgr.update(config)

        return JSONResponse(
            content={
                "installed": True,
                "message": "Copilot hooks installed at user level. "
                "Note: Copilot CLI reads hooks from .github/hooks/ in each repo. "
                "Use per-repo installation for guaranteed effectiveness.",
            }
        )
    except Exception as exc:
        logger.error("Failed to install Copilot hooks: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to install Copilot hooks: {exc}"})


@router.post("/api/hooks/copilot/uninstall")
async def uninstall_copilot_hooks(request: Request) -> JSONResponse:
    """Uninstall Copilot hooks from user level."""
    copilot_installer = _get_copilot_hook_installer(request)
    if copilot_installer is None:
        return JSONResponse(status_code=503, content={"error": "Copilot hook installer not available"})

    try:
        copilot_installer.uninstall_user()

        # Remember the user's decision
        config_mgr = getattr(request.app.state, "config_manager", None)
        if config_mgr is not None:
            config = config_mgr.get_configuration()
            config.copilot_hooks_user_uninstalled = True
            await config_mgr.update(config)

        return JSONResponse(
            content={"installed": False, "message": "Copilot hooks uninstalled successfully"}
        )
    except Exception as exc:
        logger.error("Failed to uninstall Copilot hooks: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to uninstall Copilot hooks: {exc}"})
