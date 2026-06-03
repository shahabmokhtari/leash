"""Insights (smart suggestions) endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_insights_engine(request: Request) -> Any:
    return getattr(request.app.state, "insights_engine", None)


@router.get("/api/insights")
async def get_insights(
    request: Request,
    includeAll: bool = Query(default=False),
) -> JSONResponse:
    """Return smart suggestions/insights."""
    engine = _get_insights_engine(request)
    if engine is None:
        return JSONResponse(
            content={
                "insights": [],
                "count": 0,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
            }
        )

    insights = engine.get_insights(includeAll)
    # Serialize insights
    serialized = []
    for i in insights:
        if hasattr(i, "model_dump"):
            serialized.append(i.model_dump(by_alias=True))
        elif isinstance(i, dict):
            serialized.append(i)
        else:
            serialized.append(str(i))

    return JSONResponse(
        content={
            "insights": serialized,
            "count": len(serialized),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.post("/api/insights/dismiss/{insight_id}")
async def dismiss_insight(request: Request, insight_id: str) -> JSONResponse:
    """Dismiss an insight by ID."""
    engine = _get_insights_engine(request)
    if engine is None:
        return JSONResponse(status_code=503, content={"error": "Insights engine not available"})

    engine.dismiss_insight(insight_id)
    return JSONResponse(content={"dismissed": True})


@router.post("/api/insights/regenerate")
async def regenerate(request: Request) -> JSONResponse:
    """Regenerate all insights."""
    engine = _get_insights_engine(request)
    if engine is None:
        return JSONResponse(status_code=503, content={"error": "Insights engine not available"})

    engine.regenerate_insights()
    insights = engine.get_insights()

    serialized = []
    for i in insights:
        if hasattr(i, "model_dump"):
            serialized.append(i.model_dump(by_alias=True))
        elif isinstance(i, dict):
            serialized.append(i)
        else:
            serialized.append(str(i))

    return JSONResponse(
        content={
            "insights": serialized,
            "count": len(serialized),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
