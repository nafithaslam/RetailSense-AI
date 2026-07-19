"""
RetailSense AI — Global Exception Handlers
============================================
Registers application-wide exception handlers on the FastAPI instance.
All unhandled exceptions are caught here, logged, and converted into
structured JSON error responses so that API clients always receive a
consistent error envelope.

Error Envelope Shape
--------------------
{
    "status":  "error",
    "code":    <HTTP status code>,
    "message": <human-readable description>,
    "detail":  <optional machine-readable detail or null>
}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Error response helper                                                         #
# --------------------------------------------------------------------------- #

def _error_response(
    status_code: int,
    message: str,
    detail: Any = None,
) -> JSONResponse:
    """Build a standardised JSON error response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "code": status_code,
            "message": message,
            "detail": detail,
        },
    )


# --------------------------------------------------------------------------- #
# Individual handlers                                                           #
# --------------------------------------------------------------------------- #

async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle FastAPI / Starlette HTTPException."""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert isinstance(exc, StarletteHTTPException)

    logger.warning(
        "http.exception",
        path=str(request.url),
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return _error_response(
        status_code=exc.status_code,
        message=str(exc.detail),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic request body / query-param validation errors (422)."""
    errors = exc.errors()
    logger.warning(
        "request.validation_error",
        path=str(request.url),
        errors=errors,
    )
    return _error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Request validation failed.",
        detail=errors,
    )


async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Handle unexpected database errors."""
    logger.error(
        "database.error",
        path=str(request.url),
        error=str(exc),
        exc_info=True,
    )
    return _error_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        message="A database error occurred. Please try again later.",
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler for any unhandled exception."""
    logger.error(
        "unhandled.exception",
        path=str(request.url),
        error=str(exc),
        exc_info=True,
    )
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="An unexpected error occurred. Please try again later.",
    )


# --------------------------------------------------------------------------- #
# Registration                                                                  #
# --------------------------------------------------------------------------- #

def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI application instance.

    Args:
        app: The FastAPI application to configure.
    """
    from fastapi.exceptions import HTTPException
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]
