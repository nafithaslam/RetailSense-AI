"""
RetailSense AI — Authentication Dependencies
=============================================
FastAPI dependencies that resolve the current user from an incoming JWT.
These are injected into any protected route handler via ``Depends()``.

Dependency chain
----------------
::

    OAuth2PasswordBearer (extracts raw token from Authorization header)
        └── get_current_user(token, db)
                ├── decode_token(token)          → TokenPayload
                ├── AuthService.get_user_by_id() → AuthenticatedUser
                └── get_current_active_user(user)
                        └── asserts user.is_active → AuthenticatedUser

Public API
----------
* :data:`oauth2_scheme` — FastAPI OAuth2 bearer scheme; used by Swagger UI
  to render the **Authorize** button and by FastAPI to extract the token
  string from the ``Authorization`` header.
* :func:`get_current_user` — Decodes the JWT and resolves the matching
  :class:`~app.schemas.auth.AuthenticatedUser`.  Raises HTTP 401 on any
  token problem, HTTP 404 if the user no longer exists in the database.
* :func:`get_current_active_user` — Wraps :func:`get_current_user` with an
  additional ``is_active`` check.  Raises HTTP 403 if the account is
  disabled.  **This is the dependency most route handlers should use.**

Role-based access control (future)
-----------------------------------
Role guards can be layered on top of :func:`get_current_active_user`::

    def require_role(*roles: UserRole):
        async def _guard(
            user: Annotated[AuthenticatedUser, Depends(get_current_active_user)]
        ) -> AuthenticatedUser:
            if user.role not in roles:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions.")
            return user
        return _guard

    AdminOnly = Depends(require_role(UserRole.ADMIN))

Usage
-----
    from typing import Annotated
    from fastapi import Depends
    from app.dependencies.auth import get_current_active_user
    from app.schemas.auth import AuthenticatedUser

    CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_active_user)]

    @router.get("/dashboard")
    async def dashboard(user: CurrentUser) -> dict:
        return {"welcome": user.full_name}
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import TokenExpiredError, TokenInvalidError, decode_token
from app.database.session import get_db
from app.schemas.auth import AuthenticatedUser
from app.services.auth_service import AuthService, UserNotFoundError

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# OAuth2 bearer scheme                                                          #
# --------------------------------------------------------------------------- #

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    description=(
        "JWT Bearer token.  Use POST /api/v1/auth/token (form-data) to obtain "
        "one via the Swagger Authorize dialog, or POST /api/v1/auth/login "
        "(JSON) from frontend / mobile clients."
    ),
)
"""FastAPI OAuth2 bearer extractor.

Points ``tokenUrl`` at ``/api/v1/auth/token`` — the dedicated OAuth2
form-data endpoint — so that:

1. Swagger UI's **Authorize** dialog POSTs ``username`` + ``password`` as
   ``application/x-www-form-urlencoded`` to the correct handler.
2. The handler returns ``{ "access_token": "...", "token_type": "bearer" }``
   in plain snake_case (RFC 6749 §5.1), which Swagger UI can parse and store.
3. FastAPI extracts the raw token string from subsequent requests' 
   ``Authorization: Bearer <token>`` header.
4. The endpoint is marked as requiring authentication in the OpenAPI schema,
   rendering the padlock icon on protected routes in Swagger UI.

If the ``Authorization`` header is absent, FastAPI raises HTTP 401
automatically before the route handler is invoked.
"""

# --------------------------------------------------------------------------- #
# Dependency type aliases                                                       #
# --------------------------------------------------------------------------- #

TokenDep = Annotated[str, Depends(oauth2_scheme)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


# --------------------------------------------------------------------------- #
# get_current_user                                                              #
# --------------------------------------------------------------------------- #

async def get_current_user(
    token: TokenDep,
    db: DbDep,
) -> AuthenticatedUser:
    """Decode a JWT and return the corresponding user's public profile.

    This dependency performs three steps:

    1. **Decode & validate** the JWT via
       :func:`~app.core.security.decode_token`.  Raises HTTP 401 if the
       token is expired or malformed.
    2. **Resolve the user** by looking up the ``sub`` claim (UUID string)
       via :meth:`~app.services.auth_service.AuthService.get_user_by_id`.
       Raises HTTP 404 if the user has been removed from the database.
    3. **Return** the typed :class:`~app.schemas.auth.AuthenticatedUser`
       schema instance.  The ``is_active`` check is left to
       :func:`get_current_active_user` so callers that need to serve
       deactivated users (e.g. an admin "unlock" endpoint) can use this
       dependency directly.

    Args:
        token: Raw JWT string extracted from the ``Authorization: Bearer``
            header by :data:`oauth2_scheme`.
        db: Async database session injected by ``get_db``.

    Returns:
        The :class:`~app.schemas.auth.AuthenticatedUser` whose UUID matches
        the ``sub`` claim in the token.

    Raises:
        HTTPException (401): Token is expired, malformed, or missing
            required claims.
        HTTPException (404): The user referenced by the token's ``sub``
            claim no longer exists in the database.

    Example::

        CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

        @router.get("/profile")
        async def profile(user: CurrentUser) -> AuthenticatedUser:
            return user
    """
    # 1. Validate the JWT and extract claims
    try:
        payload = decode_token(token)
    except TokenExpiredError as exc:
        logger.warning("dependency.auth.token_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc
    except TokenInvalidError as exc:
        logger.warning("dependency.auth.token_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc

    user_id: str = payload["sub"]

    # 2. Resolve the user from the database
    service = AuthService(db)
    try:
        user = await service.get_user_by_id(user_id)
    except UserNotFoundError as exc:
        logger.warning(
            "dependency.auth.user_not_found",
            user_id=user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        ) from exc

    return user


# --------------------------------------------------------------------------- #
# get_current_active_user                                                       #
# --------------------------------------------------------------------------- #

async def get_current_active_user(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """Extend :func:`get_current_user` with an ``is_active`` guard.

    This is the **primary dependency** that protected route handlers should
    inject.  It guarantees that:

    * The JWT is valid and not expired.
    * The user exists in the database.
    * The account has not been deactivated (``is_active == True``).

    Args:
        current_user: Resolved by :func:`get_current_user`; already
            validated against the database.

    Returns:
        The same :class:`~app.schemas.auth.AuthenticatedUser` instance,
        confirmed to be active.

    Raises:
        HTTPException (403): The account exists but ``is_active`` is
            ``False``.  HTTP 403 (Forbidden) is used rather than 401
            (Unauthorized) because the token is valid — the user simply
            lacks permission to access the resource in their current state.

    Example::

        ActiveUser = Annotated[AuthenticatedUser, Depends(get_current_active_user)]

        @router.delete("/inventory/{item_id}")
        async def delete_item(item_id: int, user: ActiveUser) -> None:
            ...
    """
    if not current_user.is_active:
        logger.warning(
            "dependency.auth.inactive_account",
            user_id=str(current_user.id),
            email=current_user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This account has been deactivated.  "
                "Please contact support."
            ),
        )

    return current_user
