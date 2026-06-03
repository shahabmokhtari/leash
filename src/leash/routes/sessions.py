"""Session endpoints — GET /api/sessions/{session_id}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from leash.security.input_sanitizer import InputSanitizer

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


@router.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> JSONResponse:
    """Return full session data for a given session ID."""
    if not session_id or not session_id.strip():
        return JSONResponse(status_code=400, content={"error": "SessionId is required"})

    if not InputSanitizer.is_valid_session_id(session_id):
        return JSONResponse(status_code=400, content={"error": "Invalid session ID"})

    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not available"})

    try:
        session = await session_manager.get_or_create_session(session_id)
        return JSONResponse(content=session.model_dump(by_alias=True))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("Failed to get session %s: %s", session_id, exc)
        return JSONResponse(status_code=500, content={"error": "Failed to retrieve session"})


@router.get("/api/sessions/{session_id}/events")
async def get_session_events(
    request: Request,
    session_id: str,
    limit: int | None = Query(default=None, ge=1),
) -> JSONResponse:
    """Return events for a session, optionally limited to the last N."""
    if not session_id or not session_id.strip():
        return JSONResponse(status_code=400, content={"error": "SessionId is required"})

    if not InputSanitizer.is_valid_session_id(session_id):
        return JSONResponse(status_code=400, content={"error": "Invalid session ID"})

    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not available"})

    try:
        session = await session_manager.get_or_create_session(session_id)
        events = getattr(session, "conversation_history", [])
        if limit is not None:
            events = events[-limit:]
        return JSONResponse(content=[e.model_dump(by_alias=True) for e in events])
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("Failed to get events for session %s: %s", session_id, exc)
        return JSONResponse(status_code=500, content={"error": "Failed to retrieve session events"})
