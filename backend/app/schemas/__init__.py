"""
RetailSense AI — schemas package

Re-exports all domain schemas for convenient top-level imports:
    from app.schemas import UserCreate, UserResponse, UserLogin
    from app.schemas import Token, LoginRequest, RegisterRequest
"""

from app.schemas.auth import (  # noqa: F401
    AuthenticatedUser,
    LoginRequest,
    RefreshToken,
    RegisterRequest,
    Token,
    TokenPayload,
)
from app.schemas.user import UserCreate, UserLogin, UserResponse, UserRole  # noqa: F401

__all__ = [
    # auth
    "AuthenticatedUser",
    "LoginRequest",
    "RefreshToken",
    "RegisterRequest",
    "Token",
    "TokenPayload",
    # user
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserRole",
]
