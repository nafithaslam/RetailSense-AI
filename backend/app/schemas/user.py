"""
RetailSense AI — User Pydantic Schemas
========================================
Defines the request/response contracts for all User-related API operations.

Schema hierarchy
-----------------
``UserBase``          — shared fields (validation lives here once, used by all)
  └── ``UserCreate``  — inbound: registration payload (includes raw password)
  └── ``UserLogin``   — inbound: login payload (email + password only)
``UserResponse``      — outbound: safe public representation (no password hash)

Design decisions
-----------------
* Raw ``password`` is accepted on input (``UserCreate``); the service layer
  hashes it via bcrypt before touching the database.  ``password_hash`` is
  **never** exposed in any response schema.
* ``email`` is normalised to lower-case in the validator so that
  "User@Example.COM" and "user@example.com" are treated as the same address.
* ``UserResponse`` inherits ``TimestampSchema`` so API consumers always get
  ``createdAt`` / ``updatedAt`` in camelCase (via ``BaseSchema``).
* All schemas inherit ``BaseSchema`` which enables ORM-mode and camelCase
  JSON aliases automatically.

Usage
-----
    from app.schemas.user import UserCreate, UserResponse, UserLogin
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.base import BaseSchema, TimestampSchema


# --------------------------------------------------------------------------- #
# Shared base                                                                   #
# --------------------------------------------------------------------------- #

class UserBase(BaseSchema):
    """Fields that are common to both creation and response schemas.

    Validation
    ----------
    * ``full_name`` — stripped of leading/trailing whitespace.
    * ``email``     — lower-cased and validated as a proper e-mail address.
    * ``role``      — restricted to the ``UserRole`` enum values.
    """

    full_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        examples=["Jane Smith"],
        description="Full display name of the user.",
    )

    email: EmailStr = Field(
        ...,
        examples=["jane.smith@example.com"],
        description="Unique email address. Used for login.",
    )

    role: UserRole = Field(
        default=UserRole.STAFF,
        examples=[UserRole.STAFF],
        description="Access role assigned to the user.",
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        """Remove surrounding whitespace from the display name."""
        return value.strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lowercase the email so lookups are always case-insensitive."""
        return value.strip().lower()


# --------------------------------------------------------------------------- #
# Request schemas (inbound)                                                     #
# --------------------------------------------------------------------------- #

class UserCreate(UserBase):
    """Schema for creating a new user account.

    The raw ``password`` field is accepted here.  It is **never** stored
    directly; the service layer hashes it before writing to the database.

    Password constraints
    --------------------
    * Minimum 8 characters.
    * Maximum 128 characters (bcrypt truncates beyond 72 bytes; we're
      conservative).
    """

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        examples=["S3cur3P@ssword!"],
        description="Plain-text password. Will be hashed before storage.",
    )


class UserLogin(BaseSchema):
    """Schema for authenticating an existing user.

    Only ``email`` and ``password`` are required — no other fields.
    Role, name, etc. are resolved server-side after successful auth.
    """

    email: EmailStr = Field(
        ...,
        examples=["jane.smith@example.com"],
        description="Registered email address.",
    )

    password: str = Field(
        ...,
        min_length=1,
        examples=["S3cur3P@ssword!"],
        description="Plain-text password for verification.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lowercase the email to match the stored normalised value."""
        return value.strip().lower()


# --------------------------------------------------------------------------- #
# Response schema (outbound)                                                    #
# --------------------------------------------------------------------------- #

class UserResponse(TimestampSchema, UserBase):
    """Safe public representation of a user — no password hash exposed.

    Inherits
    --------
    * ``UserBase``       — full_name, email, role (with validators).
    * ``TimestampSchema`` — created_at, updated_at.
    * ``BaseSchema``     — ORM-mode, camelCase JSON aliases (via UserBase).

    The ``id`` field is a UUID serialised as a string in JSON responses.
    The ``is_active`` flag lets clients show/hide deactivated accounts.
    """

    id: uuid.UUID = Field(
        ...,
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
        description="Unique identifier for the user.",
    )

    is_active: bool = Field(
        ...,
        examples=[True],
        description="Whether the user account is active.",
    )


# --------------------------------------------------------------------------- #
# Admin-only request schemas                                                    #
# --------------------------------------------------------------------------- #


class AdminCreateRequest(BaseSchema):
    """Payload for creating a new user with an explicit role assignment.

    This schema is used exclusively by the admin-only ``POST /users/``
    endpoint.  Unlike :class:`~app.schemas.auth.RegisterRequest` (public
    self-registration), this schema allows the caller to specify any
    :class:`~app.models.user.UserRole` because the route is guarded by
    ``AdminOnly``.

    Attributes
    ----------
    full_name:
        Display name of the new account holder.
    email:
        Unique email address.  Lower-cased before any DB interaction.
    password:
        Plain-text password.  Will be hashed before storage.
    role:
        Explicit access tier.  Required — the admin must consciously choose
        the role rather than relying on a default.
    """

    full_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        examples=["Jane Smith"],
        description="Full display name of the new user.",
    )

    email: EmailStr = Field(
        ...,
        examples=["jane.smith@example.com"],
        description="Unique email address.  Used for login.",
    )

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        examples=["S3cur3P@ssword!"],
        description="Plain-text password.  Will be hashed before storage.",
    )

    role: UserRole = Field(
        ...,
        examples=[UserRole.STAFF],
        description="Explicit access role for the new account.",
    )

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        """Remove surrounding whitespace from the display name."""
        return value.strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lowercase and strip the email to match stored normalised value."""
        return value.strip().lower()


class RoleAssignRequest(BaseSchema):
    """Payload for assigning a new role to an existing user.

    Used by the admin-only ``PATCH /users/{user_id}/role`` endpoint.

    Attributes
    ----------
    role:
        The new :class:`~app.models.user.UserRole` to assign.
    """

    role: UserRole = Field(
        ...,
        examples=[UserRole.MANAGER],
        description="The new role to assign to the target user.",
    )


# --------------------------------------------------------------------------- #
# Paginated list response                                                       #
# --------------------------------------------------------------------------- #


class PaginatedUsersResponse(BaseSchema):
    """Envelope returned by ``GET /users/`` containing a paginated user list.

    Attributes
    ----------
    total:
        Total number of users in the database (across all pages).
    page:
        Current (1-indexed) page number.
    page_size:
        Number of items per page (as requested, capped at the server maximum).
    items:
        The user records for this page.  Empty list on the last page when
        the total is exactly divisible by ``page_size``.
    """

    total: int = Field(
        ...,
        ge=0,
        description="Total number of users across all pages.",
        examples=[42],
    )

    page: int = Field(
        ...,
        ge=1,
        description="Current page number (1-indexed).",
        examples=[1],
    )

    page_size: int = Field(
        ...,
        ge=1,
        description="Number of items returned per page.",
        examples=[20],
    )

    items: list[UserResponse] = Field(
        ...,
        description="User records for the current page.",
    )
