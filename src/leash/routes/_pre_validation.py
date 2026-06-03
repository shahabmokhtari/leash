"""Shared pre-validation helper for hook endpoints.

Called by both claude_hook.py and copilot_hook.py to run a lightweight
pre-validation script before the LLM analysis. Returns a JSONResponse
to short-circuit, or None to proceed to the LLM handler.
"""

from __future__ import annotations

import json
import logging
from os.path import basename
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def _get_response_harness(harness_client: Any, provider: str) -> Any:
    """Use the registered harness when available, otherwise fall back safely."""
    if harness_client is not None:
        return harness_client

    if provider == "copilot":
        from leash.services.harness.copilot import CopilotHarnessClient

        return CopilotHarnessClient()

    from leash.services.harness.claude import ClaudeHarnessClient

    return ClaudeHarnessClient()


async def run_pre_validation(
    pre_validation_svc: Any,
    hook_input: Any,
    handler: Any,
    harness_client: Any,
    session_manager: Any,
    trigger_service: Any,
    console_status: Any,
    adaptive_service: Any,
    mode: str,
    event: str,
) -> JSONResponse | None:
    """Run the pre-validation script for a handler, if configured.

    Returns a JSONResponse to short-circuit the request, or None to
    proceed to the normal LLM handler pipeline.
    """
    script_name = getattr(handler, "pre_validation_script", None)
    if not script_name:
        return None

    # Build context for the script
    hook_event_name = getattr(hook_input, "hook_event_name", "")
    tool_name = getattr(hook_input, "tool_name", None) or ""
    tool_input = getattr(hook_input, "tool_input", None) or {}
    cwd = getattr(hook_input, "cwd", None) or ""
    session_id = getattr(hook_input, "session_id", "")
    provider = getattr(hook_input, "provider", "claude")

    context = {
        "hookEvent": hook_event_name,
        "toolName": tool_name,
        "toolInput": tool_input,
        "cwd": cwd,
        "provider": provider,
        "sessionId": session_id,
        "handlerName": getattr(handler, "name", ""),
        "threshold": getattr(handler, "threshold", 85),
    }

    try:
        result = await pre_validation_svc.run(script_name, context)
    except Exception:
        logger.debug("Pre-validation script %s failed, proceeding to LLM", script_name, exc_info=True)
        return None

    if result.decision == "approve":
        logger.info("Pre-validation approved %s/%s: %s", hook_event_name, tool_name, result.reason)
        return await _build_response(
            decision="script-approved",
            approve=True,
            reason=result.reason,
            hook_input=hook_input,
            handler=handler,
            harness_client=harness_client,
            session_manager=session_manager,
            trigger_service=trigger_service,
            console_status=console_status,
            adaptive_service=adaptive_service,
            mode=mode,
            event=event,
        )

    if result.decision == "deny":
        logger.info("Pre-validation denied %s/%s: %s", hook_event_name, tool_name, result.reason)
        return await _build_response(
            decision="script-denied",
            approve=False,
            reason=result.reason,
            hook_input=hook_input,
            handler=handler,
            harness_client=harness_client,
            session_manager=session_manager,
            trigger_service=trigger_service,
            console_status=console_status,
            adaptive_service=adaptive_service,
            mode=mode,
            event=event,
        )

    # passthrough or unknown → proceed to LLM
    logger.debug("Pre-validation passthrough for %s/%s: %s", hook_event_name, tool_name, result.reason)
    return None


async def _build_response(
    *,
    decision: str,
    approve: bool,
    reason: str,
    hook_input: Any,
    handler: Any,
    harness_client: Any,
    session_manager: Any,
    trigger_service: Any,
    console_status: Any,
    adaptive_service: Any,
    mode: str,
    event: str,
) -> JSONResponse | None:
    """Build a response and log the pre-validation decision."""
    from leash.models.session_data import SessionEvent

    _NO_OPINION = JSONResponse(content={})

    hook_event_name = getattr(hook_input, "hook_event_name", "")
    tool_name = getattr(hook_input, "tool_name", None)
    session_id = getattr(hook_input, "session_id", "")
    provider = getattr(hook_input, "provider", "claude")

    # Determine the effective decision and response BEFORE logging,
    # so side-effects (session history, triggers, counters) reflect
    # what actually happened — not what the script wanted.
    effective_decision = decision
    respond_no_opinion = False
    fall_through_to_llm = False

    if mode == "observe":
        # Observe mode: never enforce — rewrite to "logged"
        effective_decision = "logged"
        respond_no_opinion = True
    elif approve and not getattr(handler, "auto_approve", True):
        # Lockdown profile: handler.auto_approve is False — block script approvals.
        # Return None to fall through to LLM analysis + tray for human approval.
        effective_decision = "script-approved-blocked"
        respond_no_opinion = True
        fall_through_to_llm = True
    elif mode == "approve-only" and not approve:
        # Approve-only mode: never auto-deny — rewrite to logged
        effective_decision = "logged"
        respond_no_opinion = True

    # Log the event with the effective decision.
    # When falling through to LLM (lockdown), log a minimal session event
    # so the audit trail records the blocked script approval even if the
    # downstream LLM handler throws.  Skip counters (console_status,
    # adaptive_service) to avoid double-counting with _try_log_event.
    try:
        prompt_tpl = getattr(handler, "prompt_template", None)
        evt = SessionEvent(
            type=hook_event_name,
            tool_name=tool_name,
            tool_input=getattr(hook_input, "tool_input", None),
            decision=effective_decision,
            reasoning=reason,
            handler_name=getattr(handler, "name", None),
            prompt_template=basename(prompt_tpl) if prompt_tpl else None,
            threshold=getattr(handler, "threshold", None),
            provider=getattr(hook_input, "provider", "claude"),
            elapsed_ms=0,
        )

        if session_manager is not None:
            await session_manager.record_event(session_id, evt)

        if not fall_through_to_llm:
            if trigger_service is not None:
                try:
                    trigger_service.fire(effective_decision, None, evt)
                except Exception:
                    pass

            if console_status is not None:
                try:
                    console_status.record_event(effective_decision, tool_name, None, 0)
                except Exception:
                    pass

            # Skip adaptive_service for pre-validation decisions — synthetic
            # scores (0/100) would corrupt the adaptive threshold model that
            # is trained on real LLM analysis scores.
    except Exception:
        logger.debug("Failed to log pre-validation event", exc_info=True)

    if respond_no_opinion:
        if fall_through_to_llm:
            # Lockdown: script approval blocked — return None so the request
            # falls through to LLM analysis and tray for human approval.
            return None
        return _NO_OPINION

    response_harness = _get_response_harness(harness_client, provider)

    # Build the actual response
    if approve:
        from leash.models.hook_output import HookOutput

        output = HookOutput(
            auto_approve=True,
            safety_score=100,
            reasoning=reason,
            category="safe",
        )
        return JSONResponse(content=response_harness.format_response(event, output))

    if not approve:
        from leash.models.hook_output import HookOutput

        output = HookOutput(
            auto_approve=False,
            safety_score=0,
            reasoning=reason,
            category="dangerous",
            threshold=getattr(handler, "threshold", 85),
        )
        return JSONResponse(content=response_harness.format_response(event, output))

    return _NO_OPINION
