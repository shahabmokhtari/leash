"""Prompt template CRUD endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_prompt_service(request: Request) -> Any:
    return getattr(request.app.state, "prompt_template_service", None)


@router.get("/api/prompts")
async def get_all_templates(request: Request) -> JSONResponse:
    """Return all prompt templates as a dict of name -> content."""
    svc = _get_prompt_service(request)
    if svc is None:
        return JSONResponse(content={})
    templates = svc.get_all_templates()
    return JSONResponse(content=templates)


@router.get("/api/prompts/names")
async def get_template_names(request: Request) -> JSONResponse:
    """Return a list of available template names."""
    svc = _get_prompt_service(request)
    if svc is None:
        return JSONResponse(content=[])
    names = svc.get_template_names()
    return JSONResponse(content=names)


@router.get("/api/prompts/{template_name}")
async def get_template(request: Request, template_name: str) -> JSONResponse:
    """Return a single prompt template by name."""
    if not template_name or not template_name.strip():
        return JSONResponse(status_code=400, content={"error": "Template name is required"})
    if ".." in template_name or "/" in template_name or "\\" in template_name:
        return JSONResponse(status_code=400, content={"error": "Invalid template name"})

    svc = _get_prompt_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Prompt template service not available"})

    template = svc.get_template(template_name)
    if template is None:
        return JSONResponse(status_code=404, content={"error": f"Template '{template_name}' not found"})

    return JSONResponse(content={"name": template_name, "content": template})


@router.put("/api/prompts/{template_name}")
async def save_template(request: Request, template_name: str) -> JSONResponse:
    """Save or update a prompt template."""
    if not template_name or not template_name.strip():
        return JSONResponse(status_code=400, content={"error": "Template name is required"})
    if ".." in template_name or "/" in template_name or "\\" in template_name:
        return JSONResponse(status_code=400, content={"error": "Invalid template name"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    content = body.get("content") if isinstance(body, dict) else None
    if not content:
        return JSONResponse(status_code=400, content={"error": "Template content is required"})

    svc = _get_prompt_service(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Prompt template service not available"})

    success = await svc.save_template(template_name, content)
    if not success:
        return JSONResponse(status_code=500, content={"error": "Failed to save template"})

    return JSONResponse(content={"message": "Template saved successfully"})
