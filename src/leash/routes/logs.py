"""Log endpoints — filtered listing, clear, export (JSON/CSV)."""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


def _parse_filter_values(param: str | None) -> set[str] | None:
    """Parse comma-separated filter values into a set, or None if empty."""
    if not param or not param.strip():
        return None
    return {v.strip().lower() for v in param.split(",") if v.strip()}


def _event_to_dict(e: Any, session_id: str) -> dict:
    """Convert a SessionEvent to a serializable dict."""
    ts = getattr(e, "timestamp", None)
    return {
        "timestamp": ts.isoformat() if ts else None,
        "type": getattr(e, "type", ""),
        "toolName": getattr(e, "tool_name", None),
        "toolInput": getattr(e, "tool_input", None),
        "decision": getattr(e, "decision", None),
        "safetyScore": getattr(e, "safety_score", None),
        "reasoning": getattr(e, "reasoning", None),
        "category": getattr(e, "category", None),
        "content": getattr(e, "content", None),
        "handlerName": getattr(e, "handler_name", None),
        "promptTemplate": getattr(e, "prompt_template", None),
        "threshold": getattr(e, "threshold", None),
        "elapsedMs": getattr(e, "elapsed_ms", None),
        "responseJson": getattr(e, "response_json", None),
        "provider": getattr(e, "provider", None),
        "sessionId": session_id,
    }


def _apply_filters(
    events: list[dict],
    decision: str | None,
    category: str | None,
    tool_name: str | None,
    hook_type: str | None,
    provider: str | None,
) -> list[dict]:
    """Apply multi-value comma-separated filters to event list."""
    decision_filter = _parse_filter_values(decision)
    category_filter = _parse_filter_values(category)
    tool_name_filter = _parse_filter_values(tool_name)
    hook_type_filter = _parse_filter_values(hook_type)
    provider_filter = _parse_filter_values(provider)

    result = []
    for e in events:
        if decision_filter and (e.get("decision") or "").lower() not in decision_filter:
            continue
        if category_filter and (e.get("category") or "").lower() not in category_filter:
            continue
        if tool_name_filter:
            tn = (e.get("toolName") or "").lower()
            if not any(v in tn for v in tool_name_filter):
                continue
        if hook_type_filter and (e.get("type") or "").lower() not in hook_type_filter:
            continue
        if provider_filter and (e.get("provider") or "claude").lower() not in provider_filter:
            continue
        result.append(e)
    return result


async def _get_all_events(session_manager: Any, session_id_filter: str | None = None) -> list[dict]:
    """Collect events from all sessions (or a specific session)."""
    if session_manager is None:
        return []
    sessions = await session_manager.get_all_sessions()
    events = []
    for s in sessions:
        sid = getattr(s, "session_id", "")
        if session_id_filter and sid != session_id_filter:
            continue
        for e in getattr(s, "conversation_history", []):
            events.append(_event_to_dict(e, sid))
    return events


@router.get("/api/logs")
async def get_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    decision: str | None = Query(default=None),
    category: str | None = Query(default=None),
    sessionId: str | None = Query(default=None),
    toolName: str | None = Query(default=None),
    hookType: str | None = Query(default=None),
    provider: str | None = Query(default=None),
) -> JSONResponse:
    """Return filtered logs, supports comma-separated multi-values."""
    session_manager = _get_session_manager(request)

    try:
        events = await _get_all_events(session_manager, session_id_filter=sessionId)
        events = _apply_filters(events, decision, category, toolName, hookType, provider)
        events.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return JSONResponse(content=events[:limit])
    except Exception as exc:
        logger.error("Failed to retrieve logs: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load logs"})


@router.delete("/api/logs")
async def clear_logs(request: Request) -> JSONResponse:
    """Clear all session log files."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(content={"cleared": 0, "message": "Cleared 0 session(s)"})

    try:
        deleted = await session_manager.clear_all_sessions()
        return JSONResponse(content={"cleared": deleted, "message": f"Cleared {deleted} session(s)"})
    except Exception as exc:
        logger.error("Failed to clear logs: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to clear logs"})


@router.get("/api/logs/export/json")
async def export_json(request: Request) -> StreamingResponse:
    """Export all logs as a downloadable JSON file."""
    session_manager = _get_session_manager(request)

    try:
        events = await _get_all_events(session_manager)
        events.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        content = json.dumps(events, indent=2)
        filename = f"permission-logs-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Failed to export logs as JSON: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to export logs"})


def _escape_csv_field(field: str) -> str:
    """Escape a CSV field if it contains commas, quotes, or newlines."""
    if "," in field or '"' in field or "\n" in field:
        return '"' + field.replace('"', '""') + '"'
    return field


@router.get("/api/logs/export/csv")
async def export_csv(request: Request) -> StreamingResponse:
    """Export all logs as a downloadable CSV file."""
    session_manager = _get_session_manager(request)

    try:
        events = await _get_all_events(session_manager)
        events.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        output = io.StringIO()
        output.write("Timestamp,SessionId,Type,ToolName,Decision,SafetyScore,Category,Provider,Reasoning\n")
        for evt in events:
            row = [
                _escape_csv_field(str(evt.get("timestamp") or "")),
                _escape_csv_field(str(evt.get("sessionId") or "")),
                _escape_csv_field(str(evt.get("type") or "")),
                _escape_csv_field(str(evt.get("toolName") or "")),
                _escape_csv_field(str(evt.get("decision") or "")),
                _escape_csv_field(str(evt.get("safetyScore") or "")),
                _escape_csv_field(str(evt.get("category") or "")),
                _escape_csv_field(str(evt.get("provider") or "")),
                _escape_csv_field(str(evt.get("reasoning") or "")),
            ]
            output.write(",".join(row) + "\n")

        filename = f"permission-logs-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Failed to export logs as CSV: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to export logs"})
