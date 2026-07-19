"""
RetailSense AI — Authentication Service
=========================================
Orchestrates all authentication business logic: user registration, credential
verification, and JWT issuance.

Responsibilities
----------------
* Coordinate the :class:`~app.repositories.user_repository.UserRepository`
  (data access) with :mod:`app.core.security` (hashing + token creation).
* Enforce business rules:
    - Reject duplicate email registrations.
    - Reject inactive accounts at login.
    - Reject invalid credentials with a deliberately vague error message
      (prevents email enumeration).
* Return strongly-typed schemas — never raw ORM model instances — so that
  the service layer's public contract is independent of the database schema.
* Raise domain-specific exceptions that the route layer translates into
  HTTP responses.  The service layer **must not** import FastAPI or
  ``HTTPException``.

Dependency injection
--------------------
``AuthService`` accepts a :class:`~sqlalchemy.ext.asyncio.AsyncSession` in
its constructor.  This makes it trivially testable: pass an in-memory
SQLite session (or a ``MagicMock``) in tests without touching the real
database.

In FastAPI route handlers the service is injected via ``Depends``::

    async def login(
        credentials: LoginRequest,
        db: AsyncSession = Depends(get_db),
    ) -> LoginResponse:
        service = AuthService(db)
        return await service.authenticate(credentials)

Domain exceptions
-----------------
All custom exceptions are defined in this module so that the auth route
layer has a single import for everything auth-related.

* :class:`AuthenticationError`  — bad credentials or inactive account.
* :class:`RegistrationError`    — constraint violation during sign-up
                                  (e.g. duplicate email).
* :class:`UserNotFoundError`    — requested user does not exist.

Usage
-----
    from app.services.auth_service import AuthService, AuthenticationError

    service = AuthService(db)
    try:
        result = await service.authenticate(credentials)
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.core.domain_exceptions import UserNotFoundError
from app.models.user import User as UserModel, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    AuthenticatedUser,
    LoginRequest,
    RegisterRequest,
    Token,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Domain exceptions                                                             #
# --------------------------------------------------------------------------- #


class AuthenticationError(Exception):
    """Raised when credentials are invalid or the account is not usable.

    Intentionally vague in its public message to prevent email enumeration:
    an attacker should not be able to distinguish "wrong password" from
    "no such account".

    The route layer maps this to HTTP 401.
    """


class RegistrationError(Exception):
    """Raised when a registration attempt cannot be fulfilled.

    Examples
    --------
    * The email address is already registered.
    * A database constraint is violated.

    The route layer maps this to HTTP 409 (Conflict) or HTTP 400.
    """


# Re-export UserNotFoundError from the shared domain exceptions module so
# that existing importers of ``app.services.auth_service.UserNotFoundError``
# continue to work without modification.
UserNotFoundError = UserNotFoundError  # noqa: F811

# --------------------------------------------------------------------------- #
# Login / registration result DTO                                               #
# --------------------------------------------------------------------------- #


class LoginResult:
    """Bundles the token envelope and the authenticated user together.

    Returned by :meth:`AuthService.authenticate` and
    :meth:`AuthService.register` so the route handler can decide whether to
    embed both in a single response body or set the token in a cookie and
    return only the user profile.

    Attributes
    ----------
    token:
        The JWT envelope (:class:`~app.schemas.auth.Token`).
    user:
        The authenticated user's public profile
        (:class:`~app.schemas.auth.AuthenticatedUser`).
    """

    __slots__ = ("token", "user")

    def __init__(self, token: Token, user: AuthenticatedUser) -> None:
        self.token = token
        self.user = user


# --------------------------------------------------------------------------- #
# Auth service                                                                  #
# --------------------------------------------------------------------------- #


class AuthService:
    """Orchestrates registration, authentication, and JWT issuance.

    All public methods are ``async`` coroutines and must be awaited.

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.  The
            session's lifecycle (commit / rollback) is managed by the
            ``get_db`` FastAPI dependency or by the test harness — **not**
            by this class.

    Example::

        service = AuthService(db)
        result = await service.register(RegisterRequest(
            full_name="Jane Smith",
            email="jane@example.com",
            password="S3cur3P@ssword!",
        ))
        print(result.token.access_token)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = UserRepository(db)

    # ---------------------------------------------------------------------- #
    # Public API                                                               #
    # ---------------------------------------------------------------------- #

    async def register(self, payload: RegisterRequest) -> LoginResult:
        """Create a new user account and return a ready-to-use JWT.

        Steps
        -----
        1. Check that the email is not already registered — raise
           :class:`RegistrationError` if it is.
        2. Hash the plain-text password with Argon2id via
           :func:`~app.core.security.hash_password`.
        3. Persist the new user row via the repository.
        4. Issue an access token using the new user's UUID as ``sub``.
        5. Return a :class:`LoginResult` containing the token and the
           public user profile.

        Args:
            payload: Validated :class:`~app.schemas.auth.RegisterRequest`
                containing the new user's details.

        Returns:
            A :class:`LoginResult` with ``token`` and ``user`` populated.

        Raises:
            RegistrationError: If the email address is already in use.

        Example::

            result = await service.register(RegisterRequest(
                full_name="Jane Smith",
                email="jane@example.com",
                password="S3cur3P@ssword!",
            ))
        """
        # 1. Duplicate-email guard
        if await self._repo.exists_by_email(payload.email):
            logger.warning(
                "auth.register.duplicate_email",
                email=payload.email,
            )
            raise RegistrationError(
                f"An account with the email '{payload.email}' already exists."
            )

        # 2. Hash the password — plain text never touches the DB layer
        hashed = hash_password(payload.password)

        # 3. Persist the user — role is always STAFF for public registration.
        #    RegisterRequest no longer carries a role field (Sprint 3.3).
        #    Admin-initiated user creation goes through POST /users/ instead.
        user = await self._repo.create(
            full_name=payload.full_name,
            email=payload.email,
            password_hash=hashed,
            role=UserRole.STAFF,
        )

        logger.info(
            "auth.register.success",
            user_id=str(user.id),
            email=user.email,
            role=user.role.value,
        )

        # 4 + 5. Issue token and bundle the result
        return self._build_login_result(user)

    async def authenticate(self, credentials: LoginRequest) -> LoginResult:
        """Verify credentials and issue an access token.

        Steps
        -----
        1. Look up the user by email — raise :class:`AuthenticationError`
           with a generic message if not found (prevents enumeration).
        2. Verify the supplied password against the stored Argon2id hash —
           raise :class:`AuthenticationError` on mismatch.
        3. Check the ``is_active`` flag — raise :class:`AuthenticationError`
           if the account has been disabled.
        4. Issue an access token and return a :class:`LoginResult`.

        Args:
            credentials: Validated :class:`~app.schemas.auth.LoginRequest`
                containing email and plain-text password.

        Returns:
            A :class:`LoginResult` with ``token`` and ``user`` populated.

        Raises:
            AuthenticationError: If credentials are invalid, the account
                does not exist, or the account is inactive.

        Example::

            result = await service.authenticate(LoginRequest(
                email="jane@example.com",
                password="S3cur3P@ssword!",
            ))
        """
        _bad_credentials_msg = "Invalid email or password."

        # 1. User lookup — same generic error for not-found and wrong-password
        user = await self._repo.get_by_email(credentials.email)
        if user is None:
            logger.warning(
                "auth.login.user_not_found",
                email=credentials.email,
            )
            raise AuthenticationError(_bad_credentials_msg)

        # 2. Constant-time password comparison (Argon2)
        if not verify_password(credentials.password, user.password_hash):
            logger.warning(
                "auth.login.wrong_password",
                user_id=str(user.id),
                email=user.email,
            )
            raise AuthenticationError(_bad_credentials_msg)

        # 3. Active-account guard
        if not user.is_active:
            logger.warning(
                "auth.login.inactive_account",
                user_id=str(user.id),
                email=user.email,
            )
            raise AuthenticationError(
                "This account has been deactivated.  "
                "Please contact support."
            )

        logger.info(
            "auth.login.success",
            user_id=str(user.id),
            email=user.email,
            role=user.role.value,
        )

        # 4. Issue token and bundle the result
        return self._build_login_result(user)

    async def get_user_by_id(self, user_id: str) -> AuthenticatedUser:
        """Return the public profile of a user identified by their UUID string.

        Typically called by the ``get_current_user`` FastAPI dependency after
        it has decoded and validated a JWT.

        Args:
            user_id: The ``sub`` claim from the decoded JWT payload — a UUID
                serialised as a string.

        Returns:
            An :class:`~app.schemas.auth.AuthenticatedUser` schema instance.

        Raises:
            UserNotFoundError: If no user with the given UUID exists.
            ValueError: If ``user_id`` is not a valid UUID string.

        Example::

            payload = decode_token(raw_token)   # from security.py
            user    = await service.get_user_by_id(payload["sub"])
        """
        import uuid as _uuid

        try:
            uid = _uuid.UUID(user_id)
        except ValueError as exc:
            raise UserNotFoundError(
                f"'{user_id}' is not a valid UUID."
            ) from exc

        user = await self._repo.get_by_id(uid)
        if user is None:
            raise UserNotFoundError(
                f"No user found with id '{user_id}'."
            )

        return AuthenticatedUser.model_validate(user)

    # ---------------------------------------------------------------------- #
    # Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _build_token(self, user: "UserModel") -> Token:
        """Create a signed access token for the given user.

        Embeds the user's ``role`` in the JWT payload (Sprint 3.3) so that
        API gateways and frontend clients can inspect the role without a DB
        round-trip.  The FastAPI dependency chain still verifies against the
        database on every protected request.

        Args:
            user: The :class:`~app.models.user.User` ORM instance whose
                ``id`` becomes the ``sub`` claim and whose ``role`` becomes
                the ``role`` claim.

        Returns:
            A populated :class:`~app.schemas.auth.Token` schema instance.
        """
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            subject=str(user.id),
            role=user.role,
            expires_delta=expires_delta,
        )
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=int(expires_delta.total_seconds()),
        )

    def _build_login_result(self, user: object) -> LoginResult:
        """Bundle a token and a public user profile into a :class:`LoginResult`.

        Accepts any object that has the attributes expected by
        :class:`~app.schemas.auth.AuthenticatedUser` (i.e. any
        ``User`` ORM instance).

        Args:
            user: An ORM :class:`~app.models.user.User` instance with all
                fields populated (including DB-generated ones).

        Returns:
            A :class:`LoginResult` ready to be returned from a service
            method.
        """
        assert isinstance(user, UserModel)

        token = self._build_token(user)
        authenticated_user = AuthenticatedUser.model_validate(user)
        return LoginResult(token=token, user=authenticated_user)
