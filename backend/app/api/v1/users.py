"""
RetailSense AI — User Management API Routes (v1)
=================================================
Provides six HTTP endpoints for privileged user-management operations.
All endpoints require an authenticated user; most require elevated roles
enforced by the RBAC dependency guards introduced in Sprint 3.3.

Endpoints
---------
POST   /api/v1/users/
    Create a new user with an explicit role assignment.
    **Guard**: ``AdminOnly``

GET    /api/v1/users/
    List all users with pagination.
    **Guard**: ``ManagerOrAbove``

GET    /api/v1/users/{user_id}
    Retrieve a single user's public profile.
    **Guard**: ``ManagerOrAbove``

PATCH  /api/v1/users/{user_id}/role
    Assign a new role to a user.
    **Guard**: ``AdminOnly``

PATCH  /api/v1/users/{user_id}/deactivate
    Soft-disable a user account (sets ``is_active = False``).
    **Guard**: ``AdminOnly``

PATCH  /api/v1/users/{user_id}/activate
    Re-enable a previously deactivated account (sets ``is_active = True``).
    **Guard**: ``AdminOnly``

Design principles
-----------------
* **Thin handlers** — every handler delegates entirely to
  :class:`~app.services.user_service.UserService`.  No business logic or SQL
  lives in this module.
* **Domain exception translation** — :class:`~app.core.domain_exceptions.UserNotFoundError`,
  :class:`~app.core.domain_exceptions.UserAlreadyExistsError`, and
  :class:`~app.core.domain_exceptions.ForbiddenOperationError` are caught and
  converted into the appropriate ``HTTPException``.
* **RBAC via dependency injection** — route guards are declared as
  ``Annotated[AuthenticatedUser, AdminOnly]`` parameters.  FastAPI evaluates
  the full dependency chain (token decode → DB lookup → active check →
  role check) before the handler body runs.
* **Soft-delete only** — there is no ``DELETE /users/{user_id}`` endpoint.
  Deactivation preserves referential integrity and audit history.

Usage (Swagger UI)
------------------
1. Obtain an ADMIN JWT via ``POST /api/v1/auth/token``.
2. Click **Authorize** → paste the token.
3. ``GET /api/v1/users/`` → see all users.
4. ``POST /api/v1/users/`` → create a user with an explicit role.
5. ``PATCH /api/v1/users/{user_id}/role`` → assign a new role.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain_exceptions import (
    ForbiddenOperationError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from app.database.session import get_db
from app.dependencies.auth import (
    AdminOnly,
    ManagerOrAbove,
    get_current_active_user,
)
from app.schemas.auth import AuthenticatedUser
from app.schemas.user import (
    AdminCreateRequest,
    PaginatedUsersResponse,
    RoleAssignRequest,
    UserResponse,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["User Management"])

# --------------------------------------------------------------------------- #
# Dependency aliases — keeps handler signatures readable                        #
# --------------------------------------------------------------------------- #

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[AuthenticatedUser, AdminOnly]
ManagerOrAboveDep = Annotated[AuthenticatedUser, ManagerOrAbove]


# --------------------------------------------------------------------------- #
# POST /users/  — Admin: create a user with an explicit role                   #
# --------------------------------------------------------------------------- #


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user with an explicit role (admin only)",
    description=(
        "Create a new user account with the specified role.  "
        "Unlike ``POST /auth/register`` (public self-registration, always ``STAFF``), "
        "this endpoint allows the caller to assign any role.  "
        "**Requires ADMIN role.**"
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "User created successfully."},
        status.HTTP_403_FORBIDDEN: {"description": "Caller does not have ADMIN role."},
        status.HTTP_409_CONFLICT: {"description": "Email address already registered."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error."},
    },
)
async def create_user(
    payload: AdminCreateRequest,
    actor: AdminDep,
    db: DbDep,
) -> UserResponse:
    """Create a new user account with an explicitly specified role.

    Args:
        payload: ``AdminCreateRequest`` containing full_name, email, password,
            and role.
        actor: The authenticated ADMIN performing the action (resolved by
            the ``AdminOnly`` guard dependency).
        db: Async database session injected by ``get_db``.

    Returns:
        The newly created user's :class:`~app.schemas.user.UserResponse`.

    Raises:
        HTTPException (409): When the email address is already registered.
    """
    service = UserService(db)
    try:
        user = await service.create_user(payload)
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return user


# --------------------------------------------------------------------------- #
# GET /users/  — Manager+: paginated user list                                 #
# --------------------------------------------------------------------------- #


@router.get(
    "/",
    response_model=PaginatedUsersResponse,
    status_code=status.HTTP_200_OK,
    summary="List all users (manager or above)",
    description=(
        "Return a paginated list of all user accounts.  "
        "Results are ordered by account creation date (ascending).  "
        "**Requires MANAGER or ADMIN role.**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Paginated user list returned."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient role."},
    },
)
async def list_users(
    _actor: ManagerOrAboveDep,
    db: DbDep,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Number of items per page (max 100)."
    ),
) -> PaginatedUsersResponse:
    """Return a paginated list of users.

    Args:
        _actor: The authenticated MANAGER or ADMIN (guard only — not used in body).
        db: Async database session.
        page: 1-indexed page number.
        page_size: Items per page; clamped at 100 by the service.

    Returns:
        A :class:`~app.schemas.user.PaginatedUsersResponse` envelope.
    """
    service = UserService(db)
    result = await service.list_users(page=page, page_size=page_size)

    return PaginatedUsersResponse(
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        items=result.items,
    )


# --------------------------------------------------------------------------- #
# GET /users/{user_id}  — Manager+: single user profile                       #
# --------------------------------------------------------------------------- #


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single user's profile (manager or above)",
    description=(
        "Retrieve the public profile of any user by their UUID.  "
        "**Requires MANAGER or ADMIN role.**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "User profile returned."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient role."},
        status.HTTP_404_NOT_FOUND: {"description": "User not found."},
    },
)
async def get_user(
    user_id: uuid.UUID,
    _actor: ManagerOrAboveDep,
    db: DbDep,
) -> UserResponse:
    """Return a single user's public profile.

    Args:
        user_id: The UUID of the user to retrieve.
        _actor: The authenticated MANAGER or ADMIN (guard only).
        db: Async database session.

    Returns:
        The :class:`~app.schemas.user.UserResponse` for the found user.

    Raises:
        HTTPException (404): When no user with ``user_id`` exists.
    """
    service = UserService(db)
    try:
        user = await service.get_user(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return user


# --------------------------------------------------------------------------- #
# PATCH /users/{user_id}/role  — Admin: assign a role                         #
# --------------------------------------------------------------------------- #


@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign a new role to a user (admin only)",
    description=(
        "Change the access role for the specified user.  "
        "An administrator cannot modify their own role (enforced at the service layer).  "
        "**Requires ADMIN role.**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Role updated successfully."},
        status.HTTP_400_BAD_REQUEST: {"description": "Self-modification attempt."},
        status.HTTP_403_FORBIDDEN: {"description": "Caller does not have ADMIN role."},
        status.HTTP_404_NOT_FOUND: {"description": "Target user not found."},
    },
)
async def assign_role(
    user_id: uuid.UUID,
    payload: RoleAssignRequest,
    actor: AdminDep,
    db: DbDep,
) -> UserResponse:
    """Assign a new role to the specified user.

    Args:
        user_id: UUID of the user whose role will be updated.
        payload: :class:`~app.schemas.user.RoleAssignRequest` containing
            the new role.
        actor: The authenticated ADMIN (resolved by ``AdminOnly`` guard).
        db: Async database session.

    Returns:
        The updated :class:`~app.schemas.user.UserResponse`.

    Raises:
        HTTPException (400): When the actor attempts to change their own role.
        HTTPException (404): When the target user does not exist.
    """
    service = UserService(db)
    try:
        updated = await service.assign_role(
            actor_id=actor.id,
            target_id=user_id,
            new_role=payload.role,
        )
    except ForbiddenOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return updated


# --------------------------------------------------------------------------- #
# PATCH /users/{user_id}/deactivate  — Admin: soft-disable account             #
# --------------------------------------------------------------------------- #


@router.patch(
    "/{user_id}/deactivate",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate a user account (admin only)",
    description=(
        "Soft-disable the specified user account by setting ``is_active = False``.  "
        "The account record and all associated data are preserved.  "
        "An administrator cannot deactivate their own account.  "
        "**Requires ADMIN role.**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Account deactivated."},
        status.HTTP_400_BAD_REQUEST: {"description": "Self-modification attempt."},
        status.HTTP_403_FORBIDDEN: {"description": "Caller does not have ADMIN role."},
        status.HTTP_404_NOT_FOUND: {"description": "Target user not found."},
    },
)
async def deactivate_user(
    user_id: uuid.UUID,
    actor: AdminDep,
    db: DbDep,
) -> UserResponse:
    """Deactivate the specified user account.

    Args:
        user_id: UUID of the user to deactivate.
        actor: The authenticated ADMIN (resolved by ``AdminOnly`` guard).
        db: Async database session.

    Returns:
        The updated :class:`~app.schemas.user.UserResponse` with
        ``is_active = False``.

    Raises:
        HTTPException (400): When the actor tries to deactivate their own account.
        HTTPException (404): When the target user does not exist.
    """
    service = UserService(db)
    try:
        updated = await service.set_active(
            actor_id=actor.id,
            target_id=user_id,
            is_active=False,
        )
    except ForbiddenOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return updated


# --------------------------------------------------------------------------- #
# PATCH /users/{user_id}/activate  — Admin: re-enable account                  #
# --------------------------------------------------------------------------- #


@router.patch(
    "/{user_id}/activate",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate a user account (admin only)",
    description=(
        "Re-enable a previously deactivated user account by setting "
        "``is_active = True``.  "
        "**Requires ADMIN role.**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Account activated."},
        status.HTTP_400_BAD_REQUEST: {"description": "Self-modification attempt."},
        status.HTTP_403_FORBIDDEN: {"description": "Caller does not have ADMIN role."},
        status.HTTP_404_NOT_FOUND: {"description": "Target user not found."},
    },
)
async def activate_user(
    user_id: uuid.UUID,
    actor: AdminDep,
    db: DbDep,
) -> UserResponse:
    """Re-enable the specified user account.

    Args:
        user_id: UUID of the user to activate.
        actor: The authenticated ADMIN (resolved by ``AdminOnly`` guard).
        db: Async database session.

    Returns:
        The updated :class:`~app.schemas.user.UserResponse` with
        ``is_active = True``.

    Raises:
        HTTPException (400): When the actor tries to activate their own account
            (no-op guard kept for consistency).
        HTTPException (404): When the target user does not exist.
    """
    service = UserService(db)
    try:
        updated = await service.set_active(
            actor_id=actor.id,
            target_id=user_id,
            is_active=True,
        )
    except ForbiddenOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return updated
