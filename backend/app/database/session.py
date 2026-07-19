"""
RetailSense AI — Async Database Engine & Session Factory
=========================================================
Sets up SQLAlchemy's async engine backed by asyncpg (PostgreSQL).

Key design decisions
---------------------
* Async-first — all I/O with the database is non-blocking.
* One engine per process — the engine is a module-level singleton.
* Session-per-request — ``get_db`` is a FastAPI dependency that opens a
  session for each incoming request and guarantees it is closed afterwards,
  even if an exception is raised.
* Declarative base — all ORM models should inherit from ``Base``.

Usage
-----
    # In a FastAPI route
    from app.database.session import get_db
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/example")
    async def example(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(MyModel))
        ...
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Engine                                                                        #
# --------------------------------------------------------------------------- #

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT,
    echo=settings.DEBUG,           # Log SQL statements when DEBUG=true
    future=True,
)

# --------------------------------------------------------------------------- #
# Session factory                                                               #
# --------------------------------------------------------------------------- #

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,        # Avoids lazy-load issues after commit
    autoflush=False,
    autocommit=False,
)


# --------------------------------------------------------------------------- #
# Declarative base — all ORM models inherit from this                          #
# --------------------------------------------------------------------------- #

class Base(DeclarativeBase):
    """Project-wide SQLAlchemy declarative base.

    All ORM model classes must inherit from ``Base`` so that the metadata
    registry is shared and ``Base.metadata.create_all()`` / Alembic can
    discover every table.
    """


# --------------------------------------------------------------------------- #
# FastAPI dependency                                                            #
# --------------------------------------------------------------------------- #

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session; auto-rollback on error.

    Intended to be used as a FastAPI ``Depends`` injection.

    Example::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
