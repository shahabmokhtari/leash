"""Quick actions — lockdown, trust-session, reset, status."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_profile_service(request: Request) -> Any:
    return getattr(request.app.state, "profile_service", None)


def _get_insights_engine(request: Request) -> Any:
    return getattr(request.app.state, "insights_engine", None)


def _get_adaptive_service(request: Request) -> Any:
    return getattr(request.app.state, "adaptive_threshold_service", None)


@router.post("/api/quickactions/lockdown")
async def lockdown(request: Request) -> JSONResponse:
    """Activate lockdown mode — all auto-approvals disabled."""
    svc = _get_profile_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Profile service not available"})

    await svc.switch_profile("lockdown")
    logger.warning("Lockdown activated - all auto-approvals disabled")
    return JSONResponse(
        content={
            "action": "lockdown",
            "message": "Lockdown activated. All operations now require manual approval.",
            "profile": "lockdown",
        }
    )


@router.post("/api/quickactions/trust-session")
async def trust_session(request: Request) -> JSONResponse:
    """Activate trust/permissive mode for the current session."""
    svc = _get_profile_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Profile service not available"})

    await svc.switch_profile("permissive")
    logger.info("Trust session activated - switched to permissive profile")
    return JSONResponse(
        content={
            "action": "trust-session",
            "message": "Session trusted. Switched to permissive mode with lower thresholds.",
            "profile": "permissive",
        }
    )


@router.post("/api/quickactions/reset")
async def reset(request: Request) -> JSONResponse:
    """Reset to moderate profile with balanced thresholds."""
    svc = _get_profile_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Profile service not available"})

    await svc.switch_profile("moderate")
    logger.info("Reset to moderate profile")
    return JSONResponse(
        content={
            "action": "reset",
            "message": "Reset to moderate profile with balanced thresholds.",
            "profile": "moderate",
        }
    )


@router.get("/api/quickactions/status")
async def get_status(request: Request) -> JSONResponse:
    """Return current quick-actions status: active profile, pending insights, tracked tools."""
    profile_svc = _get_profile_service(request)
    insights_engine = _get_insights_engine(request)
    adaptive_svc = _get_adaptive_service(request)

    profile_key = "moderate"
    profile_name = "Moderate"
    auto_approve_enabled = True
    default_threshold = 85
    if profile_svc is not None:
        profile_key = profile_svc.get_active_profile_key()
        profile = profile_svc.get_active_profile()
        profile_name = getattr(profile, "name", profile_key)
        auto_approve_enabled = getattr(profile, "auto_approve_enabled", True)
        default_threshold = getattr(profile, "default_threshold", 85)

    pending_insights = 0
    if insights_engine is not None:
        try:
            insights = insights_engine.get_insights()
            pending_insights = len(insights)
        except Exception:
            pass

    tracked_tools = 0
    total_overrides = 0
    if adaptive_svc is not None:
        try:
            stats = adaptive_svc.get_tool_stats()
            tracked_tools = len(stats)
            total_overrides = sum(
                getattr(s, "override_count", 0) for s in stats.values()
            )
        except Exception:
            pass

    return JSONResponse(
        content={
            "activeProfile": profile_key,
            "profileName": profile_name,
            "autoApproveEnabled": auto_approve_enabled,
            "defaultThreshold": default_threshold,
            "pendingInsights": pending_insights,
            "trackedTools": tracked_tools,
            "totalOverrides": total_overrides,
        }
    )
