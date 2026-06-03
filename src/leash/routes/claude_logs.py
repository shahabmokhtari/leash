"""Claude transcript browsing and SSE streaming."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from leash.security import InputSanitizer

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _serialize(obj: Any) -> Any:
    """Convert dataclass instances to camelCase dicts recursively for JSON serialization."""
    import datetime

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # Walk fields manually to preserve nested dataclass handling
        result = {}
        for f in dataclasses.fields(obj):
            result[_to_camel(f.name)] = _serialize(getattr(obj, f.name))
        return result
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {_to_camel(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return obj


def _get_transcript_watcher(request: Request) -> Any:
    return getattr(request.app.state, "transcript_watcher", None)


def _validate_session_id(session_id: str) -> str | None:
    """Validate transcript session ID. Returns error message or None."""
    if not session_id or not session_id.strip():
        return "SessionId is required"
    if not InputSanitizer.is_valid_session_id(session_id):
        return "Invalid session ID"
    return None


@router.get("/api/claude-logs/projects")
@router.get("/api/transcripts/projects")
async def get_projects(request: Request) -> JSONResponse:
    """List available Claude projects with transcripts."""
    watcher = _get_transcript_watcher(request)
    if watcher is None:
        return JSONResponse(content=[])

    try:
        projects = await watcher.get_projects_async()
        return JSONResponse(content=_serialize(projects))
    except Exception as exc:
        logger.error("Failed to list projects: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to list projects"})


@router.get("/api/claude-logs/transcript/{session_id}")
async def get_transcript(request: Request, session_id: str) -> JSONResponse:
    """Get transcript entries for a specific session."""
    error = _validate_session_id(session_id)
    if error:
        return JSONResponse(status_code=400, content={"error": error})

    watcher = _get_transcript_watcher(request)
    if watcher is None:
        return JSONResponse(content=[])

    try:
        entries = watcher.get_transcript(session_id)
        return JSONResponse(content=_serialize(entries))
    except Exception as exc:
        logger.error("Failed to get transcript for session %s: %s", session_id, exc)
        return JSONResponse(status_code=500, content={"error": "Failed to get transcript"})


@router.get("/api/transcripts/token-usage/{session_id}")
async def get_token_usage(request: Request, session_id: str) -> JSONResponse:
    """Calculate token usage for a session by scanning its JSONL file.

    Returns per-model breakdown with input/output/cache tokens and a total.
    Results are cached by (session_id, file_mtime) on the app state.
    """
    error = _validate_session_id(session_id)
    if error:
        return JSONResponse(status_code=400, content={"error": error})

    watcher = _get_transcript_watcher(request)
    if watcher is None:
        return JSONResponse(content={"models": {}, "total": _empty_usage()})

    file_path = watcher.find_transcript_file(session_id)
    if file_path is None:
        return JSONResponse(content={"models": {}, "total": _empty_usage()})

    import os

    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        return JSONResponse(content={"models": {}, "total": _empty_usage()})

    # Check cache
    cache: dict = getattr(request.app.state, "_token_usage_cache", None) or {}
    if not hasattr(request.app.state, "_token_usage_cache"):
        request.app.state._token_usage_cache = cache

    cache_key = session_id
    cached = cache.get(cache_key)
    if cached is not None and cached.get("mtime") == mtime:
        return JSONResponse(content=cached["data"])

    # Calculate from JSONL
    result = await asyncio.to_thread(_calc_token_usage, file_path)

    cache[cache_key] = {"mtime": mtime, "data": result}
    return JSONResponse(content=result)


def _empty_usage() -> dict:
    return {"inputTokens": 0, "outputTokens": 0, "cacheCreationTokens": 0, "cacheReadTokens": 0}


def _calc_token_usage(file_path: str) -> dict:
    """Read JSONL file and aggregate token usage per model.

    Supports two formats:
    - Claude: ``message.usage`` on each assistant entry (per-turn)
    - Copilot: ``data.modelMetrics`` on the ``session.shutdown`` entry (aggregate)
    """
    models: dict[str, dict[str, int]] = {}
    total = {"inputTokens": 0, "outputTokens": 0, "cacheCreationTokens": 0, "cacheReadTokens": 0}

    def _add(model: str, inp: int, out: int, cache_create: int, cache_read: int) -> None:
        if model not in models:
            models[model] = {"inputTokens": 0, "outputTokens": 0, "cacheCreationTokens": 0, "cacheReadTokens": 0}
        models[model]["inputTokens"] += inp
        models[model]["outputTokens"] += out
        models[model]["cacheCreationTokens"] += cache_create
        models[model]["cacheReadTokens"] += cache_read
        total["inputTokens"] += inp
        total["outputTokens"] += out
        total["cacheCreationTokens"] += cache_create
        total["cacheReadTokens"] += cache_read

    try:
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Claude format: message.usage on assistant entries
                msg = entry.get("message")
                if isinstance(msg, dict):
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        _add(
                            msg.get("model") or "unknown",
                            usage.get("input_tokens", 0) or 0,
                            usage.get("output_tokens", 0) or 0,
                            usage.get("cache_creation_input_tokens", 0) or 0,
                            usage.get("cache_read_input_tokens", 0) or 0,
                        )
                        continue

                # Copilot format: data.modelMetrics on session.shutdown
                if entry.get("type") == "session.shutdown":
                    metrics = (entry.get("data") or {}).get("modelMetrics")
                    if isinstance(metrics, dict):
                        for model_name, model_data in metrics.items():
                            usage = (model_data or {}).get("usage", {})
                            if not isinstance(usage, dict):
                                continue
                            _add(
                                model_name,
                                usage.get("inputTokens", 0) or 0,
                                usage.get("outputTokens", 0) or 0,
                                usage.get("cacheWriteTokens", 0) or 0,
                                usage.get("cacheReadTokens", 0) or 0,
                            )
    except Exception:
        logger.debug("Failed to calculate token usage for %s", file_path, exc_info=True)

    return {"models": models, "total": total}


@router.get("/api/claude-logs/transcript/{session_id}/stream")
async def stream_transcript(request: Request, session_id: str):
    """SSE live transcript stream for a specific session."""
    error = _validate_session_id(session_id)
    if error:
        return JSONResponse(status_code=400, content={"error": error})

    watcher = _get_transcript_watcher(request)
    if watcher is None:
        return JSONResponse(status_code=503, content={"error": "Transcript watcher not available"})

    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        return JSONResponse(status_code=503, content={"error": "SSE not available (sse-starlette not installed)"})

    queue: asyncio.Queue = asyncio.Queue()

    def on_transcript_event(event):
        if getattr(event, "session_id", None) != session_id:
            return
        for entry in getattr(event, "new_entries", []):
            try:
                queue.put_nowait(_serialize(entry))
            except Exception:
                pass

    watcher.subscribe(on_transcript_event)

    async def event_generator():
        try:
            yield {"event": "connected", "data": ""}
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"data": json.dumps(entry)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        except asyncio.CancelledError:
            pass
        finally:
            watcher.unsubscribe(on_transcript_event)

    return EventSourceResponse(event_generator())
