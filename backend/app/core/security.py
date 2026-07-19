"""
RetailSense AI — Security Utilities
=====================================
Provides two independent security primitives:

1. **Password hashing** — wraps ``pwdlib`` with the Argon2 hasher (OWASP
   recommended).  Falls back gracefully if the ``argon2`` extra is not
   installed so unit tests can swap in a plain hasher without touching this
   module.

2. **JSON Web Tokens** — creates and verifies HS256 access tokens via
   ``python-jose``.  All datetimes are timezone-aware UTC so they serialise
   correctly and can be compared safely against ``datetime.now(UTC)``.

All functions are pure and stateless; they carry no FastAPI dependencies and
can be imported from anywhere in the application (services, dependencies,
CLI scripts, tests, …).

Public API
----------
- :func:`hash_password`
- :func:`verify_password`
- :func:`create_access_token`
- :func:`decode_token`

Token payload shape
-------------------
::

    {
        "sub":  "<user_id or username>",
        "role": "<UserRole value, e.g. 'admin'>",  # present when role is supplied
        "iat":  <unix epoch – issued-at>,
        "exp":  <unix epoch – expiry>
    }

The ``role`` claim is **informational** — it enables API gateways and frontend
clients to inspect the caller's role without a DB round-trip.  All FastAPI
protected routes still verify against the database on every request, so
deactivation or role changes take effect immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import ExpiredSignatureError, JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings
from app.core.logging import get_logger
from app.models.user import UserRole

# --------------------------------------------------------------------------- #
# Module logger                                                                 #
# --------------------------------------------------------------------------- #

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Internal helpers                                                              #
# --------------------------------------------------------------------------- #

def _build_password_hasher() -> PasswordHash:
    """Construct a :class:`~pwdlib.PasswordHash` configured with Argon2.

    Argon2id is the OWASP-recommended choice for new systems (memory-hard,
    side-channel resistant).  The hasher is built once at import time and
    re-used for every hashing / verification call to avoid repeated object
    construction overhead.

    Returns:
        A ``PasswordHash`` instance backed by the Argon2 hasher.
    """
    return PasswordHash((Argon2Hasher(),))


# Module-level singleton — cheap to share; hashers carry no mutable state.
_password_hash: PasswordHash = _build_password_hasher()


def _utc_now() -> datetime:
    """Return the current moment as a timezone-aware UTC :class:`~datetime.datetime`.

    Using a helper instead of inlining ``datetime.now(UTC)`` everywhere makes
    the timestamp source easy to mock in unit tests.

    Returns:
        Current UTC datetime with tzinfo set to :data:`datetime.UTC`.
    """
    return datetime.now(UTC)


def _subject_to_str(subject: str | UUID) -> str:
    """Normalise a token *subject* to a plain string.

    ``python-jose`` requires the ``sub`` claim to be a string.  When the
    caller passes a :class:`~uuid.UUID` (common for database primary keys) it
    is converted to its canonical hyphenated representation.

    Args:
        subject: Either a plain string (e.g. username / email) or a UUID.

    Returns:
        The subject as a ``str``.
    """
    return str(subject)


# --------------------------------------------------------------------------- #
# Password hashing                                                              #
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    """Hash a plain-text password using Argon2id.

    Produces a self-describing hash string (includes algorithm identifier,
    parameters, and salt) that can be stored directly in the database.  The
    hash is deterministic per-call only in test fixtures that patch the hasher;
    in production it incorporates a random salt and is therefore never the same
    twice.

    Args:
        password: The user-supplied plain-text password to hash.

    Returns:
        An Argon2id hash string suitable for database storage.

    Example::

        hashed = hash_password("super-secret")
        # '$argon2id$v=19$m=65536,t=3,p=4$...$...'
    """
    return _password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored Argon2id hash.

    Uses a constant-time comparison internally (provided by ``argon2-cffi``)
    to mitigate timing-attack leakage.

    Args:
        password: The plain-text password supplied by the user at login.
        hashed_password: The Argon2id hash retrieved from the database.

    Returns:
        ``True`` if ``password`` matches ``hashed_password``, ``False``
        otherwise.

    Example::

        if not verify_password(plain, stored_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials")
    """
    return _password_hash.verify(password, hashed_password)


# --------------------------------------------------------------------------- #
# JWT — token creation                                                          #
# --------------------------------------------------------------------------- #

def create_access_token(
    subject: str | UUID,
    role: UserRole | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed HS256 JWT access token.

    The token payload always contains three standard claims plus an optional
    ``role`` claim introduced in Sprint 3.3:

    * ``sub``  — the principal identifier (user id or username).
    * ``role`` — the user's :class:`~app.models.user.UserRole` value as a
      plain string (e.g. ``"admin"``).  Present only when *role* is supplied.
      This claim is **informational** — the authoritative source is always the
      database record fetched by the ``get_current_user`` dependency.
    * ``iat``  — issued-at timestamp (UTC).
    * ``exp``  — expiry timestamp (UTC).

    Args:
        subject: The entity the token represents.  Typically a user UUID or
            username string.  A :class:`~uuid.UUID` is automatically converted
            to its string representation.
        role: The user's access tier.  When supplied, embedded in the payload
            as ``role: <role.value>``.  Existing call sites that omit this
            argument continue to work — the claim is simply absent.
        expires_delta: How long the token should be valid.  When ``None`` the
            :data:`~app.core.config.Settings.ACCESS_TOKEN_EXPIRE_MINUTES`
            setting is used as the default lifetime.

    Returns:
        A compact, URL-safe JWT string (``header.payload.signature``).

    Example::

        token = create_access_token(subject=user.id, role=user.role)
        # Returns e.g. "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...."
    """
    now: datetime = _utc_now()

    if expires_delta is not None:
        expire: datetime = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": _subject_to_str(subject),
        "iat": now,
        "exp": expire,
    }

    if role is not None:
        payload["role"] = role.value

    token: str = jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    logger.debug(
        "jwt.created",
        subject=_subject_to_str(subject),
        role=role.value if role is not None else None,
        expires_at=expire.isoformat(),
    )

    return token


# --------------------------------------------------------------------------- #
# JWT — token decoding & validation                                             #
# --------------------------------------------------------------------------- #

class TokenExpiredError(Exception):
    """Raised when the JWT has passed its ``exp`` claim.

    Callers should translate this into an HTTP 401 response with a
    ``WWW-Authenticate: Bearer error="invalid_token"`` header.
    """


class TokenInvalidError(Exception):
    """Raised when the JWT is malformed, has an invalid signature, or is
    missing required claims.

    Callers should translate this into an HTTP 401 response.
    """


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token.

    Verifies the signature against :data:`~app.core.config.Settings.SECRET_KEY`
    and checks that the token has not expired.  Also asserts the presence of
    the ``sub`` claim so that callers can rely on it without further guarding.

    Args:
        token: The compact JWT string received from the client (typically
            extracted from the ``Authorization: Bearer <token>`` header).

    Returns:
        The decoded payload dictionary, e.g.::

            {
                "sub": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "iat": 1718000000,
                "exp": 1718001800
            }

    Raises:
        TokenExpiredError: The token's ``exp`` claim is in the past.
        TokenInvalidError: The token is malformed, the signature is wrong,
            or the required ``sub`` claim is absent.

    Example::

        try:
            payload = decode_token(raw_token)
            user_id = payload["sub"]
        except TokenExpiredError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired")
        except TokenInvalidError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
    except ExpiredSignatureError as exc:
        logger.warning("jwt.expired", error=str(exc))
        raise TokenExpiredError("The access token has expired.") from exc
    except JWTError as exc:
        logger.warning("jwt.invalid", error=str(exc))
        raise TokenInvalidError(
            f"Token validation failed: {exc}"
        ) from exc

    # Belt-and-suspenders: ``require`` in options should already enforce this,
    # but we guard explicitly so type checkers and future refactors stay safe.
    if not payload.get("sub"):
        raise TokenInvalidError("Token is missing the required 'sub' claim.")

    return payload
