"""
RetailSense AI — Customer Repository
=======================================
Async data-access layer for the ``customers`` table.

Responsibility
--------------
This class owns **all** raw SQLAlchemy interactions that involve the
:class:`~app.models.customer.Customer` ORM model.  It deliberately contains:

* **No** business-rule enforcement (e.g. "a customer must have a name").
* **No** uniqueness or conflict decisions — those belong in the service layer.
* **No** HTTP-layer concerns (no ``HTTPException``).
* **No** authentication or authorisation.
* **No** commit / session-lifecycle management.

All of the above belong in the service layer.  The repository's only
concern is translating Python method calls into async SQL operations and
returning typed ORM instances (or ``None`` / lists of them).

Async SQLAlchemy 2.x patterns used
------------------------------------
* ``session.execute(select(...))`` — SQLAlchemy 2.x canonical query style.
* ``result.scalar_one_or_none()``  — single-row lookups; ``None`` on miss.
* ``result.scalars().all()``       — multi-row fetches.
* ``session.add()`` + ``await session.flush()`` for inserts — the session
  is committed by the ``get_db`` dependency after the request completes,
  not inside the repository itself.
* ``select(func.count())`` for efficient count-only queries.
* ``ilike()`` for case-insensitive text search without a separate
  tsvector / full-text index (acceptable at current scale).

Transaction contract
--------------------
* Repository methods call ``flush()`` (never ``commit()``) so that
  database-generated values (UUID, timestamps) are immediately visible
  on the returned ORM object, but the transaction boundary stays with
  the caller.
* ``refresh()`` is called after every ``flush()`` on mutated objects so
  that server-generated columns (``gen_random_uuid()``, ``now()``) are
  re-read from the database into the Python object before returning.
* ``delete()`` calls ``flush()`` so the DELETE is sent within the
  current transaction but is still rollback-able by the caller.
* The repository **never** calls ``session.close()`` or opens its own
  session.

Usage
-----
    from app.repositories.customer_repository import CustomerRepository
    from app.database.session import get_db

    # Typically injected by the service layer, not called from routes directly
    async with AsyncSessionFactory() as db:
        repo = CustomerRepository(db)
        customer = await repo.get_by_email("alice@example.com")
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.customer import Customer

logger = get_logger(__name__)


class CustomerRepository:
    """Async CRUD operations for the ``customers`` table.

    All methods are ``async`` and must be awaited.  The repository does not
    own the session's lifecycle — the caller (service layer) is responsible
    for commit / rollback.

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.  Passed
            in via constructor injection so the repository is trivially
            mockable in unit tests.

    Example::

        repo = CustomerRepository(db)
        customer = await repo.get_by_id(some_uuid)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _apply_filters(stmt, *, search, email, phone, is_active):
        """Attach WHERE clauses to *stmt* based on the supplied filter values.

        This helper is shared by :meth:`list_all` and :meth:`count_all` to
        guarantee that both methods use **exactly** the same filtering logic.

        Args:
            stmt:      A SQLAlchemy ``Select`` statement targeting the
                       ``customers`` table.
            search:    Optional free-text term.  Matched case-insensitively
                       against ``first_name``, ``last_name``, and ``email``
                       using ILIKE.  Each word in a multi-word query is ANDed
                       so that "alice tan" only matches rows where both tokens
                       appear somewhere in the searchable columns.
            email:     Optional exact email filter (value already lower-cased
                       by the schema/caller).
            phone:     Optional exact phone filter (E.164 string).
            is_active: Optional boolean; ``None`` means "no filter" (return
                       both active and inactive customers).

        Returns:
            The modified ``Select`` statement with all applicable WHERE
            clauses applied.
        """
        if search:
            # Split on whitespace; each token must match at least one of the
            # name/email columns.  This means "alice tan" finds customers
            # whose first_name OR last_name OR email contains "alice" AND
            # whose first_name OR last_name OR email also contains "tan".
            for token in search.split():
                pattern = f"%{token}%"
                stmt = stmt.where(
                    or_(
                        Customer.first_name.ilike(pattern),
                        Customer.last_name.ilike(pattern),
                        Customer.email.ilike(pattern),
                    )
                )

        if email is not None:
            stmt = stmt.where(Customer.email == email)

        if phone is not None:
            stmt = stmt.where(Customer.phone == phone)

        if is_active is not None:
            stmt = stmt.where(Customer.is_active == is_active)

        return stmt

    # ---------------------------------------------------------------------- #
    # Read operations                                                          #
    # ---------------------------------------------------------------------- #

    async def get_by_id(self, customer_id: uuid.UUID) -> Customer | None:
        """Fetch a single customer by their primary key.

        Args:
            customer_id: The UUID primary key to look up.

        Returns:
            The :class:`~app.models.customer.Customer` instance if found,
            or ``None`` if no row matches.

        Example::

            customer = await repo.get_by_id(uuid.UUID("a1b2c3d4-..."))
            if customer is None:
                raise NotFoundError("customer", str(customer_id))
        """
        result = await self._db.execute(
            select(Customer).where(Customer.id == customer_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Customer | None:
        """Fetch a single customer by their (normalised) email address.

        The lookup is case-sensitive at the SQL level.  Email normalisation
        (lower-casing) must happen before calling this method — the schema
        validators and service layer handle that.

        Args:
            email: The lower-cased email address to search for.

        Returns:
            The matching :class:`~app.models.customer.Customer`, or ``None``.

        Example::

            customer = await repo.get_by_email("alice@example.com")
        """
        result = await self._db.execute(
            select(Customer).where(Customer.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Customer | None:
        """Fetch a single customer by their phone number.

        The lookup is case-sensitive and expects an E.164-formatted string
        (e.g. ``"+60123456789"``).  Normalisation must happen before calling
        this method.

        Args:
            phone: The E.164 phone number to search for.

        Returns:
            The matching :class:`~app.models.customer.Customer`, or ``None``.

        Example::

            customer = await repo.get_by_phone("+60123456789")
        """
        result = await self._db.execute(
            select(Customer).where(Customer.phone == phone)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        is_active: bool | None = None,
    ) -> Sequence[Customer]:
        """Return a filtered, paginated list of customers.

        Pagination uses ``LIMIT`` / ``OFFSET`` calculated from the supplied
        ``page`` and ``page_size`` arguments.  Ordering is deterministic:
        ``created_at DESC, id DESC`` — newest records first; ``id`` breaks
        ties on rows inserted in the same millisecond.

        Filter semantics
        ----------------
        * ``search`` — ILIKE token match on ``first_name``, ``last_name``,
          and ``email``.  Multi-word tokens are ANDed.
        * ``email``  — Exact equality on the normalised email column.
        * ``phone``  — Exact equality on the phone column.
        * ``is_active`` — Boolean equality; ``None`` = no filter.

        Args:
            page:      Current 1-indexed page number.
            page_size: Number of rows per page.
            search:    Optional free-text search term.
            email:     Optional exact email filter.
            phone:     Optional exact phone filter.
            is_active: Optional boolean filter on the active-status flag.

        Returns:
            A (possibly empty) sequence of
            :class:`~app.models.customer.Customer` instances.

        Example::

            customers = await repo.list_all(
                page=1, page_size=20, search="alice", is_active=True
            )
        """
        offset = (page - 1) * page_size

        stmt = select(Customer)
        stmt = self._apply_filters(
            stmt,
            search=search,
            email=email,
            phone=phone,
            is_active=is_active,
        )
        stmt = (
            stmt
            .order_by(Customer.created_at.desc(), Customer.id.desc())
            .limit(page_size)
            .offset(offset)
        )

        result = await self._db.execute(stmt)
        return result.scalars().all()

    async def count_all(
        self,
        *,
        search: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        is_active: bool | None = None,
    ) -> int:
        """Return the total number of customers matching the given filters.

        Uses ``COUNT(*)`` against a filter-wrapped sub-query so only one
        integer is transferred from the database.  The filter logic is
        **identical** to :meth:`list_all` (delegated to
        :meth:`_apply_filters`) so callers can trust that
        ``count_all(...)`` reflects the same population as
        ``list_all(...)``.

        Args:
            search:    Optional free-text search term.
            email:     Optional exact email filter.
            phone:     Optional exact phone filter.
            is_active: Optional boolean filter on the active-status flag.

        Returns:
            An integer count of matching ``customers`` rows.

        Example::

            total = await repo.count_all(is_active=True)
            total_pages = -(-total // page_size)   # ceiling division
        """
        stmt = select(func.count()).select_from(Customer)
        stmt = self._apply_filters(
            stmt,
            search=search,
            email=email,
            phone=phone,
            is_active=is_active,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one()

    async def exists_by_email(self, email: str) -> bool:
        """Check whether a customer with the given email already exists.

        Cheaper than :meth:`get_by_email` when only a boolean answer is
        needed — only the ``id`` column is selected; no full ORM
        hydration occurs.

        Args:
            email: The lower-cased email to check.

        Returns:
            ``True`` if at least one row with that email exists,
            ``False`` otherwise.

        Example::

            if await repo.exists_by_email("alice@example.com"):
                raise ConflictError("email already registered")
        """
        result = await self._db.execute(
            select(Customer.id).where(Customer.email == email).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def exists_by_phone(self, phone: str) -> bool:
        """Check whether a customer with the given phone number already exists.

        Cheaper than :meth:`get_by_phone` when only a boolean answer is
        needed — only the ``id`` column is selected; no full ORM
        hydration occurs.

        Args:
            phone: The E.164 phone number to check.

        Returns:
            ``True`` if at least one row with that phone exists,
            ``False`` otherwise.

        Example::

            if await repo.exists_by_phone("+60123456789"):
                raise ConflictError("phone already registered")
        """
        result = await self._db.execute(
            select(Customer.id).where(Customer.phone == phone).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ---------------------------------------------------------------------- #
    # Write operations                                                         #
    # ---------------------------------------------------------------------- #

    async def create(self, customer: Customer) -> Customer:
        """Persist a new customer ORM object and return the hydrated instance.

        The caller constructs the :class:`~app.models.customer.Customer`
        object and hands it to this method.  The repository adds it to the
        session, flushes (writing within the open transaction), and refreshes
        so that server-generated values (UUID, timestamps) are populated
        before returning.

        No ``commit()`` is called — the transaction boundary remains with
        the caller / ``get_db`` dependency.

        Args:
            customer: A fully populated (but not yet persisted)
                      :class:`~app.models.customer.Customer` ORM instance.

        Returns:
            The same instance with ``id``, ``created_at``, and ``updated_at``
            populated from the database.

        Example::

            new_customer = Customer(
                first_name="Alice",
                last_name="Tan",
                email="alice@example.com",
            )
            customer = await repo.create(new_customer)
            print(customer.id)   # UUID set by PostgreSQL
        """
        self._db.add(customer)
        await self._db.flush()
        await self._db.refresh(customer)

        logger.debug(
            "customer.created",
            customer_id=str(customer.id),
            email=customer.email,
        )
        return customer

    async def update(self, customer: Customer) -> Customer:
        """Flush mutations on an existing customer and return the refreshed instance.

        The caller mutates whichever fields need updating directly on the ORM
        object, then passes it here.  ``flush()`` sends the UPDATE to the
        database; ``refresh()`` re-reads server-side columns (``updated_at``)
        so the returned object reflects the persisted state.

        No ``commit()`` is called.

        Args:
            customer: The ORM instance to persist.  Must already be
                      tracked by the current session (i.e. obtained from
                      a previous ``get_*`` call in the same session).

        Returns:
            The same ``customer`` instance with refreshed server-generated
            column values.

        Example::

            customer = await repo.get_by_id(customer_id)
            customer.first_name = "Alicia"
            customer = await repo.update(customer)
            print(customer.updated_at)   # reflects the DB value
        """
        await self._db.flush()
        await self._db.refresh(customer)

        logger.debug(
            "customer.updated",
            customer_id=str(customer.id),
            email=customer.email,
        )
        return customer

    async def delete(self, customer: Customer) -> None:
        """Hard-delete a customer row from the database.

        The DELETE is issued within the current open transaction via
        ``flush()``.  The transaction can still be rolled back by the caller
        before ``commit()`` is invoked.

        Prefer setting ``is_active = False`` via :meth:`update` for logical
        soft-deletes that preserve audit history.

        Args:
            customer: The ORM instance to delete.  Must be in the current
                      session's identity map.

        Example::

            customer = await repo.get_by_id(customer_id)
            if customer:
                await repo.delete(customer)
        """
        await self._db.delete(customer)
        await self._db.flush()

        logger.debug("customer.deleted", customer_id=str(customer.id))
