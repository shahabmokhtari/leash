"""Debug LLM replay/raw endpoint — POST /api/debug/llm."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_llm_client_provider(request: Request) -> Any:
    return getattr(request.app.state, "llm_client_provider", None)


def _get_prompt_service(request: Request) -> Any:
    return getattr(request.app.state, "prompt_template_service", None)


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


def _get_terminal_output(request: Request) -> Any:
    return getattr(request.app.state, "terminal_output_service", None)


@router.post("/api/debug/llm")
async def debug_llm(request: Request) -> JSONResponse:
    """Replay a log entry through LLM or send a raw prompt."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    raw_prompt = body.get("rawPrompt")
    tool_name = body.get("toolName")
    tool_input = body.get("toolInput")
    prompt_template_name = body.get("promptTemplate")
    session_id = body.get("sessionId")
    cwd = body.get("cwd")

    prompt_service = _get_prompt_service(request)
    session_manager = _get_session_manager(request)
    terminal_output = _get_terminal_output(request)

    if raw_prompt:
        # Raw mode: use prompt directly
        prompt = raw_prompt
    else:
        # Replay mode: reconstruct prompt from log entry data
        template_content: str | None = None
        if prompt_template_name and prompt_service is not None:
            template_content = prompt_service.get_template(prompt_template_name)

        session_context: str | None = None
        if session_id and session_manager is not None:
            try:
                session_context = await session_manager.build_context(session_id)
            except Exception as exc:
                logger.warning("Failed to build session context for debug request: %s", exc)

        # Build prompt from template + context
        prompt_builder = getattr(request.app.state, "prompt_builder", None)
        if prompt_builder is not None:
            prompt = prompt_builder.build(template_content, tool_name, cwd, tool_input, session_context)
        elif template_content:
            # Simple fallback: substitute placeholders
            prompt = template_content
            if tool_name:
                prompt = prompt.replace("{{tool_name}}", tool_name)
            if cwd:
                prompt = prompt.replace("{{cwd}}", cwd)
            if tool_input:
                import json

                prompt = prompt.replace("{{tool_input}}", json.dumps(tool_input))
            if session_context:
                prompt = prompt.replace("{{session_context}}", session_context)
        else:
            prompt = f"Analyze tool: {tool_name}, input: {tool_input}"

    if terminal_output is not None:
        try:
            terminal_output.push("debug", "info", "--- Debug LLM Request ---")
            preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
            terminal_output.push("debug", "stdout", f"Prompt: {preview}")
        except Exception:
            logger.debug("Failed to push debug terminal output", exc_info=True)

    llm_provider = _get_llm_client_provider(request)
    if llm_provider is None:
        return JSONResponse(
            content={
                "success": False,
                "safetyScore": 0,
                "reasoning": None,
                "category": None,
                "error": "LLM client provider not available",
                "elapsedMs": 0,
                "promptUsed": prompt,
            }
        )

    start = time.monotonic()
    try:
        llm_response = await llm_provider.query(prompt)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        if terminal_output is not None:
            try:
                terminal_output.push("debug", "stderr", f"Debug LLM error: {exc}")
            except Exception:
                logger.debug("Failed to push debug terminal output", exc_info=True)
        return JSONResponse(
            content={
                "success": False,
                "safetyScore": 0,
                "reasoning": None,
                "category": None,
                "error": str(exc),
                "elapsedMs": elapsed,
                "promptUsed": prompt,
            }
        )

    elapsed = int((time.monotonic() - start) * 1000)

    if terminal_output is not None:
        try:
            terminal_output.push(
                "debug",
                "info",
                f"Debug result: score={getattr(llm_response, 'safety_score', 0)} "
                f"category={getattr(llm_response, 'category', 'unknown')} elapsed={elapsed}ms",
            )
        except Exception:
            logger.debug("Failed to push debug terminal output", exc_info=True)

    return JSONResponse(
        content={
            "success": getattr(llm_response, "success", False),
            "safetyScore": getattr(llm_response, "safety_score", 0),
            "reasoning": getattr(llm_response, "reasoning", None),
            "category": getattr(llm_response, "category", None),
            "error": getattr(llm_response, "error", None),
            "elapsedMs": elapsed,
            "promptUsed": prompt,
        }
    )
