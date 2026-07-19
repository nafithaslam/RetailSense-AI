"""
RetailSense AI — API router registry
======================================
Aggregates all versioned route modules and mounts them onto the main
FastAPI application.  New feature routers should be imported and included
here under the appropriate API version prefix.

Pattern
-------
    from app.api.v1 import some_feature
    api_router.include_router(
        some_feature.router,
        prefix="/api/v1/some-feature",
        tags=["Some Feature"],
    )
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import health
from app.api.v1 import auth as auth_v1
from app.api.v1 import users as users_v1

# Root-level (unversioned) router — health check lives here
api_router = APIRouter()

api_router.include_router(health.router)

# ---------------------------------------------------------------------------
# v1 routes
# ---------------------------------------------------------------------------

api_router.include_router(
    auth_v1.router,
    prefix="/api/v1",
)

api_router.include_router(
    users_v1.router,
    prefix="/api/v1",
)

# ---------------------------------------------------------------------------
# Future v1 routes — add feature routers below as the project grows
# ---------------------------------------------------------------------------
# from app.api.v1 import products
# api_router.include_router(products.router, prefix="/api/v1/products", tags=["Products"])
