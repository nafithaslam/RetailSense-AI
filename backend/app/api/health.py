"""
RetailSense AI — Health Check Router
======================================
Provides the ``GET /health`` endpoint required by orchestration platforms
(Kubernetes liveness probes, load balancers, uptime monitors, etc.).

Endpoints
---------
GET /health
    Returns a lightweight status payload.  Does NOT touch the database so
    that network issues never cause a false "unhealthy" at the application
    layer — add a ``/health/db`` probe separately if DB readiness is required.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Health check",
    description=(
        "Returns the application health status. "
        "Used by load-balancers and container orchestrators."
    ),
    response_description="Application is healthy and accepting traffic.",
)
async def health_check() -> JSONResponse:
    """Return a simple health status.

    Response
    --------
    ```json
    {
        "status": "healthy",
        "application": "RetailSense AI"
    }
    ```
    """
    return JSONResponse(
        content={
            "status": "healthy",
            "application": settings.APP_NAME,
        }
    )
