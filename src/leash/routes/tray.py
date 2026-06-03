"""Tray service endpoints — status, start, pending decisions, decide."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_tray_service(request: Request) -> Any:
    return getattr(request.app.state, "tray_service", None)


def _get_pending_service(request: Request) -> Any:
    return getattr(request.app.state, "pending_decision_service", None)


@router.get("/api/tray/status")
async def get_status(request: Request) -> JSONResponse:
    """Return tray service status and pending decision count."""
    tray_svc = _get_tray_service(request)
    pending_svc = _get_pending_service(request)

    available = False
    service_type = "NullTrayService"
    if tray_svc is not None:
        available = getattr(tray_svc, "is_available", False)
        service_type = type(tray_svc).__name__

    pending_count = 0
    if pending_svc is not None:
        try:
            pending_count = len(pending_svc.get_pending())
        except Exception:
            pass

    return JSONResponse(
        content={
            "available": available,
            "serviceType": service_type,
            "pendingCount": pending_count,
        }
    )


@router.post("/api/tray/start")
async def start_tray(request: Request) -> JSONResponse:
    """Start the tray service if not already running."""
    tray_svc = _get_tray_service(request)
    if tray_svc is None:
        return JSONResponse(status_code=503, content={"error": "Tray service not available"})

    if getattr(tray_svc, "is_available", False):
        return JSONResponse(content={"started": True, "message": "Tray service already running"})

    try:
        await tray_svc.start()
        logger.info("Tray service started via API")
        return JSONResponse(
            content={"started": True, "available": getattr(tray_svc, "is_available", False)}
        )
    except Exception as exc:
        logger.error("Failed to start tray service via API: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/api/tray/pending")
async def get_pending(request: Request) -> JSONResponse:
    """List all currently pending interactive decisions."""
    pending_svc = _get_pending_service(request)
    if pending_svc is None:
        return JSONResponse(content=[])

    try:
        pending = pending_svc.get_pending()
        result = []
        for p in pending:
            info = getattr(p, "info", None)
            result.append(
                {
                    "id": getattr(p, "id", ""),
                    "toolName": getattr(info, "tool_name", None) if info else None,
                    "safetyScore": getattr(info, "safety_score", None) if info else None,
                    "category": getattr(info, "category", None) if info else None,
                    "reasoning": getattr(info, "reasoning", None) if info else None,
                    "level": str(getattr(info, "level", "info")).lower() if info else "info",
                    "createdAt": getattr(p, "created_at", ""),
                }
            )
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("Failed to get pending decisions: %s", exc)
        return JSONResponse(content=[])


@router.post("/api/tray/decide/{decision_id}")
async def decide(request: Request, decision_id: str) -> JSONResponse:
    """Resolve a pending decision from the web dashboard."""
    pending_svc = _get_pending_service(request)
    if pending_svc is None:
        return JSONResponse(status_code=503, content={"error": "Pending decision service not available"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    approve = body.get("approve", False) if isinstance(body, dict) else False

    from leash.models.tray_models import TrayDecision

    decision = TrayDecision.APPROVE if approve else TrayDecision.DENY
    resolved = pending_svc.try_resolve(decision_id, decision)

    if not resolved:
        return JSONResponse(status_code=404, content={"error": "Decision not found or already resolved"})

    logger.info("Web dashboard resolved decision %s with %s", decision_id, decision.value)
    return JSONResponse(content={"resolved": True, "decision": decision.value})
