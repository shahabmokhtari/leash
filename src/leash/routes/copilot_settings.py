"""Copilot hooks.json editor — GET/PUT /api/copilot-settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_settings_path() -> Path:
    return Path.home() / ".copilot" / "hooks" / "hooks.json"


@router.get("/api/copilot-settings")
async def get_settings() -> JSONResponse:
    """Read ~/.copilot/hooks/hooks.json and return its contents."""
    settings_path = _get_settings_path()
    try:
        path_str = str(settings_path)
        if not settings_path.exists():
            return JSONResponse(content={"path": path_str, "exists": False, "content": "{}"})

        async with aiofiles.open(settings_path, "r") as f:
            raw = await f.read()

        # Validate it's valid JSON
        try:
            json.loads(raw)
            return JSONResponse(content={"path": path_str, "exists": True, "content": raw})
        except json.JSONDecodeError:
            return JSONResponse(
                content={"path": path_str, "exists": True, "content": raw, "parseError": True}
            )
    except Exception as exc:
        logger.error("Failed to read Copilot settings: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to read settings: {exc}"})


@router.put("/api/copilot-settings")
async def save_settings(request: Request) -> JSONResponse:
    """Validate and save content to ~/.copilot/hooks/hooks.json."""
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

    settings_path = _get_settings_path()
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(settings_path, "w") as f:
            await f.write(pretty)
        logger.info("Copilot hooks.json updated via web UI")
        return JSONResponse(content={"saved": True, "path": str(settings_path)})
    except Exception as exc:
        logger.error("Failed to save Copilot settings: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to save: {exc}"})
