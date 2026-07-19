"""
RetailSense AI — User Repository
==================================
Async data-access layer for the ``users`` table.

Responsibility
--------------
This class owns **all** raw SQLAlchemy interactions that involve the
:class:`~app.models.user.User` ORM model.  It deliberately contains:

* **No** password-hashing logic.
* **No** JWT creation / validation.
* **No** email-sending or notification side-effects.
* **No** HTTP-layer concerns (no ``HTTPException``).

All of the above belong in the service layer.  The repository's only
concern is translating Python method calls into async SQL operations and
returning typed ORM instances (or ``None`` / lists of them).

Async SQLAlchemy 2.x patterns used
------------------------------------
* ``session.execute(select(...))`` instead of the legacy ``session.query``.
* ``result.scalar_one_or_none()`` for single-row lookups.
* ``result.scalars().all()`` for multi-row fetches.
* ``session.add()`` + ``await session.flush()`` for inserts — the session
  is committed by the ``get_db`` dependency after the request completes,
  not inside the repository itself.

Usage
-----
    from app.repositories.user_repository import UserRepository
    from app.database.session import get_db

    # Typically injected by the service layer, not called from routes
    async with AsyncSessionFactory() as db:
        repo = UserRepository(db)
        user = await repo.get_by_email("jane@example.com")
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user import User, UserRole

logger = get_logger(__name__)


class UserRepository:
    """Async CRUD operations for the ``users`` table.

    All methods are ``async`` and must be awaited.  The repository does not
    own the session's lifecycle — the caller (service layer) is responsible
    for commit / rollback.

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.  Passed
            in via constructor injection so the repository is trivially
            mockable in unit tests.

    Example::

        repo = UserRepository(db)
        user = await repo.get_by_id(some_uuid)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Read operations                                                          #
    # ---------------------------------------------------------------------- #

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch a single user by their primary key.

        Args:
            user_id: The UUID primary key to look up.

        Returns:
            The :class:`~app.models.user.User` instance if found, or
            ``None`` if no row matches.

        Example::

            user = await repo.get_by_id(uuid.UUID("a1b2c3d4-..."))
            if user is None:
                raise NotFoundError("user", str(user_id))
        """
        result = await self._db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Fetch a single user by their (normalised) email address.

        The lookup is **case-sensitive** at the SQL level.  Email
        normalisation (lower-casing) must happen before calling this method
        — the schema validators and service layer handle that.

        Args:
            email: The lower-cased email address to search for.

        Returns:
            The matching :class:`~app.models.user.User`, or ``None``.

        Example::

            user = await repo.get_by_email("jane@example.com")
        """
        result = await self._db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[User]:
        """Return a paginated list of all users, ordered by creation date.

        Args:
            limit: Maximum number of rows to return.  Defaults to 100.
            offset: Number of rows to skip (for pagination).  Defaults to 0.

        Returns:
            A (possibly empty) sequence of :class:`~app.models.user.User`
            instances ordered by ``created_at`` ascending.

        Example::

            page_1 = await repo.list_all(limit=20, offset=0)
            page_2 = await repo.list_all(limit=20, offset=20)
        """
        result = await self._db.execute(
            select(User)
            .order_by(User.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def exists_by_email(self, email: str) -> bool:
        """Check whether a user with the given email already exists.

        Cheaper than :meth:`get_by_email` when you only need a boolean
        answer (no ORM hydration).

        Args:
            email: The lower-cased email to check.

        Returns:
            ``True`` if at least one row with ``email`` exists, ``False``
            otherwise.

        Example::

            if await repo.exists_by_email("jane@example.com"):
                raise ConflictError("email already registered")
        """
        result = await self._db.execute(
            select(User.id).where(User.email == email).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def count_all(self) -> int:
        """Return the total number of user rows in the table.

        Used by :meth:`list_all` callers to compute pagination metadata
        without fetching full ORM objects.

        Returns:
            An integer count of all ``users`` rows.

        Example::

            total = await repo.count_all()
            page_count = -(-total // page_size)   # ceiling division
        """
        from sqlalchemy import func

        result = await self._db.execute(select(func.count()).select_from(User))
        return result.scalar_one()

    # ---------------------------------------------------------------------- #
    # Write operations                                                         #
    # ---------------------------------------------------------------------- #

    async def create(
        self,
        *,
        full_name: str,
        email: str,
        password_hash: str,
        role: UserRole = UserRole.STAFF,
        is_active: bool = True,
    ) -> User:
        """Persist a new user row and return the hydrated ORM instance.

        The method calls ``flush()`` (not ``commit()``) so the new row is
        written within the current transaction but the transaction boundary
        remains with the caller.  The ``get_db`` dependency commits after a
        successful request; the service layer may also commit explicitly
        when needed.

        After ``flush()``, ``refresh()`` is called so that server-generated
        values (``id`` via ``gen_random_uuid()``, ``created_at``,
        ``updated_at``) are populated on the returned object.

        Args:
            full_name: Display name for the new account.
            email: Normalised (lower-case) email address.
            password_hash: Pre-hashed Argon2id digest.  **Never** pass a
                plain-text password here.
            role: Access tier.  Defaults to ``STAFF``.
            is_active: Whether the account starts enabled.  Defaults to
                ``True``.

        Returns:
            The freshly created :class:`~app.models.user.User` instance
            with all DB-generated fields populated.

        Example::

            hashed = hash_password(raw_password)
            user = await repo.create(
                full_name="Jane Smith",
                email="jane@example.com",
                password_hash=hashed,
            )
            print(user.id)   # UUID set by PostgreSQL
        """
        user = User(
            full_name=full_name,
            email=email,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
        )
        self._db.add(user)
        await self._db.flush()
        await self._db.refresh(user)

        logger.debug(
            "user.created",
            user_id=str(user.id),
            email=user.email,
            role=user.role.value,
        )
        return user

    async def update_active_status(
        self,
        user: User,
        *,
        is_active: bool,
    ) -> User:
        """Toggle the ``is_active`` flag on an existing user record.

        Args:
            user: The ORM instance to update.  Must already be associated
                with the current session.
            is_active: The new active status.

        Returns:
            The same ``user`` instance with the updated flag.

        Example::

            user = await repo.get_by_id(user_id)
            user = await repo.update_active_status(user, is_active=False)
        """
        user.is_active = is_active
        await self._db.flush()
        await self._db.refresh(user)

        logger.debug(
            "user.status_updated",
            user_id=str(user.id),
            is_active=is_active,
        )
        return user

    async def update_role(self, user: User, *, new_role: UserRole) -> User:
        """Assign a new role to an existing user record.

        Args:
            user: The ORM instance to update.  Must already be associated
                with the current session.
            new_role: The :class:`~app.models.user.UserRole` to assign.

        Returns:
            The same ``user`` instance with the updated role.

        Example::

            user = await repo.get_by_id(user_id)
            user = await repo.update_role(user, new_role=UserRole.MANAGER)
        """
        user.role = new_role
        await self._db.flush()
        await self._db.refresh(user)

        logger.debug(
            "user.role_updated",
            user_id=str(user.id),
            new_role=new_role.value,
        )
        return user

    async def delete(self, user: User) -> None:
        """Hard-delete a user row from the database.

        Prefer :meth:`update_active_status` for soft-deletes.  Only use
        this for irreversible removals (e.g. GDPR erasure requests).

        Args:
            user: The ORM instance to delete.  Must be in the current
                session's identity map.

        Example::

            user = await repo.get_by_id(user_id)
            if user:
                await repo.delete(user)
        """
        await self._db.delete(user)
        await self._db.flush()

        logger.debug("user.deleted", user_id=str(user.id))
