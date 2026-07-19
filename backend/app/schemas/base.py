"""
RetailSense AI — Base Pydantic Schemas
========================================
Provides base schema classes that all domain schemas should inherit from.

Design decisions
-----------------
* ``BaseSchema`` — ORM-mode enabled; all field names use camelCase in JSON
  (``alias_generator=to_camel``), keeping Python code Pythonic while the API
  surface follows JavaScript / REST conventions.
* ``TimestampSchema`` — mixin that adds ``created_at`` / ``updated_at``
  read-only fields to response schemas.

Usage
-----
    from app.schemas.base import BaseSchema, TimestampSchema

    class ProductResponse(TimestampSchema, BaseSchema):
        id: int
        name: str
        price: float
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class BaseSchema(BaseModel):
    """Project-wide Pydantic base model.

    Configuration
    -------------
    * ORM mode enabled (``from_attributes=True``) so SQLAlchemy models can
      be passed directly to response_model serialisation.
    * ``alias_generator=to_camel`` converts snake_case Python attributes to
      camelCase JSON keys automatically.
    * ``populate_by_name=True`` allows both the Python name and the alias
      to be used when constructing a model instance.
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class TimestampSchema(BaseModel):
    """Mixin that exposes created_at / updated_at timestamp fields.

    Inherit alongside ``BaseSchema`` in response schemas that correspond to
    models using ``TimestampMixin``.
    """

    created_at: datetime
    updated_at: datetime
