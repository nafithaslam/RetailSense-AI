"""
RetailSense AI — Domain Exceptions
=====================================
Shared, HTTP-agnostic exception hierarchy for the service layer.

All exceptions defined here are pure Python — they carry no FastAPI or
SQLAlchemy concerns and can be raised freely from services, repositories,
CLI scripts, or background tasks.  The route layer is responsible for
translating them into the appropriate ``HTTPException``.

Exception Hierarchy
-------------------
``RetailSenseError``               — base for all application exceptions
  ├── ``UserNotFoundError``        — lookup by id or email yielded no result
  ├── ``UserAlreadyExistsError``   — email collision on creation
  └── ``ForbiddenOperationError``  — actor lacks permission for the operation
                                     (e.g. admin attempting self-modification)

Usage
-----
    from app.core.domain_exceptions import UserNotFoundError, ForbiddenOperationError

    raise UserNotFoundError(f"No user found with id '{user_id}'.")
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# Base                                                                          #
# --------------------------------------------------------------------------- #


class RetailSenseError(Exception):
    """Base class for all RetailSense AI application exceptions.

    Catching this in a broad handler will capture any domain-layer error
    without accidentally swallowing unrelated Python built-ins.
    """


# --------------------------------------------------------------------------- #
# User-domain exceptions                                                        #
# --------------------------------------------------------------------------- #


class UserNotFoundError(RetailSenseError):
    """Raised when a user lookup by id or email yields no result.

    Callers should translate this into ``HTTP 404 Not Found``.

    Example::

        user = await repo.get_by_id(uid)
        if user is None:
            raise UserNotFoundError(f"No user found with id '{uid}'.")
    """


class UserAlreadyExistsError(RetailSenseError):
    """Raised when a new user cannot be created because the email is already
    registered.

    Callers should translate this into ``HTTP 409 Conflict``.

    Example::

        if await repo.exists_by_email(email):
            raise UserAlreadyExistsError(
                f"An account with the email '{email}' already exists."
            )
    """


class ForbiddenOperationError(RetailSenseError):
    """Raised when an actor attempts an operation they are not allowed to
    perform — even if they hold the correct role.

    The canonical example is an administrator trying to modify their own
    role or active status.  The route layer should translate this into
    ``HTTP 403 Forbidden``.

    Example::

        if actor_id == target_id:
            raise ForbiddenOperationError(
                "Administrators cannot modify their own role or active status."
            )
    """
