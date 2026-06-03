"""Tests for _tray_helpers.py enforcement mode decision logic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse

from leash.models.tray_models import TrayDecision
from leash.routes._tray_helpers import make_tray_decision


def _make_output(score: int = 50, threshold: int = 85, auto_approve: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        safety_score=score,
        threshold=threshold,
        auto_approve=auto_approve,
        tray_decision=None,
        category="cautious",
        reasoning="test",
        _hook_input=None,
    )


def _make_harness() -> MagicMock:
    harness = MagicMock()
    harness.format_response.return_value = {"result": "allow"}
    return harness


def _make_tray_config(enabled=True, show_in_observe=True, show_in_approve_only=True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        show_in_observe=show_in_observe,
        show_in_approve_only=show_in_approve_only,
        interactive_timeout_seconds=5,
        sound=False,
    )


def _is_no_opinion(resp: JSONResponse) -> bool:
    return resp.body == b"{}"


# ── Observe mode ──


@pytest.mark.asyncio
async def test_observe_auto_approved_returns_no_opinion():
    """Observe mode: even safe requests return no-opinion (never approve)."""
    output = _make_output(score=95, auto_approve=True)
    resp = await make_tray_decision(
        mode="observe", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert _is_no_opinion(resp)


@pytest.mark.asyncio
async def test_observe_unsafe_returns_no_opinion():
    """Observe mode: unsafe requests return no-opinion (never deny)."""
    output = _make_output(score=10, auto_approve=False)
    resp = await make_tray_decision(
        mode="observe", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert _is_no_opinion(resp)


@pytest.mark.asyncio
async def test_observe_with_tray_shows_alert_not_interactive():
    """Observe mode with tray: shows informational alert, still returns no-opinion."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    output = _make_output(score=30, auto_approve=False)
    tray_config = _make_tray_config(enabled=True, show_in_observe=True)

    resp = await make_tray_decision(
        mode="observe", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=None,
        tray_config=tray_config, provider="claude",
    )
    assert _is_no_opinion(resp)
    notification_svc.show_alert.assert_called_once()
    notification_svc.show_interactive.assert_not_called()


# ── Approve-only mode ──


@pytest.mark.asyncio
async def test_approve_only_safe_request_approved():
    """Approve-only: safe request (auto_approve=True) is approved."""
    harness = _make_harness()
    output = _make_output(score=95, auto_approve=True)
    resp = await make_tray_decision(
        mode="approve-only", output=output, harness_client=harness,
        event="PreToolUse", tool_name="Read",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert not _is_no_opinion(resp)
    harness.format_response.assert_called_once()


@pytest.mark.asyncio
async def test_approve_only_unsafe_no_tray_returns_no_opinion():
    """Approve-only: unsafe request without tray returns no-opinion (never deny)."""
    output = _make_output(score=20, auto_approve=False)
    resp = await make_tray_decision(
        mode="approve-only", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert _is_no_opinion(resp)


@pytest.mark.asyncio
async def test_approve_only_tray_timeout_returns_no_opinion():
    """Approve-only: tray timeout returns no-opinion (never auto-deny)."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    notification_svc.show_interactive.return_value = None  # timeout
    pending_svc = MagicMock()
    pending_svc.create_pending.return_value = ("id1", asyncio.Future())

    output = _make_output(score=30, auto_approve=False)
    tray_config = _make_tray_config()

    resp = await make_tray_decision(
        mode="approve-only", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=pending_svc,
        tray_config=tray_config, provider="claude",
    )
    assert _is_no_opinion(resp)


@pytest.mark.asyncio
async def test_approve_only_tray_approve():
    """Approve-only: user clicks Approve in tray."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    notification_svc.show_interactive.return_value = TrayDecision.APPROVE
    pending_svc = MagicMock()
    pending_svc.create_pending.return_value = ("id1", asyncio.Future())

    harness = _make_harness()
    output = _make_output(score=30, auto_approve=False)
    tray_config = _make_tray_config()

    resp = await make_tray_decision(
        mode="approve-only", output=output, harness_client=harness,
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=pending_svc,
        tray_config=tray_config, provider="claude",
    )
    assert not _is_no_opinion(resp)
    assert output.tray_decision == "tray-approved"


# ── Enforce mode ──


@pytest.mark.asyncio
async def test_enforce_safe_request_approved():
    """Enforce: safe request is approved."""
    harness = _make_harness()
    output = _make_output(score=95, auto_approve=True)
    resp = await make_tray_decision(
        mode="enforce", output=output, harness_client=harness,
        event="PreToolUse", tool_name="Read",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert not _is_no_opinion(resp)
    harness.format_response.assert_called_once()


@pytest.mark.asyncio
async def test_enforce_unsafe_no_tray_denies():
    """Enforce: unsafe request without tray is denied."""
    harness = _make_harness()
    output = _make_output(score=20, auto_approve=False)
    resp = await make_tray_decision(
        mode="enforce", output=output, harness_client=harness,
        event="PreToolUse", tool_name="Bash",
        notification_svc=None, pending_decision_svc=None,
        tray_config=None, provider="claude",
    )
    assert not _is_no_opinion(resp)
    harness.format_response.assert_called_once()
    assert output.auto_approve is False


@pytest.mark.asyncio
async def test_enforce_tray_timeout_denies():
    """Enforce: tray timeout executes deny."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    notification_svc.show_interactive.return_value = None  # timeout
    pending_svc = MagicMock()
    pending_svc.create_pending.return_value = ("id1", asyncio.Future())

    harness = _make_harness()
    output = _make_output(score=30, auto_approve=False)
    tray_config = _make_tray_config()

    resp = await make_tray_decision(
        mode="enforce", output=output, harness_client=harness,
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=pending_svc,
        tray_config=tray_config, provider="claude",
    )
    assert not _is_no_opinion(resp)
    assert output.tray_decision == "tray-timeout"


@pytest.mark.asyncio
async def test_enforce_tray_ignore_returns_no_opinion():
    """Enforce: user clicks Ignore in tray overrides system deny."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    notification_svc.show_interactive.return_value = TrayDecision.IGNORE
    pending_svc = MagicMock()
    pending_svc.create_pending.return_value = ("id1", asyncio.Future())

    output = _make_output(score=30, auto_approve=False)
    tray_config = _make_tray_config()

    resp = await make_tray_decision(
        mode="enforce", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=pending_svc,
        tray_config=tray_config, provider="claude",
    )
    assert _is_no_opinion(resp)
    assert output.tray_decision == "tray-ignored"


@pytest.mark.asyncio
async def test_enforce_tray_enabled_by_default():
    """Enforce mode: tray is active when tray_config.enabled=True (no extra flag needed)."""
    notification_svc = AsyncMock()
    notification_svc.supports_interactive = True
    notification_svc.show_interactive.return_value = TrayDecision.APPROVE
    pending_svc = MagicMock()
    pending_svc.create_pending.return_value = ("id1", asyncio.Future())

    output = _make_output(score=30, auto_approve=False)
    # Only enabled=True, no show_in_observe or show_in_approve_only needed for enforce
    tray_config = _make_tray_config(enabled=True, show_in_observe=False, show_in_approve_only=False)

    resp = await make_tray_decision(
        mode="enforce", output=output, harness_client=_make_harness(),
        event="PreToolUse", tool_name="Bash",
        notification_svc=notification_svc, pending_decision_svc=pending_svc,
        tray_config=tray_config, provider="claude",
    )
    # If tray is active, show_interactive should have been called
    notification_svc.show_interactive.assert_called_once()
