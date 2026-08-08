"""
RetailSense AI — Customer API Routes (v1)
==========================================
Provides five HTTP endpoints for customer management.  All endpoints require
an authenticated, active user; most require a fine-grained permission enforced
by the RBAC dependency infrastructure introduced in Sprint 3.3.

Endpoints
---------
POST   /api/v1/customers/
    Register a new customer record.
    **Guard**: any authenticated, active user
    (``customers:write`` is implicitly satisfied by all roles that can log in
    and is checked explicitly via :data:`CustomerWriteDep`).

GET    /api/v1/customers/
    Paginated, filterable customer list.
    **Guard**: ``customers:read`` (ADMIN, MANAGER, STAFF)

GET    /api/v1/customers/{customer_id}
    Retrieve a single customer by UUID.
    **Guard**: ``customers:read`` (ADMIN, MANAGER, STAFF)

PATCH  /api/v1/customers/{customer_id}
    Partial update of an existing customer.
    **Guard**: ``customers:write`` (ADMIN, MANAGER)

DELETE /api/v1/customers/{customer_id}
    Hard-delete a customer record.
    **Guard**: ``customers:delete`` (ADMIN only)

Design principles
-----------------
* **Thin handlers** — every handler delegates entirely to
  :class:`~app.services.customer_service.CustomerService`.  No business
  logic, SQL, or ORM access lives in this module.
* **Domain exception translation** —
  :class:`~app.core.domain_exceptions.CustomerNotFoundError` and
  :class:`~app.core.domain_exceptions.CustomerAlreadyExistsError` are caught
  here and converted into the correct ``HTTPException``.
* **RBAC via dependency injection** — guards are declared as
  ``Annotated[AuthenticatedUser, <guard>]`` parameters.  FastAPI evaluates the
  full chain (token decode → DB lookup → active check → permission check)
  before the handler body runs.
* **No commit / rollback / close** — transaction lifecycle is managed
  exclusively by the ``get_db`` dependency.
* **Response assembly** — route handlers convert the service's internal
  :class:`~app.services.customer_service.CustomerPage` DTO to the wire-format
  :class:`~app.schemas.customer.CustomerListResponse` Pydantic schema.

Usage (Swagger UI)
------------------
1. Obtain a JWT via ``POST /api/v1/auth/token``.
2. Click **Authorize** → paste the token.
3. ``GET /api/v1/customers/``              → browse customers.
4. ``POST /api/v1/customers/``             → register a new customer.
5. ``PATCH /api/v1/customers/{id}``        → update a customer.
6. ``DELETE /api/v1/customers/{id}``       → delete a customer (ADMIN only).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain_exceptions import (
    CustomerAlreadyExistsError,
    CustomerNotFoundError,
)
from app.core.permissions import Permission
from app.database.session import get_db
from app.dependencies.auth import (
    get_current_active_user,
    require_permission,
)
from app.schemas.auth import AuthenticatedUser
from app.schemas.customer import (
    CustomerCreate,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
)
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/customers", tags=["Customers"])


# --------------------------------------------------------------------------- #
# Dependency aliases — keeps handler signatures readable                        #
# --------------------------------------------------------------------------- #

DbDep = Annotated[AsyncSession, Depends(get_db)]

#: Any authenticated active user — used for POST (customer creation).
ActiveUserDep = Annotated[AuthenticatedUser, Depends(get_current_active_user)]

#: customers:read — ADMIN, MANAGER, STAFF
CustomerReadDep = Annotated[
    AuthenticatedUser,
    Depends(require_permission(Permission.CUSTOMERS_READ)),
]

#: customers:write — ADMIN, MANAGER
CustomerWriteDep = Annotated[
    AuthenticatedUser,
    Depends(require_permission(Permission.CUSTOMERS_WRITE)),
]

#: customers:delete — ADMIN only
CustomerDeleteDep = Annotated[
    AuthenticatedUser,
    Depends(require_permission(Permission.CUSTOMERS_DELETE)),
]


# --------------------------------------------------------------------------- #
# POST /customers/  — register a new customer                                   #
# --------------------------------------------------------------------------- #


@router.post(
    "/",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new customer",
    description=(
        "Create a new customer record.  The new customer is always set to "
        "``is_active = true`` by the service layer.  "
        "Email and phone numbers must be unique across all customers.  "
        "**Requires ``customers:write`` permission (ADMIN or MANAGER).**"
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "Customer created successfully."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient permissions."},
        status.HTTP_409_CONFLICT: {"description": "Email or phone already registered."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error."},
    },
)
async def create_customer(
    payload: CustomerCreate,
    _actor: CustomerWriteDep,
    db: DbDep,
) -> CustomerResponse:
    """Register a new customer record.

    Args:
        payload: Validated :class:`~app.schemas.customer.CustomerCreate` body.
        _actor: Authenticated user with ``customers:write`` permission (guard
            only — not used in the handler body).
        db: Async database session injected by ``get_db``.

    Returns:
        The newly created :class:`~app.schemas.customer.CustomerResponse` with
        all DB-generated fields (id, timestamps) populated.

    Raises:
        HTTPException (409): When ``payload.email`` or ``payload.phone`` is
            already registered to another customer.
    """
    service = CustomerService(db)
    try:
        customer = await service.create_customer(payload)
    except CustomerAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return customer


# --------------------------------------------------------------------------- #
# GET /customers/  — paginated, filterable list                                 #
# --------------------------------------------------------------------------- #


@router.get(
    "/",
    response_model=CustomerListResponse,
    status_code=status.HTTP_200_OK,
    summary="List customers with filtering and pagination",
    description=(
        "Return a paginated list of customers.  All filter parameters are "
        "optional and combined with AND semantics.  Results are ordered by "
        "creation date (newest first).  "
        "**Requires ``customers:read`` permission (ADMIN, MANAGER, or STAFF).**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Paginated customer list returned."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient permissions."},
    },
)
async def list_customers(
    _actor: CustomerReadDep,
    db: DbDep,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Items per page (max 100)."
    ),
    search: Optional[str] = Query(
        default=None,
        max_length=255,
        description="Free-text search on first_name, last_name, and email.",
    ),
    email: Optional[str] = Query(
        default=None, description="Exact email filter (case-insensitive)."
    ),
    phone: Optional[str] = Query(
        default=None, description="Exact phone filter (E.164)."
    ),
    is_active: Optional[bool] = Query(
        default=None, description="Filter by active status.  Omit to return all."
    ),
) -> CustomerListResponse:
    """Return a filtered, paginated list of customers.

    Args:
        _actor: Authenticated user with ``customers:read`` (guard only).
        db: Async database session.
        page: 1-indexed page number.
        page_size: Items per page; clamped at 100 by the service.
        search: Optional ILIKE search across first_name, last_name, email.
        email: Optional exact email filter.
        phone: Optional exact phone filter.
        is_active: Optional boolean; omit to return both active and inactive.

    Returns:
        A :class:`~app.schemas.customer.CustomerListResponse` envelope.
    """
    service = CustomerService(db)
    result = await service.list_customers(
        page=page,
        page_size=page_size,
        search=search,
        email=email,
        phone=phone,
        is_active=is_active,
    )

    return CustomerListResponse(
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
        items=result.items,
    )


# --------------------------------------------------------------------------- #
# GET /customers/{customer_id}  — single customer profile                      #
# --------------------------------------------------------------------------- #


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single customer by ID",
    description=(
        "Retrieve the full profile of a single customer by their UUID.  "
        "**Requires ``customers:read`` permission (ADMIN, MANAGER, or STAFF).**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Customer profile returned."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient permissions."},
        status.HTTP_404_NOT_FOUND: {"description": "Customer not found."},
    },
)
async def get_customer(
    customer_id: uuid.UUID,
    _actor: CustomerReadDep,
    db: DbDep,
) -> CustomerResponse:
    """Return the full profile of a single customer.

    Args:
        customer_id: UUID path parameter identifying the customer.
        _actor: Authenticated user with ``customers:read`` (guard only).
        db: Async database session.

    Returns:
        The :class:`~app.schemas.customer.CustomerResponse` for the customer.

    Raises:
        HTTPException (404): When no customer with ``customer_id`` exists.
    """
    service = CustomerService(db)
    try:
        customer = await service.get_customer(customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return customer


# --------------------------------------------------------------------------- #
# PATCH /customers/{customer_id}  — partial update                              #
# --------------------------------------------------------------------------- #


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially update a customer",
    description=(
        "Apply a partial update to an existing customer.  Only fields present "
        "in the request body are modified; omitted fields remain unchanged.  "
        "To clear ``phone`` or ``notes``, send ``null`` explicitly.  "
        "**Requires ``customers:write`` permission (ADMIN or MANAGER).**"
    ),
    responses={
        status.HTTP_200_OK: {"description": "Customer updated successfully."},
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient permissions."},
        status.HTTP_404_NOT_FOUND: {"description": "Customer not found."},
        status.HTTP_409_CONFLICT: {"description": "Email or phone already taken."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error."},
    },
)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    _actor: CustomerWriteDep,
    db: DbDep,
) -> CustomerResponse:
    """Partially update a customer record.

    Args:
        customer_id: UUID path parameter identifying the customer to update.
        payload: Validated :class:`~app.schemas.customer.CustomerUpdate` body.
            Only supplied (non-``None``) fields are applied.
        _actor: Authenticated user with ``customers:write`` (guard only).
        db: Async database session.

    Returns:
        The refreshed :class:`~app.schemas.customer.CustomerResponse`.

    Raises:
        HTTPException (404): When the customer does not exist.
        HTTPException (409): When the new email or phone is already taken by
            a different customer.
    """
    service = CustomerService(db)
    try:
        updated = await service.update_customer(customer_id, payload)
    except CustomerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except CustomerAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return updated


# --------------------------------------------------------------------------- #
# DELETE /customers/{customer_id}  — hard delete                                #
# --------------------------------------------------------------------------- #


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a customer (admin only)",
    description=(
        "Permanently delete a customer record.  This action is irreversible.  "
        "To preserve audit history, prefer setting ``is_active = false`` via "
        "``PATCH /customers/{customer_id}`` instead.  "
        "**Requires ``customers:delete`` permission (ADMIN only).**"
    ),
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Insufficient permissions."},
        status.HTTP_404_NOT_FOUND: {"description": "Customer not found."},
    },
)
async def delete_customer(
    customer_id: uuid.UUID,
    _actor: CustomerDeleteDep,
    db: DbDep,
) -> Response:
    """Hard-delete a customer record.

    Args:
        customer_id: UUID path parameter identifying the customer to delete.
        _actor: Authenticated user with ``customers:delete`` (ADMIN guard only).
        db: Async database session.

    Returns:
        Empty :class:`~fastapi.responses.Response` with HTTP 204 No Content.

    Raises:
        HTTPException (404): When the customer does not exist.
    """
    service = CustomerService(db)
    try:
        await service.delete_customer(customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
