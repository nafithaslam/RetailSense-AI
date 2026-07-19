"""
RetailSense AI — User Management Service
==========================================
Orchestrates all user-management operations that require elevated privileges:
listing users, retrieving individual profiles, creating users with an explicit
role, assigning roles, and toggling active status.

This service is the **admin path** for user management.  Public self-registration
goes through :class:`~app.services.auth_service.AuthService` instead.

Responsibilities
----------------
* Coordinate :class:`~app.repositories.user_repository.UserRepository` with
  the domain validation rules specific to user management.
* Enforce the **self-modification guard**: an administrator may not demote or
  deactivate their own account.
* Return strongly-typed Pydantic schemas — never raw ORM model instances — so
  the service layer's public contract is independent of the database schema.
* Raise domain-specific exceptions that the route layer translates into HTTP
  responses.  This module **must not** import FastAPI or ``HTTPException``.

Domain exceptions
-----------------
* :class:`~app.core.domain_exceptions.UserNotFoundError`  — target user does not exist.
* :class:`~app.core.domain_exceptions.UserAlreadyExistsError` — email collision on creation.
* :class:`~app.core.domain_exceptions.ForbiddenOperationError` — self-modification attempt.

Usage
-----
    from app.services.user_service import UserService

    service = UserService(db)
    result = await service.list_users(page=1, page_size=20)
    print(result.total, len(result.items))
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain_exceptions import (
    ForbiddenOperationError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.user import UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.user import AdminCreateRequest, UserResponse

logger = get_logger(__name__)

_PAGE_SIZE_MAX = 100  # Hard cap on items-per-page regardless of caller input


# --------------------------------------------------------------------------- #
# Result DTO                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class PaginatedResult:
    """Holds a page of :class:`~app.schemas.user.UserResponse` objects.

    Returned by :meth:`UserService.list_users`.  A lightweight dataclass
    rather than a Pydantic model because it is an internal DTO — callers
    (route handlers) convert it to the wire-format schema themselves.

    Attributes
    ----------
    total:
        Total number of users in the database (used to calculate page count).
    page:
        Current 1-indexed page number.
    page_size:
        Number of items on this page (may be less than requested on the last page).
    items:
        Resolved :class:`~app.schemas.user.UserResponse` objects for this page.
    """

    total: int
    page: int
    page_size: int
    items: list[UserResponse] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# User management service                                                       #
# --------------------------------------------------------------------------- #


class UserService:
    """Orchestrates privileged user-management operations.

    All public methods are ``async`` coroutines and must be awaited.

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.  The
            session's lifecycle is managed by the ``get_db`` FastAPI dependency
            or by the test harness — **not** by this class.

    Example::

        service = UserService(db)
        page = await service.list_users(page=1, page_size=20)
        for user in page.items:
            print(user.email, user.role)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = UserRepository(db)

    # ---------------------------------------------------------------------- #
    # Read operations                                                          #
    # ---------------------------------------------------------------------- #

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult:
        """Return a paginated list of all users ordered by creation date.

        Args:
            page: 1-indexed page number.  Values < 1 are clamped to 1.
            page_size: Number of items per page.  Values > ``_PAGE_SIZE_MAX``
                are clamped to the maximum.

        Returns:
            A :class:`PaginatedResult` containing the requested page.

        Example::

            page = await service.list_users(page=2, page_size=20)
            print(f"Page {page.page}/{-(-page.total // page.page_size)}")
        """
        # Clamp inputs
        page = max(1, page)
        page_size = min(max(1, page_size), _PAGE_SIZE_MAX)

        offset = (page - 1) * page_size

        users = await self._repo.list_all(limit=page_size, offset=offset)
        total = await self._repo.count_all()

        items = [UserResponse.model_validate(u) for u in users]

        logger.debug(
            "user_service.list_users",
            page=page,
            page_size=page_size,
            total=total,
            returned=len(items),
        )

        return PaginatedResult(
            total=total,
            page=page,
            page_size=page_size,
            items=items,
        )

    async def get_user(self, user_id: uuid.UUID) -> UserResponse:
        """Return the public profile of a single user.

        Args:
            user_id: The UUID primary key of the user to retrieve.

        Returns:
            A :class:`~app.schemas.user.UserResponse` for the found user.

        Raises:
            UserNotFoundError: If no user with ``user_id`` exists.

        Example::

            user = await service.get_user(uuid.UUID("a1b2c3d4-..."))
        """
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"No user found with id '{user_id}'.")

        return UserResponse.model_validate(user)

    # ---------------------------------------------------------------------- #
    # Write operations                                                         #
    # ---------------------------------------------------------------------- #

    async def create_user(self, payload: AdminCreateRequest) -> UserResponse:
        """Create a new user with an explicitly specified role.

        This is the **admin path** for user creation.  Unlike public
        self-registration (``POST /auth/register``), the caller may specify
        any :class:`~app.models.user.UserRole`.

        Steps
        -----
        1. Check for email uniqueness — raise :class:`UserAlreadyExistsError`
           if the address is already taken.
        2. Hash the plain-text password with Argon2id.
        3. Persist the new user row via the repository.
        4. Return the new user's public profile.

        Args:
            payload: Validated :class:`~app.schemas.user.AdminCreateRequest`
                containing full_name, email, password, and explicit role.

        Returns:
            A :class:`~app.schemas.user.UserResponse` for the created user.

        Raises:
            UserAlreadyExistsError: If the email address is already registered.

        Example::

            user = await service.create_user(AdminCreateRequest(
                full_name="Jane Smith",
                email="jane@example.com",
                password="S3cur3P@ssword!",
                role=UserRole.MANAGER,
            ))
        """
        if await self._repo.exists_by_email(payload.email):
            logger.warning(
                "user_service.create_user.duplicate_email",
                email=payload.email,
            )
            raise UserAlreadyExistsError(
                f"An account with the email '{payload.email}' already exists."
            )

        hashed = hash_password(payload.password)

        user = await self._repo.create(
            full_name=payload.full_name,
            email=payload.email,
            password_hash=hashed,
            role=payload.role,
        )

        logger.info(
            "user_service.create_user.success",
            user_id=str(user.id),
            email=user.email,
            role=user.role.value,
        )

        return UserResponse.model_validate(user)

    async def assign_role(
        self,
        *,
        actor_id: uuid.UUID,
        target_id: uuid.UUID,
        new_role: UserRole,
    ) -> UserResponse:
        """Assign a new role to an existing user.

        The **self-modification guard** prevents an administrator from changing
        their own role.  This rule is enforced here (service layer) rather than
        only in the HTTP handler so that CLI scripts and background tasks are
        equally protected.

        Args:
            actor_id: UUID of the authenticated administrator performing the
                action.  Used exclusively for the self-modification guard.
            target_id: UUID of the user whose role will be changed.
            new_role: The :class:`~app.models.user.UserRole` to assign.

        Returns:
            A :class:`~app.schemas.user.UserResponse` reflecting the updated role.

        Raises:
            ForbiddenOperationError: If ``actor_id == target_id``.
            UserNotFoundError: If no user with ``target_id`` exists.

        Example::

            updated = await service.assign_role(
                actor_id=admin.id,
                target_id=target_uuid,
                new_role=UserRole.MANAGER,
            )
        """
        if actor_id == target_id:
            raise ForbiddenOperationError(
                "Administrators cannot change their own role."
            )

        target = await self._repo.get_by_id(target_id)
        if target is None:
            raise UserNotFoundError(f"No user found with id '{target_id}'.")

        old_role = target.role
        updated = await self._repo.update_role(target, new_role=new_role)

        logger.info(
            "user_service.assign_role",
            actor_id=str(actor_id),
            target_id=str(target_id),
            old_role=old_role.value,
            new_role=new_role.value,
        )

        return UserResponse.model_validate(updated)

    async def set_active(
        self,
        *,
        actor_id: uuid.UUID,
        target_id: uuid.UUID,
        is_active: bool,
    ) -> UserResponse:
        """Activate or deactivate a user account.

        The **self-modification guard** prevents an administrator from
        deactivating their own account.

        Args:
            actor_id: UUID of the authenticated administrator performing the
                action.  Used exclusively for the self-modification guard.
            target_id: UUID of the user whose active status will change.
            is_active: ``True`` to activate, ``False`` to deactivate.

        Returns:
            A :class:`~app.schemas.user.UserResponse` reflecting the new status.

        Raises:
            ForbiddenOperationError: If ``actor_id == target_id``.
            UserNotFoundError: If no user with ``target_id`` exists.

        Example::

            await service.set_active(
                actor_id=admin.id,
                target_id=target_uuid,
                is_active=False,
            )
        """
        if actor_id == target_id:
            raise ForbiddenOperationError(
                "Administrators cannot change their own active status."
            )

        target = await self._repo.get_by_id(target_id)
        if target is None:
            raise UserNotFoundError(f"No user found with id '{target_id}'.")

        updated = await self._repo.update_active_status(target, is_active=is_active)

        logger.info(
            "user_service.set_active",
            actor_id=str(actor_id),
            target_id=str(target_id),
            is_active=is_active,
        )

        return UserResponse.model_validate(updated)
