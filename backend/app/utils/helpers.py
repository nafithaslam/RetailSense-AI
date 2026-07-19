"""
RetailSense AI — Utility Helpers
==================================
General-purpose utility functions used across the application.

Functions
---------
utc_now          — Return the current UTC datetime (timezone-aware).
paginate_query   — Apply LIMIT / OFFSET to an async SQLAlchemy query.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


def utc_now() -> datetime:
    """Return the current UTC datetime with timezone info.

    Prefer this over ``datetime.utcnow()`` which returns a naive datetime.

    Returns:
        A timezone-aware ``datetime`` in UTC.
    """
    return datetime.now(timezone.utc)


async def paginate_query(
    db: AsyncSession,
    query: Select[tuple[T]],
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[T], int]:
    """Execute a paginated SELECT and return results plus the total row count.

    Args:
        db:        An active async database session.
        query:     The base SQLAlchemy select statement (no LIMIT/OFFSET).
        page:      1-based page number.
        page_size: Number of records per page (max enforced by callers).

    Returns:
        A tuple of ``(records, total_count)`` where ``total_count`` is the
        number of rows that match the query without pagination.

    Raises:
        ValueError: If ``page`` < 1 or ``page_size`` < 1.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    # Count without pagination
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total: int = total_result.scalar_one()

    # Paginated records
    offset = (page - 1) * page_size
    paginated = query.limit(page_size).offset(offset)
    records_result = await db.execute(paginated)
    records: list[T] = list(records_result.scalars().all())

    return records, total
