"""Terminal output buffer and SSE stream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_terminal_output(request: Request) -> Any:
    return getattr(request.app.state, "terminal_output_service", None)


@router.get("/api/terminal/buffer")
async def get_buffer(request: Request) -> JSONResponse:
    """Return the current terminal output buffer."""
    svc = _get_terminal_output(request)
    if svc is None:
        return JSONResponse(content=[])

    lines = svc.get_buffer()
    serialized = [dataclasses.asdict(line) if dataclasses.is_dataclass(line) else line for line in lines]
    # Use default=str to handle datetime and other non-serializable types
    body = json.dumps(serialized, default=str)
    return JSONResponse(content=json.loads(body))


@router.post("/api/terminal/clear")
async def clear_buffer(request: Request) -> JSONResponse:
    """Clear the terminal output buffer."""
    svc = _get_terminal_output(request)
    if svc is not None:
        svc.clear()
    return JSONResponse(content={"cleared": True})


@router.get("/api/terminal/stream")
async def stream_terminal(request: Request):
    """SSE terminal output stream."""
    svc = _get_terminal_output(request)
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "Terminal output service not available"})

    try:
        from sse_starlette.sse import EventSourceResponse

        queue: asyncio.Queue = asyncio.Queue()

        def on_line_received(line):
            try:
                queue.put_nowait(line)
            except Exception:
                pass

        svc.subscribe(on_line_received)

        async def event_generator():
            try:
                yield {"event": "connected", "data": ""}
                while True:
                    try:
                        line = await asyncio.wait_for(queue.get(), timeout=30.0)
                        if hasattr(line, "__dataclass_fields__"):
                            import dataclasses
                            data = json.dumps(dataclasses.asdict(line), default=str)
                        elif isinstance(line, dict):
                            data = json.dumps(line)
                        else:
                            data = json.dumps({
                                "source": getattr(line, "source", ""),
                                "level": getattr(line, "level", ""),
                                "text": str(line),
                            })
                        yield {"data": data}
                    except asyncio.TimeoutError:
                        yield {"comment": "keepalive"}
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    svc.unsubscribe(on_line_received)
                except Exception:
                    pass

        return EventSourceResponse(event_generator())
    except ImportError:
        return JSONResponse(status_code=503, content={"error": "SSE not available"})
