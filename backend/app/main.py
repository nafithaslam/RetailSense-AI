"""
RetailSense AI — Application Entry Point
==========================================
Bootstraps the FastAPI application, registers middleware, mounts routers,
and wires up the async database lifecycle.

Running locally
---------------
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Swagger UI is available at:  http://localhost:8000/docs
ReDoc is available at:       http://localhost:8000/redoc
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.database.session import engine

# Initialise logging before anything else so all startup messages are captured
configure_logging()
logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Lifespan — startup / shutdown                                                 #
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown events.

    Startup
    -------
    * Log environment and connection pool configuration.
    * (Future) warm up caches, start background workers, etc.

    Shutdown
    --------
    * Dispose the SQLAlchemy connection pool gracefully.
    """
    # --- Startup ---
    logger.info(
        "application.startup",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        debug=settings.DEBUG,
    )

    yield  # Application is running

    # --- Shutdown ---
    logger.info("application.shutdown", app_name=settings.APP_NAME)
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Application factory                                                           #
# --------------------------------------------------------------------------- #

def create_application() -> FastAPI:
    """Construct and configure the FastAPI application instance.

    Returns:
        A fully configured ``FastAPI`` application ready to be served.
    """
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "RetailSense AI — Production-quality retail management platform.\n\n"
            "This API powers inventory management, sales analytics, supplier "
            "management, and AI-driven retail insights."
        ),
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ #
    # Middleware                                                            #
    # ------------------------------------------------------------------ #

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Exception handlers                                                    #
    # ------------------------------------------------------------------ #

    register_exception_handlers(application)

    # ------------------------------------------------------------------ #
    # Routers                                                              #
    # ------------------------------------------------------------------ #

    application.include_router(api_router)

    return application


# --------------------------------------------------------------------------- #
# Module-level ASGI app (used by uvicorn)                                      #
# --------------------------------------------------------------------------- #

app: FastAPI = create_application()
