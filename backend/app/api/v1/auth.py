"""
RetailSense AI — Authentication API Routes (v1)
=================================================
Provides four HTTP endpoints that cover the full user authentication
lifecycle:

Endpoints
---------
POST /api/v1/auth/register
    Create a new user account and return a JWT access token immediately.

POST /api/v1/auth/login
    Authenticate with email + password (**JSON** body) and return a JWT
    together with the user profile.  Intended for frontend / mobile clients.

POST /api/v1/auth/token
    OAuth2-compatible token endpoint.  Accepts
    ``application/x-www-form-urlencoded`` with ``username`` and ``password``
    fields.  Returns ``{ "access_token": "...", "token_type": "bearer" }`` in
    plain snake_case as required by RFC 6749 / Swagger UI.  This is the
    endpoint ``OAuth2PasswordBearer(tokenUrl=...)`` points to.

GET  /api/v1/auth/me
    Return the profile of the currently authenticated user by validating
    the ``Authorization: Bearer <token>`` header.

Design principles
-----------------
* **Thin handlers** — every handler delegates entirely to
  :class:`~app.services.auth_service.AuthService`.  There is no business
  logic, no password handling, and no SQL in this module.
* **Domain exception translation** — :class:`~app.services.auth_service.AuthenticationError`,
  :class:`~app.services.auth_service.RegistrationError`, and
  :class:`~app.services.auth_service.UserNotFoundError` are caught at the
  route layer and converted into the appropriate ``HTTPException`` so the
  service layer stays free of HTTP concerns.
* **Strongly typed response models** — every route declares a
  ``response_model`` so FastAPI serialises and validates outbound data,
  strips unexpected fields, and generates accurate OpenAPI docs.
* **Security scheme** — the ``/me`` endpoint is protected by the
  ``oauth2_scheme`` declared in :mod:`app.dependencies.auth`.  FastAPI adds
  an **Authorize** button in Swagger UI automatically.

Usage (Swagger UI)
------------------
1. Expand ``POST /api/v1/auth/token`` → **Try it out** → enter username
   (= email) and password → **Execute** → copy ``access_token`` from the
   response.  *Or* click the top-level **Authorize** button — Swagger will
   POST the form to ``/token`` automatically.
2. Click **Authorize** → paste the token into the Bearer field → **Authorize**.
3. ``GET /api/v1/auth/me`` → see your profile.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.dependencies.auth import get_current_active_user
from app.schemas.auth import AuthenticatedUser, LoginRequest, RegisterRequest, Token
from app.services.auth_service import (
    AuthService,
    AuthenticationError,
    LoginResult,
    RegistrationError,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# --------------------------------------------------------------------------- #
# Dependency aliases — keeps handler signatures readable                        #
# --------------------------------------------------------------------------- #

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[AuthenticatedUser, Depends(get_current_active_user)]


# --------------------------------------------------------------------------- #
# Response schemas                                                              #
# --------------------------------------------------------------------------- #


class OAuth2TokenResponse(BaseModel):
    """Minimal OAuth2-compliant token response for the ``/token`` endpoint.

    This schema intentionally does **not** inherit :class:`~app.schemas.base.BaseSchema`
    so it never receives the camelCase ``alias_generator``.  RFC 6749 §5.1 and
    Swagger UI both require the field names to be exactly ``access_token`` and
    ``token_type`` in snake_case — any aliasing breaks token extraction.

    JSON shape (as required by the OAuth2 spec)::

        {
            "access_token": "eyJhbGci...",
            "token_type":   "bearer"
        }
    """

    access_token: str
    token_type: str = "bearer"


class AuthResponse(Token):
    """Combined response returned after a successful register or login.

    Extends :class:`~app.schemas.auth.Token` with the authenticated user's
    public profile so clients can seed their user-context store immediately,
    avoiding a second ``GET /me`` round-trip.

    JSON shape::

        {
            "accessToken": "eyJhbGci...",
            "tokenType":   "bearer",
            "expiresIn":   1800,
            "user": {
                "id":       "...",
                "fullName": "Jane Smith",
                "email":    "jane@example.com",
                "role":     "staff",
                "isActive": true
            }
        }
    """

    user: AuthenticatedUser


def _to_auth_response(result: LoginResult) -> AuthResponse:
    """Bundle a :class:`~app.services.auth_service.LoginResult` into an
    :class:`AuthResponse` for the wire.

    Args:
        result: The service-layer result containing token + user profile.

    Returns:
        An :class:`AuthResponse` schema instance.
    """
    return AuthResponse(
        access_token=result.token.access_token,
        token_type=result.token.token_type,
        expires_in=result.token.expires_in,
        user=result.user,
    )


# --------------------------------------------------------------------------- #
# POST /register                                                                #
# --------------------------------------------------------------------------- #

@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    description=(
        "Create a new user account with the provided details.  "
        "Returns a JWT access token and the new user's public profile.  "
        "The plain-text password is never stored or logged."
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "Account created; JWT issued."},
        status.HTTP_409_CONFLICT: {"description": "Email address already registered."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error in request body."},
    },
)
async def register(
    payload: RegisterRequest,
    db: DbDep,
) -> AuthResponse:
    """Register a new user and receive an access token.

    Args:
        payload: Registration data (full name, email, password, optional role).
        db: Async database session injected by ``get_db``.

    Returns:
        An :class:`AuthResponse` containing the signed JWT and the new
        user's public profile.

    Raises:
        HTTPException (409): When the email address is already in use.
    """
    service = AuthService(db)
    try:
        result = await service.register(payload)
    except RegistrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return _to_auth_response(result)


# --------------------------------------------------------------------------- #
# POST /token  — OAuth2 form-data endpoint (Swagger Authorize dialog)          #
# --------------------------------------------------------------------------- #


@router.post(
    "/token",
    response_model=OAuth2TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="OAuth2 token endpoint (form-data)",
    description=(
        "OAuth2-compatible token issuance endpoint.  Accepts credentials as "
        "``application/x-www-form-urlencoded`` (``username`` = email, "
        "``password``).  Returns ``access_token`` and ``token_type`` in "
        "plain snake_case as required by RFC 6749 and Swagger UI.\n\n"
        "**Use this endpoint via the Swagger Authorize dialog.**  "
        "Frontend / mobile clients should prefer ``POST /login`` which returns "
        "a richer JSON response including the user profile."
    ),
    include_in_schema=True,
    responses={
        status.HTTP_200_OK: {"description": "Credentials valid; access token issued."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid credentials or inactive account."},
    },
)
async def token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbDep,
) -> OAuth2TokenResponse:
    """Issue an OAuth2 access token from form-encoded credentials.

    This endpoint satisfies the contract expected by
    :data:`~app.dependencies.auth.oauth2_scheme` and Swagger UI's
    **Authorize** dialog.  Two deliberate deviations from the standard
    ``/token`` naming convention:

    * The route lives at ``/auth/token`` (not bare ``/token``) to keep all
      auth endpoints under a single prefix.
    * The ``username`` form field is treated as the user's **email address**.
      This is explicitly permitted by RFC 6749 — the field name is fixed by
      the spec but its semantic meaning is application-defined.

    Args:
        form_data: FastAPI-provided dependency that parses
            ``application/x-www-form-urlencoded`` into a
            :class:`~fastapi.security.OAuth2PasswordRequestForm` instance
            with ``username`` and ``password`` attributes.
        db: Async database session injected by ``get_db``.

    Returns:
        An :class:`OAuth2TokenResponse` with ``access_token`` and
        ``token_type`` in plain snake_case (no camelCase aliasing).

    Raises:
        HTTPException (401): When credentials are invalid or the account is
            inactive.  The ``WWW-Authenticate: Bearer`` header is included
            as required by RFC 6750.
    """
    # form_data.username holds the email — OAuth2 spec mandates the field
    # name 'username' but the value can be any identifier.
    credentials = LoginRequest(
        email=form_data.username,
        password=form_data.password,
    )

    service = AuthService(db)
    try:
        result = await service.authenticate(credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return OAuth2TokenResponse(access_token=result.token.access_token)


# --------------------------------------------------------------------------- #
# POST /login                                                                   #
# --------------------------------------------------------------------------- #

@router.post(
    "/login",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and obtain a JWT",
    description=(
        "Verify email + password credentials.  On success, returns a signed "
        "JWT access token and the authenticated user's public profile.  "
        "The error message is deliberately vague to prevent email enumeration."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Credentials valid; JWT issued."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid credentials or inactive account."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error in request body."},
    },
)
async def login(
    credentials: LoginRequest,
    db: DbDep,
) -> AuthResponse:
    """Authenticate a user and receive an access token.

    Args:
        credentials: Login payload containing email and plain-text password.
        db: Async database session injected by ``get_db``.

    Returns:
        An :class:`AuthResponse` containing the signed JWT and the
        authenticated user's public profile.

    Raises:
        HTTPException (401): When credentials are wrong or the account is
            inactive.
    """
    service = AuthService(db)
    try:
        result = await service.authenticate(credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return _to_auth_response(result)


# --------------------------------------------------------------------------- #
# GET /me                                                                       #
# --------------------------------------------------------------------------- #

@router.get(
    "/me",
    response_model=AuthenticatedUser,
    status_code=status.HTTP_200_OK,
    summary="Return the current user's profile",
    description=(
        "Decode and validate the ``Authorization: Bearer <token>`` header "
        "and return the corresponding user's public profile.  "
        "Requires a valid, non-expired access token."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Token valid; user profile returned."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing, expired, or invalid token."},
        status.HTTP_404_NOT_FOUND: {"description": "User referenced by token no longer exists."},
    },
)
async def get_me(
    current_user: CurrentUserDep,
) -> AuthenticatedUser:
    """Return the profile of the currently authenticated user.

    The heavy lifting (token decoding, DB lookup, active-status check) is
    done inside the :func:`~app.dependencies.auth.get_current_active_user`
    dependency.  This handler simply returns what the dependency resolved.

    Args:
        current_user: Resolved by ``get_current_active_user``; already
            validated and active.

    Returns:
        The authenticated user's :class:`~app.schemas.auth.AuthenticatedUser`
        profile.
    """
    return current_user
