"""Dashboard endpoints — stats, sessions, activity, trends, health."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


@router.get("/api/dashboard/stats")
async def get_stats(request: Request) -> JSONResponse:
    """Return dashboard statistics: approvals, denials, active sessions, average score."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(
            content={
                "autoApprovedToday": 0,
                "deniedToday": 0,
                "activeSessions": 0,
                "avgSafetyScore": 0,
                "totalEventsToday": 0,
            }
        )

    try:
        sessions = await session_manager.get_all_sessions()
        today = datetime.now(timezone.utc).date()

        all_events = []
        active_count = 0
        one_hour_ago = datetime.now(timezone.utc).timestamp() - 3600

        for s in sessions:
            history = getattr(s, "conversation_history", [])
            all_events.extend(history)
            last_activity = getattr(s, "last_activity", None)
            if last_activity is not None and last_activity.timestamp() > one_hour_ago:
                active_count += 1

        today_events = []
        for e in all_events:
            ts = getattr(e, "timestamp", None)
            if ts is not None and ts.date() == today:
                today_events.append(e)
        auto_approved = sum(1 for e in today_events if getattr(e, "decision", "") in ("auto-approved", "script-approved", "tray-approved"))
        denied = sum(1 for e in today_events if getattr(e, "decision", "") in ("denied", "script-denied", "tray-denied"))

        scored = [e for e in today_events if getattr(e, "safety_score", None) is not None]
        avg_score = round(sum(e.safety_score for e in scored) / len(scored)) if scored else 0

        return JSONResponse(
            content={
                "autoApprovedToday": auto_approved,
                "deniedToday": denied,
                "activeSessions": active_count,
                "avgSafetyScore": avg_score,
                "totalEventsToday": len(today_events),
            }
        )
    except Exception as exc:
        logger.error("Failed to retrieve dashboard stats: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load dashboard statistics"})


@router.get("/api/dashboard/latency")
async def get_latency_stats(request: Request) -> JSONResponse:
    """Return hook response latency statistics: overall, per provider, per session, and time series."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(content={"overall": _empty_latency(), "byProvider": {}, "bySessions": [], "timeSeries": []})

    try:
        sessions = await session_manager.get_all_sessions()

        all_latencies: list[int] = []
        by_provider: dict[str, list[int]] = {}
        session_stats: list[dict] = []
        # Time series: collect (timestamp_iso, provider, ms) tuples
        ts_points: list[tuple[str, str, int]] = []

        for s in sessions:
            history = getattr(s, "conversation_history", [])
            session_latencies: list[int] = []

            for e in history:
                ms = getattr(e, "elapsed_ms", None)
                if ms is None or ms <= 0:
                    continue
                provider = getattr(e, "llm_provider", None) or getattr(e, "provider", "unknown") or "unknown"
                all_latencies.append(ms)
                session_latencies.append(ms)
                by_provider.setdefault(provider, []).append(ms)
                ts = getattr(e, "timestamp", None)
                if ts is not None:
                    ts_points.append((ts.isoformat(), provider, ms))

            if session_latencies:
                session_stats.append({
                    "sessionId": getattr(s, "session_id", ""),
                    "provider": getattr(s, "provider", None) or (
                        getattr(history[0], "provider", "unknown") if history else "unknown"
                    ),
                    "count": len(session_latencies),
                    **_calc_latency(session_latencies),
                })

        # Sort sessions by most recent activity (most events = likely most recent)
        session_stats.sort(key=lambda x: x["count"], reverse=True)

        # Build hourly time series per provider
        time_series = _build_latency_time_series(ts_points)

        return JSONResponse(content={
            "overall": {**_calc_latency(all_latencies), "count": len(all_latencies)},
            "byProvider": {
                p: {**_calc_latency(lats), "count": len(lats)}
                for p, lats in by_provider.items()
            },
            "bySessions": session_stats[:20],  # Top 20 sessions
            "timeSeries": time_series,
        })
    except Exception as exc:
        logger.error("Failed to compute latency stats: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to compute latency stats"})


def _build_latency_time_series(
    points: list[tuple[str, str, int]],
) -> list[dict]:
    """Group latency points into hourly buckets per provider.

    Returns a list of ``{"time": iso_hour, "provider": str, "avg": int,
    "median": int, "p95": int, "count": int}`` sorted by time.
    """
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for ts_iso, provider, ms in points:
        # Truncate to hour
        hour = ts_iso[:13] + ":00:00"
        buckets[(hour, provider)].append(ms)

    result = []
    for (hour, provider), values in buckets.items():
        stats = _calc_latency(values)
        result.append({
            "time": hour,
            "provider": provider,
            "avg": stats["avg"],
            "median": stats["median"],
            "p95": stats["p95"],
            "max": stats["max"],
            "count": len(values),
        })
    result.sort(key=lambda x: x["time"])
    return result


def _empty_latency() -> dict:
    return {"min": 0, "max": 0, "avg": 0, "median": 0, "p95": 0, "count": 0}


def _calc_latency(values: list[int]) -> dict:
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "median": 0, "p95": 0}
    s = sorted(values)
    n = len(s)
    return {
        "min": s[0],
        "max": s[-1],
        "avg": round(sum(s) / n),
        "median": s[n // 2],
        "p95": s[min(int(n * 0.95), n - 1)],
    }


@router.get("/api/dashboard/sessions")
async def get_sessions(request: Request) -> JSONResponse:
    """Return session list with event counts."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(content=[])

    try:
        sessions = await session_manager.get_all_sessions()

        result = []
        for s in sessions:
            history = getattr(s, "conversation_history", [])
            approved_count = sum(1 for e in history if getattr(e, "decision", "") in ("auto-approved", "script-approved", "tray-approved"))
            denied_count = sum(1 for e in history if getattr(e, "decision", "") in ("denied", "script-denied", "tray-denied"))
            last_tool = getattr(history[-1], "tool_name", None) if history else None

            start_time = getattr(s, "start_time", None)
            last_activity = getattr(s, "last_activity", None)
            result.append(
                {
                    "sessionId": getattr(s, "session_id", ""),
                    "startTime": start_time.isoformat() if start_time else None,
                    "lastActivity": last_activity.isoformat() if last_activity else None,
                    "workingDirectory": getattr(s, "working_directory", None),
                    "eventCount": len(history),
                    "approvedCount": approved_count,
                    "deniedCount": denied_count,
                    "lastTool": last_tool,
                }
            )

        # Sort by last activity descending (sessions without timestamps go last)
        result.sort(key=lambda x: x["lastActivity"] or "", reverse=True)
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("Failed to retrieve sessions: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load sessions"})


@router.get("/api/dashboard/activity")
async def get_recent_activity(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """Return recent events across all sessions."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(content=[])

    try:
        sessions = await session_manager.get_all_sessions()

        events = []
        for s in sessions:
            session_id = getattr(s, "session_id", "")
            for e in getattr(s, "conversation_history", []):
                evt_ts = getattr(e, "timestamp", None)
                events.append(
                    {
                        "timestamp": evt_ts.isoformat() if evt_ts else None,
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
                        "sessionId": session_id,
                    }
                )

        # Sort by timestamp descending and take limit
        events.sort(key=lambda x: x["timestamp"] or "", reverse=True)
        return JSONResponse(content=events[:limit])
    except Exception as exc:
        logger.error("Failed to retrieve recent activity: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load activity feed"})


@router.get("/api/dashboard/trends")
async def get_trends(
    request: Request,
    days: int = Query(default=7, ge=1, le=30),
) -> JSONResponse:
    """Return per-day approved/denied/total trends."""
    session_manager = _get_session_manager(request)
    if session_manager is None:
        return JSONResponse(content=[])

    try:
        sessions = await session_manager.get_all_sessions()
        all_events = []
        for s in sessions:
            all_events.extend(getattr(s, "conversation_history", []))

        from datetime import timedelta

        today = datetime.now(timezone.utc).date()
        trends = []
        for i in range(days - 1, -1, -1):
            day = today - timedelta(days=i)
            day_events = [e for e in all_events if getattr(e, "timestamp", None) is not None and e.timestamp.date() == day]
            trends.append(
                {
                    "date": day.isoformat(),
                    "approved": sum(1 for e in day_events if getattr(e, "decision", "") in ("auto-approved", "script-approved", "tray-approved")),
                    "denied": sum(1 for e in day_events if getattr(e, "decision", "") in ("denied", "script-denied", "tray-denied")),
                    "total": len(day_events),
                }
            )

        return JSONResponse(content=trends)
    except Exception as exc:
        logger.error("Failed to retrieve trends data: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to load trends"})


@router.get("/api/dashboard/health")
async def get_dashboard_health() -> JSONResponse:
    """Simple health check for the dashboard."""
    return JSONResponse(
        content={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime": int(time.monotonic() * 1000),
        }
    )
