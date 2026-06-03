"""Shared tray notification helpers for hook endpoints.

Tray behavior by mode:

- **Observe** ("logging only"):
  NEVER approve or deny. Always returns ``_NO_OPINION``.
  If tray is enabled (``tray_config.enabled and tray_config.show_in_observe``),
  shows informational-only alerts via ``show_alert()`` (no buttons).

- **Approve-only**:
  Safe requests (score >= threshold) are approved via ``harness_client``.
  Unsafe requests are NEVER auto-denied.
  If tray is enabled (``tray_config.enabled and tray_config.show_in_approve_only``),
  shows an interactive tray dialog (Approve/Deny/Ignore).
  On timeout the response is ``_NO_OPINION`` (never deny on timeout).

- **Enforce**:
  Safe requests (score >= threshold) are approved.
  Unsafe requests default to DENY.
  If tray is enabled (``tray_config.enabled``), shows an interactive tray
  dialog so the user can override the system deny.
  On timeout the deny is executed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from leash.models.tray_models import NotificationInfo, NotificationLevel, TrayDecision

if TYPE_CHECKING:
    from leash.models.configuration import TrayConfig
    from leash.services.tray.base import NotificationService
    from leash.services.tray.pending_decision import PendingDecisionService

# Empty JSON = no opinion; the AI assistant asks the user as normal
_NO_OPINION = JSONResponse(content={})

logger = logging.getLogger(__name__)


def _build_info(
    tool_name: str | None,
    output: Any,
    cwd: str | None,
    provider: str,
    timeout: int | None,
    level: NotificationLevel,
    sound: bool = False,
) -> NotificationInfo:
    """Build a rich NotificationInfo from an LLM output."""
    score = getattr(output, "safety_score", None)
    threshold = getattr(output, "threshold", None)
    reasoning = getattr(output, "reasoning", None)
    category = getattr(output, "category", None)

    # Build tool input summary
    tool_input_summary: str | None = None
    hook_input = getattr(output, "_hook_input", None)
    if hook_input is not None:
        ti = getattr(hook_input, "tool_input", None)
        if isinstance(ti, dict):
            cmd = ti.get("command")
            fp = ti.get("file_path")
            if cmd:
                tool_input_summary = cmd[:200]
            elif fp:
                tool_input_summary = fp

    # Suggested action based on score
    suggested_action = None
    if score is not None and threshold is not None:
        if score >= threshold:
            suggested_action = "approve"
        elif score <= 0:
            suggested_action = "deny"
        else:
            suggested_action = "review"

    title = f"Leash: {tool_name or 'unknown'}"
    body = reasoning or f"Score: {score}"

    return NotificationInfo(
        title=title,
        body=body,
        tool_name=tool_name,
        safety_score=score,
        threshold=threshold,
        reasoning=reasoning,
        suggested_action=suggested_action,
        category=category,
        provider=provider,
        cwd=cwd,
        tool_input_summary=tool_input_summary,
        sound=sound,
        timeout_seconds=timeout,
        level=level,
    )


async def make_tray_decision(
    mode: str,
    output: Any,
    harness_client: Any,
    event: str,
    tool_name: str,
    notification_svc: NotificationService | None,
    pending_decision_svc: PendingDecisionService | None,
    tray_config: TrayConfig | None,
    provider: str,
    cwd: str | None = None,
) -> JSONResponse:
    """Apply enforcement mode decision logic with tray integration."""

    score = getattr(output, "safety_score", None) or 0
    threshold = getattr(output, "threshold", None) or 85
    auto_approved = getattr(output, "auto_approve", False)
    timeout = getattr(tray_config, "interactive_timeout_seconds", 10) if tray_config else 10
    sound = getattr(tray_config, "sound", False) if tray_config else False

    # ── Determine if tray is active for this mode ──
    tray_active = False
    if tray_config and getattr(tray_config, "enabled", False):
        if mode == "observe" and getattr(tray_config, "show_in_observe", False):
            tray_active = True
        elif mode == "approve-only" and getattr(tray_config, "show_in_approve_only", True):
            tray_active = True
        elif mode == "enforce":
            tray_active = True

    # ── Observe mode: NEVER approve or deny ──
    if mode == "observe":
        if tray_active and notification_svc is not None:
            level = NotificationLevel.DANGER if score <= 0 else (
                NotificationLevel.INFO if auto_approved else NotificationLevel.WARNING
            )
            info = _build_info(tool_name, output, cwd, provider, None, level, sound)
            info.title = f"Leash: {tool_name or 'unknown'} (score {score})"
            try:
                await notification_svc.show_alert(info)
            except Exception:
                logger.debug("Failed to show observe alert for %s", tool_name, exc_info=True)
        return _NO_OPINION

    # ── Auto-approved (score >= threshold): approve in both approve-only and enforce ──
    if auto_approved:
        if harness_client is not None:
            return JSONResponse(content=harness_client.format_response(event, output))
        return _NO_OPINION

    # ── Unsafe request: mode-specific handling ──

    if mode == "approve-only":
        # Never auto-deny. Show interactive tray if available, else _NO_OPINION.
        return await _handle_unsafe_with_tray(
            mode=mode,
            output=output,
            harness_client=harness_client,
            event=event,
            tool_name=tool_name,
            notification_svc=notification_svc,
            pending_decision_svc=pending_decision_svc,
            tray_active=tray_active,
            timeout=timeout,
            score=score,
            sound=sound,
            cwd=cwd,
            provider=provider,
        )

    if mode == "enforce":
        # Default action is DENY. Show interactive tray if available to let user override.
        return await _handle_unsafe_with_tray(
            mode=mode,
            output=output,
            harness_client=harness_client,
            event=event,
            tool_name=tool_name,
            notification_svc=notification_svc,
            pending_decision_svc=pending_decision_svc,
            tray_active=tray_active,
            timeout=timeout,
            score=score,
            sound=sound,
            cwd=cwd,
            provider=provider,
        )

    # Unknown mode: safe fallback
    return _NO_OPINION


async def _handle_unsafe_with_tray(
    *,
    mode: str,
    output: Any,
    harness_client: Any,
    event: str,
    tool_name: str,
    notification_svc: NotificationService | None,
    pending_decision_svc: PendingDecisionService | None,
    tray_active: bool,
    timeout: int,
    score: int,
    sound: bool,
    cwd: str | None,
    provider: str,
) -> JSONResponse:
    """Handle an unsafe request with optional interactive tray dialog.

    In approve-only mode, fallback (no tray / timeout) is _NO_OPINION.
    In enforce mode, fallback (no tray / timeout) is DENY.
    """
    level = NotificationLevel.DANGER if score <= 0 else NotificationLevel.WARNING

    if mode == "enforce":
        title_prefix = f"Leash [DENY]: {tool_name or 'unknown'}"
    else:
        title_prefix = f"Leash: {tool_name or 'unknown'} needs review"

    # Try interactive tray
    if tray_active and notification_svc is not None and pending_decision_svc is not None:
        info = _build_info(tool_name, output, cwd, provider, timeout, level, sound)
        info.title = title_prefix

        try:
            decision_id, future = pending_decision_svc.create_pending(info, timeout)
        except Exception:
            logger.warning("Failed to create pending decision for %s", tool_name, exc_info=True)
            return _default_unsafe_response(mode, output, harness_client, event)

        interactive_handled = False
        if getattr(notification_svc, "supports_interactive", False):
            try:
                result = await notification_svc.show_interactive(info, timeout)
                interactive_handled = True
                if result is not None:
                    pending_decision_svc.try_resolve(decision_id, result)
                else:
                    pending_decision_svc.cancel(decision_id)

                return _apply_tray_result(result, output, harness_client, event, mode)
            except Exception:
                logger.debug("Interactive toast failed for %s", tool_name, exc_info=True)

        # Fallback: show passive alert + wait for pending decision from API
        if not interactive_handled:
            try:
                await notification_svc.show_alert(info)
            except Exception:
                logger.warning("Failed to show passive alert for %s", tool_name, exc_info=True)

            try:
                result = await future
                return _apply_tray_result(result, output, harness_client, event, mode)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pending_decision_svc.cancel(decision_id)
            except Exception:
                pending_decision_svc.cancel(decision_id)

    # No tray or tray not available: use default for mode
    logger.debug("%s mode - %s (score=%s) falling through to default", mode, tool_name, score)
    return _default_unsafe_response(mode, output, harness_client, event)


def _default_unsafe_response(
    mode: str, output: Any, harness_client: Any, event: str,
) -> JSONResponse:
    """Return the default response when there is no tray interaction.

    approve-only -> _NO_OPINION (never auto-deny)
    enforce      -> DENY via harness_client
    """
    if mode == "enforce" and harness_client is not None:
        output.auto_approve = False
        return JSONResponse(content=harness_client.format_response(event, output))
    return _NO_OPINION


def _apply_tray_result(
    result: TrayDecision | None,
    output: Any,
    harness_client: Any,
    event: str,
    mode: str,
) -> JSONResponse:
    """Convert a tray decision into a JSONResponse.

    Always returns a response: approve, deny, or _NO_OPINION.
    Timeout behavior is mode-specific (enforce=deny, approve-only=no-opinion).
    """
    if result == TrayDecision.APPROVE:
        output.auto_approve = True
        output.tray_decision = "tray-approved"
        if harness_client is not None:
            return JSONResponse(content=harness_client.format_response(event, output))
        return _NO_OPINION

    if result == TrayDecision.DENY:
        output.tray_decision = "tray-denied"
        if harness_client is not None:
            return JSONResponse(content=harness_client.format_response(event, output))
        return _NO_OPINION

    if result == TrayDecision.IGNORE:
        output.tray_decision = "tray-ignored"
        return _NO_OPINION

    # None (timeout/dismissed) — mode-specific behavior
    output.tray_decision = "tray-timeout"
    if mode == "enforce" and harness_client is not None:
        # Enforce: timeout means deny
        return JSONResponse(content=harness_client.format_response(event, output))
    # Approve-only: timeout means _NO_OPINION (never auto-deny)
    return _NO_OPINION
