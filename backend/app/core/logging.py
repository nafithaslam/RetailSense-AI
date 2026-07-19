"""
RetailSense AI — Logging Configuration
========================================
Configures structlog for structured, levelled logging.  Integrates with
Python's standard ``logging`` module so that third-party libraries
(SQLAlchemy, uvicorn, etc.) participate in the same log pipeline.

Usage
-----
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("server.started", host="0.0.0.0", port=8000)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Bootstrap structlog and the standard logging subsystem.

    Should be called **once** during application startup (before any logger
    is obtained) so that all log records share the same configuration.
    """
    log_level: int = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    # ------------------------------------------------------------------ #
    # Standard library logging root configuration                          #
    # ------------------------------------------------------------------ #
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Silence noisy third-party loggers in production
    if settings.is_production:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    # ------------------------------------------------------------------ #
    # Shared processors (applied to every log record)                      #
    # ------------------------------------------------------------------ #
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # ------------------------------------------------------------------ #
    # Renderer — human-readable in dev, JSON in production                 #
    # ------------------------------------------------------------------ #
    if settings.is_development:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A structlog BoundLogger instance.
    """
    return structlog.get_logger(name)
