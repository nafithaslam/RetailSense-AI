"""
RetailSense AI — Base ORM Model Mixin
=======================================
Provides a reusable ``TimestampMixin`` that injects ``created_at`` and
``updated_at`` audit columns into any ORM model.

Usage
-----
    from app.database.session import Base
    from app.models.base import TimestampMixin

    class Product(TimestampMixin, Base):
        __tablename__ = "products"
        id: Mapped[int] = mapped_column(primary_key=True)
        ...
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` audit columns to an ORM model.

    Both columns are stored as timezone-aware UTC datetimes.  ``updated_at``
    is automatically refreshed by the database on every UPDATE via
    ``onupdate``.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
