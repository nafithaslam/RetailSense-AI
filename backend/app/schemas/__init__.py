"""
RetailSense AI — schemas package

Re-exports all domain schemas for convenient top-level imports:
    from app.schemas import UserCreate, UserResponse, UserLogin
    from app.schemas import Token, LoginRequest, RegisterRequest
    from app.schemas import CustomerCreate, CustomerUpdate, CustomerResponse
    from app.schemas import CustomerListResponse, CustomerSearchFilters

Sprint history
--------------
3.2  Auth and User schemas added.
4.2.1  Customer schemas added.
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
from app.schemas.customer import (  # noqa: F401
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerListResponse,
    CustomerSearchFilters,
)

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
    # customer
    "CustomerCreate",
    "CustomerUpdate",
    "CustomerResponse",
    "CustomerListResponse",
    "CustomerSearchFilters",
]
