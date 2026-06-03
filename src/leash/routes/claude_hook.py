"""Main Claude Code hook endpoint — POST /api/hooks/claude."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from os.path import basename
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from leash.routes._pre_validation import run_pre_validation as _run_pre_validation
from leash.routes._tray_helpers import make_tray_decision as _make_tray_decision
from leash.security.input_sanitizer import InputSanitizer

if TYPE_CHECKING:
    from leash.services.tray.base import NotificationService, TrayService
    from leash.services.tray.pending_decision import PendingDecisionService

logger = logging.getLogger(__name__)
router = APIRouter()

# Non-actionable tools that should always pass through without analysis
PASSTHROUGH_TOOLS: set[str] = {
    "AskUserQuestion",
    "AskFollowupQuestion",
    "AttemptCompletion",
    "ReadNotebook",
    "ListNotebooks",
}

# Empty JSON = no opinion; Claude asks the user as normal
_NO_OPINION = JSONResponse(content={})


def _is_decision_event(hook_event_name: str) -> bool:
    return hook_event_name in {"PermissionRequest", "PreToolUse"}


def _get_harness_client(request: Request) -> Any:
    registry = getattr(request.app.state, "harness_client_registry", None)
    if registry is not None:
        return registry.get("claude")
    return getattr(request.app.state, "claude_harness_client", None)


def _get_config_manager(request: Request) -> Any:
    return getattr(request.app.state, "config_manager", None)


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


def _get_handler_factory(request: Request) -> Any:
    return getattr(request.app.state, "handler_factory", None)


def _get_enforcement_service(request: Request) -> Any:
    return getattr(request.app.state, "enforcement_service", None)


def _get_profile_service(request: Request) -> Any:
    return getattr(request.app.state, "profile_service", None)


def _get_adaptive_service(request: Request) -> Any:
    return getattr(request.app.state, "adaptive_threshold_service", None)


def _get_trigger_service(request: Request) -> Any:
    return getattr(request.app.state, "trigger_service", None)


def _get_console_status(request: Request) -> Any:
    return getattr(request.app.state, "console_status_service", None)


def _get_pre_validation_service(request: Request) -> Any:
    return getattr(request.app.state, "pre_validation_service", None)


def _get_tray_service(request: Request) -> TrayService | None:
    return getattr(request.app.state, "tray_service", None)


def _get_notification_service(request: Request) -> NotificationService | None:
    return getattr(request.app.state, "notification_service", None)


def _get_pending_decision_service(request: Request) -> PendingDecisionService | None:
    return getattr(request.app.state, "pending_decision_service", None)


def _truncate(s: str | None, max_len: int = 200) -> str:
    if not s:
        return ""
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


async def _try_log_event(
    session_manager: Any,
    harness_client: Any,
    trigger_service: Any,
    console_status: Any,
    adaptive_service: Any,
    hook_input: Any,
    output: Any | None,
    handler: Any | None,
    llm_provider: str | None = None,
) -> None:
    """Log a hook event to the session manager. Non-fatal on error."""
    try:
        from leash.models.session_data import SessionEvent

        if output is None:
            decision = "logged"
        elif getattr(output, "tray_decision", None):
            decision = output.tray_decision
        elif getattr(output, "auto_approve", False):
            decision = "auto-approved"
        else:
            decision = "denied"

        response_json: str | None = None
        if output is not None and harness_client is not None:
            try:
                client_response = harness_client.format_response(
                    getattr(hook_input, "hook_event_name", ""), output
                )
                response_json = json.dumps(client_response, indent=2)
            except Exception:
                pass

        prompt_tpl = getattr(handler, "prompt_template", None) if handler else None
        evt = SessionEvent(
            type=getattr(hook_input, "hook_event_name", ""),
            tool_name=getattr(hook_input, "tool_name", None),
            tool_input=getattr(hook_input, "tool_input", None),
            decision=decision,
            safety_score=getattr(output, "safety_score", None) if output else None,
            reasoning=getattr(output, "reasoning", None) if output else None,
            category=getattr(output, "category", None) if output else None,
            handler_name=getattr(handler, "name", None) if handler else None,
            prompt_template=basename(prompt_tpl) if prompt_tpl else None,
            threshold=getattr(output, "threshold", None) or (getattr(handler, "threshold", None) if handler else None),
            provider=getattr(hook_input, "provider", "claude"),
            llm_provider=llm_provider,
            elapsed_ms=getattr(output, "elapsed_ms", None) if output else None,
            response_json=response_json,
        )

        if session_manager is not None:
            session_id = getattr(hook_input, "session_id", "")
            await session_manager.record_event(session_id, evt)

        # Fire trigger webhooks (fire-and-forget)
        if trigger_service is not None:
            try:
                trigger_service.fire(decision, getattr(output, "category", None) if output else None, evt)
            except Exception:
                pass

        # Update console status
        if console_status is not None:
            try:
                console_status.record_event(
                    decision,
                    getattr(hook_input, "tool_name", None),
                    getattr(output, "safety_score", None) if output else None,
                    getattr(output, "elapsed_ms", None) if output else None,
                )
            except Exception:
                pass

        # Record to adaptive threshold service
        if output is not None and adaptive_service is not None:
            tool_name = getattr(hook_input, "tool_name", None)
            if tool_name:
                try:
                    await adaptive_service.record_decision(
                        tool_name, getattr(output, "safety_score", 0), decision
                    )
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Failed to log event: %s", exc)


@router.post("/api/hooks/claude")
async def handle_claude_hook(
    request: Request,
    event: str = Query(..., description="Hook event type"),
) -> JSONResponse:
    """Main Claude hook endpoint. Returns Claude-formatted JSON on stdout."""
    if not event or not event.strip():
        return JSONResponse(status_code=400, content={"error": "event query parameter is required"})

    harness_client = _get_harness_client(request)
    config_manager = _get_config_manager(request)
    session_manager = _get_session_manager(request)
    handler_factory = _get_handler_factory(request)
    enforcement_svc = _get_enforcement_service(request)
    profile_svc = _get_profile_service(request)
    adaptive_svc = _get_adaptive_service(request)
    trigger_svc = _get_trigger_service(request)
    console_status = _get_console_status(request)

    # Read raw body
    try:
        raw_body = await request.body()
        raw_json = json.loads(raw_body) if raw_body else {}
    except Exception:
        return _NO_OPINION

    # Map via harness client
    try:
        if harness_client is not None:
            hook_input = harness_client.map_input(raw_json, event)
        else:
            # Fallback: build HookInput from raw JSON
            from leash.models.hook_input import HookInput

            hook_input = HookInput(
                hook_event_name=event,
                session_id=raw_json.get("sessionId", raw_json.get("session_id", "")),
                tool_name=raw_json.get("toolName", raw_json.get("tool_name")),
                tool_input=raw_json.get("toolInput", raw_json.get("tool_input")),
                cwd=raw_json.get("cwd"),
                provider="claude",
            )
    except Exception:
        return _NO_OPINION

    session_id = getattr(hook_input, "session_id", "")
    if not session_id or not session_id.strip():
        logger.warning("Claude hook missing sessionId")
        return _NO_OPINION

    # Resolve symlinks in paths so the LLM sees consistent real paths
    # (e.g. C:\r\project and C:\Users\...\project both resolve to the same path).
    # Uses a thread to avoid blocking the event loop on slow filesystems.
    if config_manager is not None:
        try:
            if config_manager.get_configuration().resolve_hook_symlinks:
                await asyncio.to_thread(hook_input.resolve_symlinks)
        except Exception:
            pass

    # Validate inputs
    if (
        not InputSanitizer.is_valid_session_id(session_id)
        or not InputSanitizer.is_valid_hook_event_name(getattr(hook_input, "hook_event_name", ""))
        or not InputSanitizer.is_valid_tool_name(getattr(hook_input, "tool_name", None))
    ):
        return _NO_OPINION

    try:
        logger.debug("Claude hook %s for %s", event, getattr(hook_input, "tool_name", "unknown"))

        # Get config and enforcement mode
        app_config = None
        if config_manager is not None:
            app_config = config_manager.get_configuration()

        llm_provider_name = getattr(app_config.llm, "provider", None) if app_config else None

        mode = "observe"
        if enforcement_svc is not None:
            mode = getattr(enforcement_svc, "mode", "observe")

        # Find matching handler
        handler = None
        if config_manager is not None:
            client_name = getattr(harness_client, "name", "claude") if harness_client else "claude"
            handler = config_manager.find_matching_handler(
                getattr(hook_input, "hook_event_name", ""),
                getattr(hook_input, "tool_name", None),
                provider=client_name,
            )

        if handler is None or getattr(handler, "mode", "log-only") == "log-only":
            await _try_log_event(
                session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                hook_input, None, handler, llm_provider=llm_provider_name,
            )
            return _NO_OPINION

        # Passthrough tools always return no opinion
        tool_name = getattr(hook_input, "tool_name", None) or ""
        if harness_client is not None and hasattr(harness_client, "is_passthrough_tool"):
            if harness_client.is_passthrough_tool(tool_name):
                logger.debug("Passthrough tool %s - skipping analysis", tool_name)
                await _try_log_event(
                    session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                    hook_input, None, handler,
                )
                return _NO_OPINION
        elif tool_name in PASSTHROUGH_TOOLS:
            logger.debug("Passthrough tool %s - skipping analysis", tool_name)
            await _try_log_event(
                session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                hook_input, None, handler, llm_provider=llm_provider_name,
            )
            return _NO_OPINION

        analyze_in_observe = getattr(app_config, "analyze_in_observe_mode", True) if app_config else True
        handler_mode = getattr(handler, "mode", "log-only") if handler is not None else "log-only"
        if mode == "observe" and not analyze_in_observe and handler_mode in {"llm-analysis", "llm-validation"}:
            logger.debug("Skipping LLM analysis for %s/%s: observe mode with analyzeInObserveMode=false", event, tool_name)
            await _try_log_event(
                session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                hook_input, None, handler, llm_provider=llm_provider_name,
            )
            return _NO_OPINION

        # Build session context
        context: str | None = None
        if session_manager is not None:
            try:
                context = await session_manager.build_context(session_id)
            except Exception:
                pass

        # Apply profile-based threshold (copy to avoid mutating shared config)
        if profile_svc is not None:
            active_profile = profile_svc.get_active_profile_key()
            handler = copy.copy(handler)
            handler.threshold = handler.get_threshold_for_profile(active_profile)
            if active_profile == "lockdown":
                handler.auto_approve = False

        # Run pre-validation script if configured (before LLM call)
        pre_validation_svc = _get_pre_validation_service(request)
        if pre_validation_svc and handler and getattr(handler, "pre_validation_script", None):
            pv_result = await _run_pre_validation(
                pre_validation_svc, hook_input, handler, harness_client,
                session_manager, trigger_svc, console_status, adaptive_svc, mode, event,
            )
            if pv_result is not None:
                return pv_result

        # Create and execute handler
        output = None
        session_entry = None
        shared_client_ref = None
        if handler_factory is not None:
            try:
                handler_instance, session_entry = await handler_factory.create(
                    getattr(handler, "mode", ""),
                    getattr(handler, "prompt_template", None),
                    session_id,
                )
                output = await handler_instance.handle(hook_input, handler, context)
            except Exception as exc:
                logger.error("Claude handler execution failed for %s/%s: %s", event, tool_name, exc, exc_info=True)
                return _NO_OPINION
            finally:
                # Release the per-session client so it can be evicted/cleaned up.
                # Shield from CancelledError to prevent in_use counter leaks
                # when the request task is cancelled (e.g., client disconnect).
                llm_provider = getattr(request.app.state, "llm_client_provider", None)
                if llm_provider is not None:
                    try:
                        await asyncio.shield(llm_provider.release_session(session_entry))
                    except asyncio.CancelledError:
                        pass

        if output is None:
            await _try_log_event(
                session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                hook_input, None, handler, llm_provider=llm_provider_name,
            )
            return _NO_OPINION

        hook_event_name = getattr(hook_input, "hook_event_name", "")
        if not _is_decision_event(hook_event_name):
            await _try_log_event(
                session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
                hook_input, output, handler, llm_provider=llm_provider_name,
            )
            if harness_client is not None:
                return JSONResponse(content=harness_client.format_response(event, output))
            return _NO_OPINION

        # Decision logic based on enforcement mode + tray integration
        # (tray may override output.auto_approve)
        response = await _make_tray_decision(
            mode=mode, output=output, harness_client=harness_client,
            event=event, tool_name=tool_name,
            notification_svc=_get_notification_service(request),
            pending_decision_svc=_get_pending_decision_service(request),
            tray_config=getattr(app_config, "tray", None) if app_config else None,
            provider="claude",
            cwd=getattr(hook_input, "cwd", None),
        )

        # Observe mode: always return _NO_OPINION regardless of tray result.
        # LLM analysis ran above for logging, but we never approve/deny.
        # Mark as "logged" so the log entry doesn't say "denied".
        if mode == "observe":
            response = _NO_OPINION
            if output is not None:
                output.tray_decision = "logged"

        # Log after tray decision so the log reflects any user override
        await _try_log_event(
            session_manager, harness_client, trigger_svc, console_status, adaptive_svc,
            hook_input, output, handler, llm_provider=llm_provider_name,
        )

        return response

    except Exception as exc:
        logger.error("Error processing Claude hook for %s: %s", event, exc)
        return _NO_OPINION
