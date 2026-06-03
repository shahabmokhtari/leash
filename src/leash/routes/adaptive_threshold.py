"""Adaptive threshold endpoints — stats, overrides, suggestions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_adaptive_service(request: Request) -> Any:
    return getattr(request.app.state, "adaptive_threshold_service", None)


@router.get("/api/adaptivethreshold/stats")
async def get_stats(request: Request) -> JSONResponse:
    """Return per-tool threshold statistics."""
    svc = _get_adaptive_service(request)
    if svc is None:
        return JSONResponse(content={"toolStats": []})

    stats = svc.get_tool_stats()
    tool_stats = []
    for tool_name, s in stats.items():
        tool_stats.append(
            {
                "toolName": tool_name,
                "totalDecisions": getattr(s, "total_decisions", 0),
                "overrideCount": getattr(s, "override_count", 0),
                "falsePositives": getattr(s, "false_positives", 0),
                "falseNegatives": getattr(s, "false_negatives", 0),
                "suggestedThreshold": getattr(s, "suggested_threshold", None),
                "averageSafetyScore": round(getattr(s, "average_safety_score", 0.0), 1),
                "confidenceLevel": round(getattr(s, "confidence_level", 0.0), 2),
            }
        )

    return JSONResponse(content={"toolStats": tool_stats})


@router.get("/api/adaptivethreshold/overrides")
async def get_recent_overrides(
    request: Request,
    count: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    """Return recent user overrides."""
    svc = _get_adaptive_service(request)
    if svc is None:
        return JSONResponse(content={"overrides": []})

    overrides = svc.get_recent_overrides(min(count, 100))
    return JSONResponse(content={"overrides": overrides})


@router.post("/api/adaptivethreshold/override")
async def record_override(request: Request) -> JSONResponse:
    """Record a user override of an automatic decision."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid request body"})

    tool_name = body.get("toolName")
    original_decision = body.get("originalDecision")
    user_action = body.get("userAction")
    safety_score = body.get("safetyScore", 0)
    threshold = body.get("threshold", 0)
    session_id = body.get("sessionId", "")

    if not tool_name or not str(tool_name).strip():
        return JSONResponse(status_code=400, content={"error": "toolName is required"})
    if not original_decision or not str(original_decision).strip():
        return JSONResponse(status_code=400, content={"error": "originalDecision and userAction are required"})
    if not user_action or not str(user_action).strip():
        return JSONResponse(status_code=400, content={"error": "originalDecision and userAction are required"})

    svc = _get_adaptive_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Adaptive threshold service not available"})

    await svc.record_override(
        tool_name, original_decision, user_action, safety_score, threshold, session_id
    )

    suggested = svc.get_suggested_threshold(tool_name)
    return JSONResponse(content={"recorded": True, "suggestedThreshold": suggested})


@router.get("/api/adaptivethreshold/suggestion/{tool_name}")
async def get_suggestion(request: Request, tool_name: str) -> JSONResponse:
    """Get the suggested threshold adjustment for a specific tool."""
    svc = _get_adaptive_service(request)
    if svc is None:
        return JSONResponse(
            content={
                "toolName": tool_name,
                "suggestedThreshold": None,
                "confidence": 0,
                "totalDecisions": 0,
                "overrideCount": 0,
            }
        )

    suggested = svc.get_suggested_threshold(tool_name)
    stats = svc.get_tool_stats()
    tool_stats = stats.get(tool_name)

    return JSONResponse(
        content={
            "toolName": tool_name,
            "suggestedThreshold": suggested,
            "confidence": getattr(tool_stats, "confidence_level", 0) if tool_stats else 0,
            "totalDecisions": getattr(tool_stats, "total_decisions", 0) if tool_stats else 0,
            "overrideCount": getattr(tool_stats, "override_count", 0) if tool_stats else 0,
        }
    )
