"""
RetailSense AI — Authentication Pydantic Schemas
==================================================
Defines the request/response contracts that are specific to the
authentication flow.  These schemas are intentionally kept separate from
``app.schemas.user`` so that user-management concerns do not bleed into
the auth surface.

Schema overview
---------------
Request (inbound)
  * :class:`LoginRequest`     — email + password credentials for sign-in.
  * :class:`RegisterRequest`  — full registration payload (name, email, password,
                                optional role).

Response (outbound)
  * :class:`Token`            — the envelope returned after a successful auth
                                operation (access token + metadata).
  * :class:`RefreshToken`     — carries a refresh token string for the token-
                                rotation endpoint (future sprint).
  * :class:`AuthenticatedUser` — lightweight public representation of the
                                 principal attached to a valid token.

Internal / service-layer
  * :class:`TokenPayload`     — typed view of the decoded JWT claims; used
                                inside the auth dependency (never serialised
                                to clients directly).

Design decisions
-----------------
* All auth schemas inherit :class:`~app.schemas.base.BaseSchema` to get ORM
  mode, camelCase aliases, and ``populate_by_name`` for free.
* ``LoginRequest`` / ``RegisterRequest`` perform email normalisation at the
  schema layer so callers (services, tests) never have to remember to do it.
* ``Token.token_type`` is hard-coded to ``"bearer"`` — this is the only
  token type the platform supports and it avoids an accidental mismatch
  between service and route layer.
* ``TokenPayload`` is *not* a response schema; it is a pure internal DTO.
  ``sub`` is kept as a ``str`` because ``python-jose`` decodes it that way
  and callers that need a UUID can parse it themselves.

Usage
-----
    from app.schemas.auth import LoginRequest, RegisterRequest, Token
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.base import BaseSchema


# --------------------------------------------------------------------------- #
# Request schemas (inbound)                                                     #
# --------------------------------------------------------------------------- #


class LoginRequest(BaseSchema):
    """Credentials submitted by a user during sign-in.

    Attributes
    ----------
    email:
        The registered email address.  Normalised to lower-case so that
        ``"User@EXAMPLE.com"`` and ``"user@example.com"`` resolve to the
        same account.
    password:
        Plain-text password.  Validated only for presence; the service
        layer performs the constant-time Argon2 comparison.
    """

    email: EmailStr = Field(
        ...,
        examples=["jane.smith@example.com"],
        description="Registered email address used for login.",
    )

    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        examples=["S3cur3P@ssword!"],
        description="Plain-text password for credential verification.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Strip and lower-case the email to match the stored normalised value."""
        return value.strip().lower()


class RegisterRequest(BaseSchema):
    """Payload required to create a new user account.

    This schema mirrors ``app.schemas.user.UserCreate`` but lives in the auth
    module so that the auth service has a single, authoritative input type
    without a circular dependency on the user schema.

    Password constraints
    --------------------
    * Minimum 8 characters — enforced here and documented in OpenAPI.
    * Maximum 128 characters — conservative cap (Argon2 supports unlimited
      lengths, but extremely long passwords enable DoS via CPU exhaustion).

    Attributes
    ----------
    full_name:
        Display name of the new account holder.
    email:
        Unique email address.  Lower-cased before any DB interaction.
    password:
        Plain-text password.  **Never** stored; the auth service hashes it
        via :func:`~app.core.security.hash_password` before writing.
    role:
        Access tier for the new account.  Defaults to ``STAFF`` (least
        privilege) — only an ``ADMIN`` caller should override this.
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
        default=UserRole.STAFF,
        examples=[UserRole.STAFF],
        description="Access role for the new account.  Defaults to STAFF.",
    )

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        """Remove surrounding whitespace from the display name."""
        return value.strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Lower-case and strip the email address."""
        return value.strip().lower()


# --------------------------------------------------------------------------- #
# Response schemas (outbound)                                                   #
# --------------------------------------------------------------------------- #


class Token(BaseSchema):
    """Envelope returned to the client after a successful authentication.

    Attributes
    ----------
    access_token:
        A signed JWT string.  The client must include this in the
        ``Authorization: Bearer <token>`` header on subsequent requests.
    token_type:
        Always ``"bearer"``.  Included for OAuth 2.0 / RFC 6750
        compatibility.
    expires_in:
        Number of seconds until the access token expires, so that clients
        can schedule a refresh without parsing the JWT themselves.

    Example JSON response::

        {
            "accessToken": "eyJhbGci...",
            "tokenType": "bearer",
            "expiresIn": 1800
        }
    """

    access_token: str = Field(
        ...,
        description="Signed JWT access token.",
        examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."],
    )

    token_type: str = Field(
        default="bearer",
        description="Token type.  Always 'bearer' for this API.",
        examples=["bearer"],
    )

    expires_in: int = Field(
        ...,
        description="Seconds until the access token expires.",
        examples=[1800],
    )


class RefreshToken(BaseSchema):
    """Carries a refresh-token string for the token-rotation endpoint.

    This schema is reserved for the future refresh-token flow (Sprint 3.x).
    The ``refresh_token`` field contains an opaque string (or a separate
    JWT) that the client presents to obtain a new access token without
    re-entering credentials.

    Attributes
    ----------
    refresh_token:
        An opaque token string that can be exchanged for a new access
        token.  Treat it like a password — store securely (HttpOnly cookie
        or secure storage), never in localStorage.
    """

    refresh_token: str = Field(
        ...,
        description="Opaque refresh token for obtaining a new access token.",
        examples=["dGhpcyBpcyBhIHJlZnJlc2ggdG9rZW4..."],
    )


class AuthenticatedUser(BaseSchema):
    """Lightweight public representation of the principal behind a valid token.

    Returned alongside the :class:`Token` envelope so that clients can
    populate a user-context store (e.g. Redux / Zustand) immediately after
    login without a second round-trip to ``GET /users/me``.

    Attributes
    ----------
    id:
        The user's UUID primary key.
    full_name:
        Display name.
    email:
        Normalised (lower-case) email address.
    role:
        The access tier assigned to this account.
    is_active:
        Whether the account is currently enabled.

    Note: ``password_hash`` and audit timestamps are intentionally excluded
    from this schema.
    """

    id: uuid.UUID = Field(
        ...,
        description="Unique identifier for the authenticated user.",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )

    full_name: str = Field(
        ...,
        description="Display name of the authenticated user.",
        examples=["Jane Smith"],
    )

    email: EmailStr = Field(
        ...,
        description="Normalised email address of the authenticated user.",
        examples=["jane.smith@example.com"],
    )

    role: UserRole = Field(
        ...,
        description="Access role assigned to this account.",
        examples=[UserRole.STAFF],
    )

    is_active: bool = Field(
        ...,
        description="Whether the user account is currently active.",
        examples=[True],
    )


# --------------------------------------------------------------------------- #
# Internal / service-layer DTOs                                                 #
# --------------------------------------------------------------------------- #


class TokenPayload(BaseSchema):
    """Typed representation of a decoded JWT payload.

    This is an *internal* DTO — it is populated by
    :func:`~app.core.security.decode_token` and consumed by the auth
    dependency that resolves the current user.  It is **never** serialised
    and returned to API clients.

    Attributes
    ----------
    sub:
        Subject claim — the user's UUID as a plain string (the form stored
        by ``python-jose``).  Parse to :class:`~uuid.UUID` when you need
        the typed version: ``uuid.UUID(payload.sub)``.
    iat:
        Issued-at timestamp (UTC epoch seconds).
    exp:
        Expiry timestamp (UTC epoch seconds).
    """

    sub: str = Field(
        ...,
        description="Subject claim — the user UUID as a string.",
    )

    iat: datetime = Field(
        ...,
        description="Issued-at timestamp (UTC).",
    )

    exp: datetime = Field(
        ...,
        description="Expiry timestamp (UTC).",
    )
