"""Audit report endpoints — JSON and HTML reports per session."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from leash.security.input_sanitizer import InputSanitizer

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_report_generator(request: Request) -> Any:
    return getattr(request.app.state, "audit_report_generator", None)


@router.get("/api/auditreport/{session_id}")
async def get_report(request: Request, session_id: str) -> JSONResponse:
    """Generate a JSON audit report for a session."""
    if not session_id or not session_id.strip():
        return JSONResponse(status_code=400, content={"error": "sessionId is required"})

    if not InputSanitizer.is_valid_session_id(session_id):
        return JSONResponse(status_code=400, content={"error": "Invalid session ID"})

    generator = _get_report_generator(request)
    if generator is None:
        return JSONResponse(status_code=503, content={"error": "Audit report generator not available"})

    try:
        report = await generator.generate_report(session_id)
        if hasattr(report, "model_dump"):
            return JSONResponse(content=report.model_dump(by_alias=True))
        return JSONResponse(content=report)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("Failed to generate audit report for session %s: %s", session_id, exc)
        return JSONResponse(status_code=500, content={"error": "Failed to generate report"})


@router.get("/api/auditreport/{session_id}/html", response_model=None)
async def get_html_report(request: Request, session_id: str) -> HTMLResponse | JSONResponse:
    """Generate an HTML audit report for a session."""
    if not session_id or not session_id.strip():
        return JSONResponse(status_code=400, content={"error": "sessionId is required"})

    if not InputSanitizer.is_valid_session_id(session_id):
        return JSONResponse(status_code=400, content={"error": "Invalid session ID"})

    generator = _get_report_generator(request)
    if generator is None:
        return JSONResponse(status_code=503, content={"error": "Audit report generator not available"})

    try:
        report = await generator.generate_report(session_id)
        html = generator.render_html(report)
        return HTMLResponse(content=html)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("Failed to generate HTML audit report for session %s: %s", session_id, exc)
        return JSONResponse(status_code=500, content={"error": "Failed to generate report"})
