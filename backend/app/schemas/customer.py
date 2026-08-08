"""
RetailSense AI — Customer Pydantic Schemas
===========================================
Defines the request/response contracts for all Customer-related API
operations.

Schema hierarchy
-----------------
``CustomerBase``                  — shared validated fields (used by all).
  └── ``CustomerCreate``          — inbound: register a new customer.
  └── ``CustomerUpdate``          — inbound: partial update (all fields optional).
``CustomerResponse``              — outbound: full customer representation.
``CustomerListResponse``          — outbound: paginated envelope of customers.
``CustomerSearchFilters``         — inbound: query-string filter parameters for
                                    the list / search endpoint.

Design decisions
-----------------
* ``email`` is normalised to lower-case by a ``field_validator`` so that
  ``"Alice@STORE.com"`` and ``"alice@store.com"`` always resolve to the same
  record.  Pydantic's ``EmailStr`` enforces structural validity first.
* ``phone`` accepts an optional E.164-formatted string
  (e.g. ``"+60123456789"``).  Validation is intentionally liberal — a regex
  allows ``+`` followed by 7–15 digits — because global phone number formats
  vary widely and stricter rules break legitimate numbers.  The leading ``+``
  is required to make the international prefix unambiguous.
* ``first_name`` and ``last_name`` are stripped of surrounding whitespace.
  Internal whitespace is preserved (double-barrel names, initials, etc.).
* ``CustomerUpdate`` uses ``Optional`` for every mutable field with a default
  of ``None``, enabling true *partial* updates: only supplied fields are
  patched by the service layer.
* **``CustomerResponse`` does NOT inherit ``CustomerBase``** — it inherits
  ``BaseSchema`` directly and redeclares all fields as plain typed attributes
  without validators.  Data arriving from the ORM layer is already normalised
  and trusted; re-running E.164 / email validators on it would reject any
  legacy rows whose phone was stored without a ``+`` prefix.  Strict
  validators belong *only* on write schemas (``CustomerCreate``,
  ``CustomerUpdate``).
* ``CustomerResponse`` inherits ``TimestampSchema`` so API consumers always
  receive ``createdAt`` / ``updatedAt`` in camelCase via ``BaseSchema``.
* ``CustomerListResponse`` and ``CustomerSearchFilters`` deliberately do *not*
  inherit from ``CustomerBase`` — they carry no customer field data — but they
  still inherit ``BaseSchema`` for ORM-mode and camelCase aliases.
* Pagination parameters in ``CustomerSearchFilters`` mirror those in
  ``PaginatedUsersResponse`` so the paginated list contract is consistent
  across all resource endpoints.

Usage
-----
    from app.schemas.customer import (
        CustomerCreate,
        CustomerUpdate,
        CustomerResponse,
        CustomerListResponse,
        CustomerSearchFilters,
    )
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import EmailStr, Field, field_validator, model_validator

from app.schemas.base import BaseSchema, TimestampSchema


# --------------------------------------------------------------------------- #
# Internal constants                                                            #
# --------------------------------------------------------------------------- #

# E.164 phone pattern: optional leading '+', then 7–15 digits.
# The '+' is *required* here so the country code is never ambiguous.
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


# --------------------------------------------------------------------------- #
# Shared base                                                                   #
# --------------------------------------------------------------------------- #

class CustomerBase(BaseSchema):
    """Fields shared by *write* schemas — ``CustomerCreate`` and ``CustomerUpdate``.

    Strict input validators live here so they run exactly once and cannot
    drift between the two write use-cases.

    **This class is intentionally NOT inherited by ``CustomerResponse``.**
    Response schemas serialise data that is already stored in the database;
    re-running E.164 or email validators on ORM data would reject any row
    whose phone was stored in a format predating the current validation rules.
    Plain field declarations in ``CustomerResponse`` carry no such risk.

    Validation
    ----------
    * ``first_name`` / ``last_name`` — leading/trailing whitespace stripped.
    * ``email``                      — lower-cased and validated as a proper
                                       email address (Pydantic ``EmailStr``).
    * ``phone``                      — validated as E.164 when provided.
    """

    first_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        examples=["Alice"],
        description="Customer's given name.",
    )

    last_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        examples=["Tan"],
        description="Customer's family name.",
    )

    email: EmailStr = Field(
        ...,
        examples=["alice.tan@example.com"],
        description="Unique email address for the customer.",
    )

    phone: Optional[str] = Field(
        default=None,
        examples=["+60123456789"],
        description=(
            "Optional contact phone number in E.164 format "
            "(e.g. '+60123456789').  Must be unique when provided."
        ),
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        """Remove leading/trailing whitespace from name fields.

        Internal whitespace (e.g. double-barrel surnames, initials) is
        preserved as entered.
        """
        return value.strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Strip and lower-case the email so lookups are case-insensitive."""
        return value.strip().lower()

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        """Validate and normalise the phone number.

        Accepts ``None`` (field is optional).  When a value is present it
        must match E.164 format: a leading ``+`` followed by the country
        code and national number, 7–15 digits in total.

        Raises
        ------
        ValueError
            If the supplied string does not match the E.164 pattern.
        """
        if value is None:
            return None
        # Collapse any whitespace or dashes the user may have typed.
        cleaned = re.sub(r"[\s\-]", "", value.strip())
        if not _E164_RE.match(cleaned):
            raise ValueError(
                "Phone number must be in E.164 format, e.g. '+60123456789'."
            )
        return cleaned


# --------------------------------------------------------------------------- #
# Request schemas (inbound)                                                     #
# --------------------------------------------------------------------------- #

class CustomerCreate(CustomerBase):
    """Schema for registering a new customer record.

    Inherits all field definitions and validators from :class:`CustomerBase`.
    The additional ``notes`` field is optional free-text captured at point
    of registration (staff remarks, preferences, allergies, etc.).

    Attributes
    ----------
    first_name:
        Customer's given name.  Trimmed of surrounding whitespace.
    last_name:
        Customer's family name.  Trimmed of surrounding whitespace.
    email:
        Unique contact email.  Lower-cased before any DB interaction.
    phone:
        Optional E.164 phone number.  Must be unique across all customers
        when supplied.
    notes:
        Optional free-text remarks entered by staff at registration time.
        Max 1 000 characters.  Stored as-is (no normalisation).
    """

    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        examples=["Prefers email contact. Allergic to latex."],
        description=(
            "Optional free-text notes about the customer.  "
            "Max 1 000 characters."
        ),
    )


class CustomerUpdate(BaseSchema):
    """Schema for partially updating an existing customer record.

    Every field is **optional** with a default of ``None`` to support
    true partial updates: only fields explicitly supplied in the request
    body are forwarded to the service layer for patching.

    Fields absent from the payload are left unchanged in the database.

    Attributes
    ----------
    first_name:
        New given name.  Trimmed of surrounding whitespace when provided.
    last_name:
        New family name.  Trimmed of surrounding whitespace when provided.
    email:
        New email address.  Lower-cased when provided.  Must remain unique.
    phone:
        New phone number in E.164 format.  Pass ``null`` explicitly to
        *clear* a previously stored phone number.
    is_active:
        Set to ``false`` to soft-deactivate the customer without deleting
        the record.
    notes:
        Replacement notes.  Pass ``null`` to clear existing notes.
    """

    first_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=150,
        examples=["Alice"],
        description="Customer's given name.  Omit to leave unchanged.",
    )

    last_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=150,
        examples=["Tan"],
        description="Customer's family name.  Omit to leave unchanged.",
    )

    email: Optional[EmailStr] = Field(
        default=None,
        examples=["alice.tan@example.com"],
        description="New email address.  Must be unique.  Omit to leave unchanged.",
    )

    phone: Optional[str] = Field(
        default=None,
        examples=["+60123456789"],
        description=(
            "New phone number in E.164 format.  "
            "Send ``null`` explicitly to clear an existing number."
        ),
    )

    is_active: Optional[bool] = Field(
        default=None,
        examples=[True],
        description=(
            "Active status flag.  Set to ``false`` to soft-deactivate "
            "the customer.  Omit to leave unchanged."
        ),
    )

    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        examples=["Updated contact preference: WhatsApp only."],
        description=(
            "Replacement notes.  Send ``null`` to clear.  "
            "Omit to leave unchanged."
        ),
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_name(cls, value: Optional[str]) -> Optional[str]:
        """Strip surrounding whitespace from name fields when provided."""
        if value is None:
            return None
        return value.strip()

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: Optional[str]) -> Optional[str]:
        """Strip and lower-case the email when provided."""
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        """Validate and normalise the phone number when provided.

        ``None`` passes through unchanged (clearing a phone number is
        explicitly allowed).  A non-``None`` value must satisfy E.164.
        """
        if value is None:
            return None
        cleaned = re.sub(r"[\s\-]", "", value.strip())
        if not _E164_RE.match(cleaned):
            raise ValueError(
                "Phone number must be in E.164 format, e.g. '+60123456789'."
            )
        return cleaned

    @model_validator(mode="after")
    def at_least_one_field(self) -> "CustomerUpdate":
        """Require at least one field to be supplied in a PATCH request.

        An update payload where every field is ``None`` would be a no-op
        and most likely indicates a client-side bug.  Rejecting it early
        gives a clear error message rather than a silent 200 OK.
        """
        provided = {
            name
            for name, value in self.model_dump().items()
            if value is not None
        }
        if not provided:
            raise ValueError(
                "At least one field must be provided for an update."
            )
        return self


# --------------------------------------------------------------------------- #
# Response schema (outbound)                                                    #
# --------------------------------------------------------------------------- #

class CustomerResponse(TimestampSchema, BaseSchema):
    """Full public representation of a customer record returned by the API.

    Inherits
    --------
    * :class:`~app.schemas.base.TimestampSchema` — created_at, updated_at.
    * :class:`~app.schemas.base.BaseSchema`       — ORM-mode
      (``from_attributes=True``), camelCase JSON aliases.

    **Does NOT inherit** :class:`CustomerBase`.  Data returned from the ORM
    layer is already normalised and stored; re-running strict validators (E.164
    phone regex, email format check) on read-path data would raise
    ``ValidationError`` on any row whose phone was stored before the current
    validation rules were introduced.  All fields are therefore declared as
    plain typed attributes with no validators.

    Attributes
    ----------
    id:
        Unique identifier for the customer (UUID v4).
    first_name:
        Customer's given name.
    last_name:
        Customer's family name.
    email:
        Normalised (lower-case) email address as stored in the database.
    phone:
        Phone number as stored in the database, or ``null`` if not on record.
    is_active:
        Whether the customer account is currently active.
    notes:
        Free-text remarks, or ``null`` if none recorded.
    created_at:
        UTC timestamp when the record was created.
    updated_at:
        UTC timestamp of the most recent update.
    """

    id: uuid.UUID = Field(
        ...,
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
        description="Unique identifier for the customer.",
    )

    first_name: str = Field(
        ...,
        examples=["Alice"],
        description="Customer's given name.",
    )

    last_name: str = Field(
        ...,
        examples=["Tan"],
        description="Customer's family name.",
    )

    email: str = Field(
        ...,
        examples=["alice.tan@example.com"],
        description="Normalised email address as stored in the database.",
    )

    phone: Optional[str] = Field(
        default=None,
        examples=["+60123456789"],
        description="Phone number as stored, or ``null`` if not on record.",
    )

    is_active: bool = Field(
        ...,
        examples=[True],
        description="Whether the customer account is currently active.",
    )

    notes: Optional[str] = Field(
        default=None,
        examples=["Prefers email contact."],
        description="Free-text notes recorded by staff.  ``null`` if none.",
    )


# --------------------------------------------------------------------------- #
# Paginated list response (outbound)                                            #
# --------------------------------------------------------------------------- #

class CustomerListResponse(BaseSchema):
    """Paginated envelope returned by ``GET /customers/``.

    Mirrors the structure of :class:`~app.schemas.user.PaginatedUsersResponse`
    to keep the list-endpoint contract consistent across all resource types.

    Attributes
    ----------
    total:
        Total number of customers matching the active filters (across all
        pages), enabling clients to render pagination controls accurately.
    page:
        Current 1-indexed page number.
    page_size:
        Number of items per page as applied by the server (may be lower
        than requested if the server enforces a maximum).
    total_pages:
        Total number of pages for the current filter set.  Computed as
        ``ceil(total / page_size)``; ``0`` when ``total`` is ``0``.
    items:
        Customer records for the current page.  Empty list when the page
        number exceeds the last page.
    """

    total: int = Field(
        ...,
        ge=0,
        description="Total number of matching customers across all pages.",
        examples=[128],
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
        description="Number of customer records returned per page.",
        examples=[20],
    )

    total_pages: int = Field(
        ...,
        ge=0,
        description="Total number of pages for the current result set.",
        examples=[7],
    )

    items: list[CustomerResponse] = Field(
        ...,
        description="Customer records for the current page.",
    )


# --------------------------------------------------------------------------- #
# Search / filter parameters (inbound)                                          #
# --------------------------------------------------------------------------- #

class CustomerSearchFilters(BaseSchema):
    """Query-string filter parameters for the customer list / search endpoint.

    All fields are optional; omitting a field means "no filter on this
    dimension".  Multiple filters are combined with AND semantics by the
    service / repository layer.

    Attributes
    ----------
    search:
        Free-text search term matched against ``first_name``, ``last_name``,
        and ``email`` (case-insensitive ILIKE / full-text search).  Useful
        for the POS quick-search bar.
    email:
        Exact email look-up (lower-cased before matching).
    phone:
        Exact phone look-up (E.164).
    is_active:
        Filter by active status.  ``None`` returns both active and inactive
        records (default admin view); ``true`` restricts to active customers;
        ``false`` lists deactivated accounts.
    page:
        1-indexed page number for pagination.  Defaults to ``1``.
    page_size:
        Number of results per page.  Defaults to ``20``, capped at ``100``
        by the server to prevent runaway queries.

    Validation
    ----------
    * ``search`` is stripped of surrounding whitespace; an all-whitespace
      value is treated as absent (i.e. becomes ``None``).
    * ``email`` is lower-cased to match the normalised DB value.
    * ``page`` must be ≥ 1; ``page_size`` must be between 1 and 100.
    """

    search: Optional[str] = Field(
        default=None,
        max_length=255,
        examples=["Alice"],
        description=(
            "Free-text search against first_name, last_name, and email."
        ),
    )

    email: Optional[str] = Field(
        default=None,
        examples=["alice.tan@example.com"],
        description="Exact email look-up.  Lower-cased before matching.",
    )

    phone: Optional[str] = Field(
        default=None,
        examples=["+60123456789"],
        description="Exact phone look-up (E.164).",
    )

    is_active: Optional[bool] = Field(
        default=None,
        examples=[True],
        description=(
            "Filter by active status.  ``null`` returns all customers."
        ),
    )

    page: int = Field(
        default=1,
        ge=1,
        examples=[1],
        description="1-indexed page number.  Defaults to 1.",
    )

    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        examples=[20],
        description="Results per page.  Min 1, max 100.  Defaults to 20.",
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("search", mode="before")
    @classmethod
    def strip_search(cls, value: Optional[str]) -> Optional[str]:
        """Strip surrounding whitespace from the free-text search term.

        An all-whitespace string is normalised to ``None`` so that the
        repository layer never receives an empty ILIKE pattern.
        """
        if value is None:
            return None
        stripped = value.strip()
        # Treat an all-whitespace string as absent rather than an empty search.
        return stripped if stripped else None

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: Optional[str]) -> Optional[str]:
        """Lower-case and strip the email filter to match the stored value."""
        if value is None:
            return None
        return value.strip().lower()
