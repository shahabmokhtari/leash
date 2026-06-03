"""Permission profile endpoints — list profiles, switch profile."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_profile_service(request: Request) -> Any:
    return getattr(request.app.state, "profile_service", None)


@router.get("/api/profile")
async def get_profiles(request: Request) -> JSONResponse:
    """List all profiles and indicate which is active."""
    svc = _get_profile_service(request)
    if svc is None:
        # Return built-in profiles as fallback
        from leash.models.permission_profile import BUILTIN_PROFILES

        profiles_list = []
        for key, p in BUILTIN_PROFILES.items():
            profiles_list.append(
                {
                    "key": key,
                    "name": p.name,
                    "description": p.description,
                    "defaultThreshold": p.default_threshold,
                    "autoApproveEnabled": p.auto_approve_enabled,
                    "thresholdOverrides": p.threshold_overrides,
                    "isActive": key == "moderate",
                }
            )
        return JSONResponse(content={"activeProfile": "moderate", "profiles": profiles_list})

    all_profiles = svc.get_all_profiles()
    active_key = svc.get_active_profile_key()

    profiles_list = []
    for key, p in all_profiles.items():
        profiles_list.append(
            {
                "key": key,
                "name": getattr(p, "name", key),
                "description": getattr(p, "description", ""),
                "defaultThreshold": getattr(p, "default_threshold", 85),
                "autoApproveEnabled": getattr(p, "auto_approve_enabled", True),
                "thresholdOverrides": getattr(p, "threshold_overrides", {}),
                "isActive": key == active_key,
            }
        )

    return JSONResponse(content={"activeProfile": active_key, "profiles": profiles_list})


@router.post("/api/profile/switch")
async def switch_profile(request: Request) -> JSONResponse:
    """Switch the active permission profile."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    profile_key = body.get("profileKey") if isinstance(body, dict) else None
    if not profile_key or not str(profile_key).strip():
        return JSONResponse(status_code=400, content={"error": "profileKey is required"})

    svc = _get_profile_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Profile service not available"})

    success = await svc.switch_profile(profile_key)
    if not success:
        return JSONResponse(status_code=404, content={"error": f"Profile '{profile_key}' not found"})

    logger.info("Profile switched to %s", profile_key)
    active_profile = svc.get_active_profile()
    return JSONResponse(
        content={
            "activeProfile": profile_key,
            "profile": active_profile.model_dump(by_alias=True) if hasattr(active_profile, "model_dump") else {},
        }
    )
