"""
RetailSense AI — services package

Service classes encapsulate business logic and sit between API route
handlers (thin) and the database layer.  Each domain area (products,
inventory, orders, …) should have its own service module here.

Pattern
-------
    # app/services/product_service.py
    class ProductService:
        def __init__(self, db: AsyncSession) -> None:
            self.db = db

        async def list_products(self) -> list[Product]:
            result = await self.db.execute(select(Product))
            return list(result.scalars().all())

Sprint history
--------------
3.2  AuthService, UserService added.
4.2.3  CustomerService added.
"""

from app.services.auth_service import (  # noqa: F401
    AuthService,
    AuthenticationError,
    LoginResult,
    RegistrationError,
    UserNotFoundError,
)
from app.services.customer_service import (  # noqa: F401
    CustomerService,
    CustomerPage,
)

__all__ = [
    # auth / user
    "AuthService",
    "AuthenticationError",
    "LoginResult",
    "RegistrationError",
    "UserNotFoundError",
    # customer
    "CustomerService",
    "CustomerPage",
]
