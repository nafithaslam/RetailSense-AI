"""
RetailSense AI — models package

Import all ORM models here so that Alembic (and Base.metadata) can
discover every table automatically.  Each import registers the model's
table in ``Base.metadata``, making it visible to Alembic autogenerate.
"""

from app.models.user import User, UserRole  # noqa: F401

__all__ = ["User", "UserRole"]
