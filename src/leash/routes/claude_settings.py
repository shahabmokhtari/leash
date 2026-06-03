"""Claude settings.json editor — GET/PUT /api/claude-settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


@router.get("/api/claude-settings")
async def get_settings() -> JSONResponse:
    """Read ~/.claude/settings.json and return its contents."""
    try:
        settings_path = str(SETTINGS_PATH)
        if not SETTINGS_PATH.exists():
            return JSONResponse(content={"path": settings_path, "exists": False, "content": "{}"})

        async with aiofiles.open(SETTINGS_PATH, "r") as f:
            raw = await f.read()

        # Validate it's valid JSON
        try:
            json.loads(raw)
            return JSONResponse(content={"path": settings_path, "exists": True, "content": raw})
        except json.JSONDecodeError:
            return JSONResponse(
                content={"path": settings_path, "exists": True, "content": raw, "parseError": True}
            )
    except Exception as exc:
        logger.error("Failed to read Claude settings: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to read settings: {exc}"})


@router.put("/api/claude-settings")
async def save_settings(request: Request) -> JSONResponse:
    """Validate and save content to ~/.claude/settings.json."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    content = body.get("content") if isinstance(body, dict) else None
    if content is None:
        return JSONResponse(status_code=400, content={"error": "content field is required"})
    if not isinstance(content, str):
        return JSONResponse(status_code=400, content={"error": "content must be a string"})

    # Validate JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid JSON: {exc}"})

    if parsed is None:
        return JSONResponse(status_code=400, content={"error": "content is not valid JSON"})

    # Pretty-print
    pretty = json.dumps(parsed, indent=2)

    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(SETTINGS_PATH, "w") as f:
            await f.write(pretty)
        logger.info("Claude settings.json updated via web UI")
        return JSONResponse(content={"saved": True, "path": str(SETTINGS_PATH)})
    except Exception as exc:
        logger.error("Failed to save Claude settings: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to save: {exc}"})
