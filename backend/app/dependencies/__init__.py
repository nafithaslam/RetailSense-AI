"""
RetailSense AI — dependencies package

FastAPI dependency functions that can be injected into any route handler
via ``Depends()``.  Each sub-module groups dependencies by domain:

* :mod:`app.dependencies.auth` — JWT extraction, user resolution,
  role-based access control guards.

Usage
-----
    from app.dependencies.auth import get_current_active_user
    from typing import Annotated
    from fastapi import Depends
    from app.schemas.auth import AuthenticatedUser

    CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_active_user)]

    @router.get("/protected")
    async def protected(user: CurrentUser) -> dict:
        return {"hello": user.full_name}
"""

from app.dependencies.auth import (  # noqa: F401
    get_current_active_user,
    get_current_user,
    oauth2_scheme,
)

__all__ = [
    "get_current_active_user",
    "get_current_user",
    "oauth2_scheme",
]
