"""
RetailSense AI — Customer Service
====================================
Orchestrates all customer-management operations: registration, retrieval,
paginated search, partial update, and deletion.

Responsibilities
----------------
* Coordinate :class:`~app.repositories.customer_repository.CustomerRepository`
  (data access) with business rules specific to the customer domain.
* Enforce uniqueness guards before any write:
    - Email must be unique across all customer records.
    - Phone must be unique across all customer records when provided.
* Return strongly-typed Pydantic schemas — never raw ORM model instances — so
  the service layer's public contract is independent of the database schema.
* Raise domain-specific exceptions that the route layer translates into HTTP
  responses.  This module **must not** import FastAPI or ``HTTPException``.
* Respect the repository transaction boundary: the service never calls
  ``commit()``, ``rollback()``, or ``close()`` on the session.  These belong
  exclusively to the ``get_db`` FastAPI dependency or the test harness.

Domain exceptions
-----------------
* :class:`~app.core.domain_exceptions.CustomerNotFoundError`
      — requested customer does not exist (→ HTTP 404).
* :class:`~app.core.domain_exceptions.CustomerAlreadyExistsError`
      — email or phone uniqueness violation on create / update (→ HTTP 409).

Usage
-----
    from app.services.customer_service import CustomerService

    service = CustomerService(db)
    result  = await service.list_customers(page=1, page_size=20)
    print(result.total, len(result.items))
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain_exceptions import (
    CustomerAlreadyExistsError,
    CustomerNotFoundError,
)
from app.core.logging import get_logger
from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import (
    CustomerCreate,
    CustomerListResponse,
    CustomerResponse,
    CustomerSearchFilters,
    CustomerUpdate,
)

logger = get_logger(__name__)

# Hard cap: regardless of what the caller requests, we never return more than
# this many customer rows in a single page.  Mirrors the constraint defined in
# CustomerSearchFilters.page_size (le=100).
_PAGE_SIZE_MAX = 100


# --------------------------------------------------------------------------- #
# Internal pagination DTO                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class CustomerPage:
    """Internal DTO holding one page of customer results.

    Returned by :meth:`CustomerService.list_customers`.  A plain dataclass
    rather than a Pydantic model because it is an internal transport object —
    route handlers convert it to :class:`~app.schemas.customer.CustomerListResponse`
    themselves, decoupling the wire format from the service's internal shape.

    Attributes
    ----------
    total:
        Total number of customers matching the active filters (all pages).
    page:
        Current 1-indexed page number.
    page_size:
        Number of items on this page (may be less than requested on the last
        page).
    total_pages:
        ``ceil(total / page_size)``, pre-computed for the route layer.
        ``0`` when ``total`` is ``0``.
    items:
        Resolved :class:`~app.schemas.customer.CustomerResponse` objects for
        this page.
    """

    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[CustomerResponse] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Customer service                                                               #
# --------------------------------------------------------------------------- #


class CustomerService:
    """Orchestrates customer-management operations.

    All public methods are ``async`` coroutines and must be awaited.

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.  The
            session's lifecycle (commit / rollback / close) is managed by
            the ``get_db`` FastAPI dependency or by the test harness — **not**
            by this class.

    Example::

        service = CustomerService(db)
        page = await service.list_customers(page=1, page_size=20)
        for customer in page.items:
            print(customer.email, customer.is_active)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = CustomerRepository(db)

    # ---------------------------------------------------------------------- #
    # Read operations                                                          #
    # ---------------------------------------------------------------------- #

    async def get_customer(self, customer_id: uuid.UUID) -> CustomerResponse:
        """Return the public profile of a single customer.

        Args:
            customer_id: The UUID primary key of the customer to retrieve.

        Returns:
            A :class:`~app.schemas.customer.CustomerResponse` for the found
            customer.

        Raises:
            CustomerNotFoundError: If no customer with ``customer_id`` exists.

        Example::

            customer = await service.get_customer(uuid.UUID("a1b2c3d4-..."))
        """
        customer = await self._repo.get_by_id(customer_id)
        if customer is None:
            raise CustomerNotFoundError(
                f"No customer found with id '{customer_id}'."
            )

        return CustomerResponse.model_validate(customer)

    async def list_customers(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        is_active: bool | None = None,
    ) -> CustomerPage:
        """Return a filtered, paginated list of customers.

        All filter parameters are optional; omitting them returns the full
        customer population ordered by creation date (newest first).  Multiple
        filters are combined with AND semantics.

        Pagination values are clamped to safe bounds before the repository is
        called: ``page`` is clamped to ≥ 1; ``page_size`` is clamped to
        [1, ``_PAGE_SIZE_MAX``].

        Args:
            page:      1-indexed page number.  Values < 1 are clamped to 1.
            page_size: Items per page.  Values > ``_PAGE_SIZE_MAX`` are
                       clamped to the maximum.
            search:    Optional free-text term matched against first_name,
                       last_name, and email (case-insensitive).
            email:     Optional exact email filter (already lower-cased by
                       the schema layer when coming from a request).
            phone:     Optional exact phone filter (E.164).
            is_active: Optional boolean filter; ``None`` returns all customers.

        Returns:
            A :class:`CustomerPage` containing the page metadata and the
            resolved :class:`~app.schemas.customer.CustomerResponse` items.

        Example::

            page = await service.list_customers(
                page=2, page_size=20, search="Alice", is_active=True
            )
            print(f"Page {page.page}/{page.total_pages}")
        """
        # Clamp inputs to safe bounds
        page      = max(1, page)
        page_size = min(max(1, page_size), _PAGE_SIZE_MAX)

        filter_kwargs = dict(
            search=search,
            email=email,
            phone=phone,
            is_active=is_active,
        )

        customers = await self._repo.list_all(
            page=page,
            page_size=page_size,
            **filter_kwargs,
        )
        total = await self._repo.count_all(**filter_kwargs)
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        items = [CustomerResponse.model_validate(c) for c in customers]

        logger.debug(
            "customer_service.list_customers",
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            returned=len(items),
        )

        return CustomerPage(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=items,
        )

    async def list_customers_from_filters(
        self,
        filters: CustomerSearchFilters,
    ) -> CustomerPage:
        """Convenience overload that unpacks a :class:`CustomerSearchFilters` schema.

        Accepts the validated query-param schema produced by a FastAPI route
        handler and delegates to :meth:`list_customers`.  The route layer does
        not need to unpack each field manually.

        Args:
            filters: A validated :class:`~app.schemas.customer.CustomerSearchFilters`
                     instance (typically from ``Depends``).

        Returns:
            A :class:`CustomerPage` identical to :meth:`list_customers`.

        Example::

            page = await service.list_customers_from_filters(filters)
        """
        return await self.list_customers(
            page=filters.page,
            page_size=filters.page_size,
            search=filters.search,
            email=filters.email,
            phone=filters.phone,
            is_active=filters.is_active,
        )

    # ---------------------------------------------------------------------- #
    # Write operations                                                         #
    # ---------------------------------------------------------------------- #

    async def create_customer(self, payload: CustomerCreate) -> CustomerResponse:
        """Register a new customer record.

        Steps
        -----
        1. Guard: reject duplicate email — raise :class:`CustomerAlreadyExistsError`
           if another customer already uses ``payload.email``.
        2. Guard: reject duplicate phone — raise :class:`CustomerAlreadyExistsError`
           if ``payload.phone`` is provided and another customer already holds it.
        3. Construct the ORM object and persist it via the repository.
        4. Return the new customer's public profile.

        No ``commit()`` is called — the transaction boundary belongs to the
        caller / ``get_db`` dependency.

        Args:
            payload: Validated :class:`~app.schemas.customer.CustomerCreate`
                     schema.  Email is already lower-cased; phone is already
                     validated as E.164 by the schema layer.

        Returns:
            A :class:`~app.schemas.customer.CustomerResponse` for the created
            customer, with DB-generated fields (id, timestamps) populated.

        Raises:
            CustomerAlreadyExistsError: If ``payload.email`` or
                ``payload.phone`` is already registered.

        Example::

            customer = await service.create_customer(CustomerCreate(
                first_name="Alice",
                last_name="Tan",
                email="alice@example.com",
                phone="+60123456789",
            ))
            print(customer.id)
        """
        # 1. Email uniqueness guard
        if await self._repo.exists_by_email(payload.email):
            logger.warning(
                "customer_service.create.duplicate_email",
                email=payload.email,
            )
            raise CustomerAlreadyExistsError(
                f"A customer with the email '{payload.email}' already exists."
            )

        # 2. Phone uniqueness guard (only when a phone is supplied)
        if payload.phone and await self._repo.exists_by_phone(payload.phone):
            logger.warning(
                "customer_service.create.duplicate_phone",
                phone=payload.phone,
            )
            raise CustomerAlreadyExistsError(
                f"A customer with the phone '{payload.phone}' already exists."
            )

        # 3. Construct the ORM object (service decides initial is_active)
        customer = Customer(
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            phone=payload.phone,
            notes=payload.notes,
            is_active=True,            # new customers are always active
        )

        persisted = await self._repo.create(customer)

        logger.info(
            "customer_service.create.success",
            customer_id=str(persisted.id),
            email=persisted.email,
        )

        # 4. Return public schema (ORM mode)
        return CustomerResponse.model_validate(persisted)

    async def update_customer(
        self,
        customer_id: uuid.UUID,
        payload: CustomerUpdate,
    ) -> CustomerResponse:
        """Apply a partial update to an existing customer record.

        Only fields that are explicitly set in ``payload`` (i.e. not ``None``)
        are written to the database.  Fields absent from the payload are left
        unchanged.

        Steps
        -----
        1. Load the customer — raise :class:`CustomerNotFoundError` if absent.
        2. If a new email is supplied and differs from the current one, guard
           against email uniqueness collision.
        3. If a new phone is supplied and differs from the current one, guard
           against phone uniqueness collision.
        4. Patch only the supplied fields onto the ORM object.
        5. Persist via the repository (flush + refresh).
        6. Return the refreshed public profile.

        Args:
            customer_id: UUID of the customer to update.
            payload: Validated :class:`~app.schemas.customer.CustomerUpdate`
                     schema.  Fields set to ``None`` are intentionally absent
                     (not clearing — use the ``notes``/``phone`` explicit-null
                     behaviour for clearing).

        Returns:
            A :class:`~app.schemas.customer.CustomerResponse` reflecting the
            updated state.

        Raises:
            CustomerNotFoundError: If no customer with ``customer_id`` exists.
            CustomerAlreadyExistsError: If the new email or phone is already
                taken by a *different* customer.

        Example::

            updated = await service.update_customer(
                customer_id,
                CustomerUpdate(first_name="Alicia"),
            )
        """
        # 1. Load the target record
        customer = await self._repo.get_by_id(customer_id)
        if customer is None:
            raise CustomerNotFoundError(
                f"No customer found with id '{customer_id}'."
            )

        # 2. Email uniqueness guard (only when email is being changed)
        if payload.email is not None and payload.email != customer.email:
            if await self._repo.exists_by_email(payload.email):
                logger.warning(
                    "customer_service.update.duplicate_email",
                    customer_id=str(customer_id),
                    email=payload.email,
                )
                raise CustomerAlreadyExistsError(
                    f"A customer with the email '{payload.email}' already exists."
                )

        # 3. Phone uniqueness guard (only when phone is being changed to a new value)
        if (
            payload.phone is not None          # caller is setting a new phone value
            and payload.phone != customer.phone  # and it differs from the current one
            and await self._repo.exists_by_phone(payload.phone)
        ):
            logger.warning(
                "customer_service.update.duplicate_phone",
                customer_id=str(customer_id),
                phone=payload.phone,
            )
            raise CustomerAlreadyExistsError(
                f"A customer with the phone '{payload.phone}' already exists."
            )

        # 4. Patch only the supplied (non-None) fields onto the ORM object.
        #    Note: for phone and notes, the caller can set them to None
        #    *explicitly* via model_dump(exclude_unset=False) — but
        #    CustomerUpdate.model_dump() returns None for absent fields too.
        #    We use exclude_unset=True so that truly absent fields are
        #    skipped and only fields present in the JSON body are applied.
        for field_name, value in payload.model_dump(exclude_unset=True).items():
            setattr(customer, field_name, value)

        # 5. Persist and refresh
        updated = await self._repo.update(customer)

        logger.info(
            "customer_service.update.success",
            customer_id=str(updated.id),
            email=updated.email,
        )

        # 6. Return refreshed public schema
        return CustomerResponse.model_validate(updated)

    async def delete_customer(self, customer_id: uuid.UUID) -> None:
        """Hard-delete a customer record from the database.

        Prefer setting ``is_active=False`` via :meth:`update_customer` for
        soft-deletes that preserve audit history and prevent foreign-key
        orphans.  Only use this method for irreversible removals (e.g. GDPR
        data-erasure requests).

        Steps
        -----
        1. Load the customer — raise :class:`CustomerNotFoundError` if absent.
        2. Delete via the repository (flush, no commit).

        Args:
            customer_id: UUID of the customer to delete.

        Raises:
            CustomerNotFoundError: If no customer with ``customer_id`` exists.

        Example::

            await service.delete_customer(customer_id)
        """
        customer = await self._repo.get_by_id(customer_id)
        if customer is None:
            raise CustomerNotFoundError(
                f"No customer found with id '{customer_id}'."
            )

        await self._repo.delete(customer)

        logger.info(
            "customer_service.delete.success",
            customer_id=str(customer_id),
        )
