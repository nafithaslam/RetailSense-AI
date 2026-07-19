"""
RetailSense AI — Permission Registry
======================================
Central, code-defined source of truth for fine-grained capabilities in the
platform.  No database table; permissions are derived entirely from a user's
role via the ``ROLE_PERMISSIONS`` matrix.

Design
------
* ``Permission`` is a ``str`` enum so values are JSON-serialisable and
  comparable to plain strings without an extra ``.value`` call.
* ``ROLE_PERMISSIONS`` maps each :class:`~app.models.user.UserRole` to an
  immutable ``frozenset`` of ``Permission`` values — immutable so that guards
  cannot accidentally mutate the matrix at runtime.
* ``has_permission()`` is a pure function with no side-effects; it is safe
  to call from any layer (dependencies, services, tests, CLI).

Permission Naming Convention
-----------------------------
``<domain>:<action>``

  * ``domain`` — the resource area (``users``, ``inventory``, ``sales``, …)
  * ``action`` — one of ``read``, ``write``, ``delete``, ``export``,
    ``assign_role``, ``deactivate``

Adding new permissions
----------------------
1. Add the value to the ``Permission`` enum.
2. Add it to the correct role sets in ``ROLE_PERMISSIONS``.
3. Write a unit test in ``tests/unit/test_permissions.py``.
   No migration is required — permissions live only in Python.

Role matrix summary
--------------------
+-------------------+-------+---------+-------+
| Permission        | ADMIN | MANAGER | STAFF |
+-------------------+-------+---------+-------+
| users:read        |  ✅   |   ✅    |       |
| users:write       |  ✅   |         |       |
| users:assign_role |  ✅   |         |       |
| users:deactivate  |  ✅   |         |       |
| inventory:read    |  ✅   |   ✅    |  ✅   |
| inventory:write   |  ✅   |   ✅    |       |
| inventory:delete  |  ✅   |         |       |
| sales:read        |  ✅   |   ✅    |  ✅   |
| sales:write       |  ✅   |   ✅    |  ✅   |
| reports:read      |  ✅   |   ✅    |       |
| reports:export    |  ✅   |         |       |
| suppliers:read    |  ✅   |   ✅    |  ✅   |
| suppliers:write   |  ✅   |   ✅    |       |
| settings:read     |  ✅   |   ✅    |       |
| settings:write    |  ✅   |         |       |
+-------------------+-------+---------+-------+

Usage
-----
    from app.core.permissions import Permission, has_permission
    from app.models.user import UserRole

    if has_permission(UserRole.MANAGER, Permission.INVENTORY_WRITE):
        # manager can write inventory
        ...
"""

from __future__ import annotations

import enum

from app.models.user import UserRole


# --------------------------------------------------------------------------- #
# Permission enum                                                               #
# --------------------------------------------------------------------------- #


class Permission(str, enum.Enum):
    """Fine-grained capability identifiers.

    Inheriting from ``str`` ensures values are JSON-serialisable and
    comparable directly to string literals without calling ``.value``.
    """

    # --- User management ---------------------------------------------------
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    USERS_ASSIGN_ROLE = "users:assign_role"
    USERS_DEACTIVATE = "users:deactivate"

    # --- Inventory ---------------------------------------------------------
    INVENTORY_READ = "inventory:read"
    INVENTORY_WRITE = "inventory:write"
    INVENTORY_DELETE = "inventory:delete"

    # --- Sales -------------------------------------------------------------
    SALES_READ = "sales:read"
    SALES_WRITE = "sales:write"

    # --- Reports -----------------------------------------------------------
    REPORTS_READ = "reports:read"
    REPORTS_EXPORT = "reports:export"

    # --- Suppliers ---------------------------------------------------------
    SUPPLIERS_READ = "suppliers:read"
    SUPPLIERS_WRITE = "suppliers:write"

    # --- Settings ----------------------------------------------------------
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"


# --------------------------------------------------------------------------- #
# Role → permissions matrix                                                    #
# --------------------------------------------------------------------------- #

#: Authoritative mapping of each :class:`~app.models.user.UserRole` to the
#: ``frozenset`` of :class:`Permission` values granted to that role.
#: ``frozenset`` is used instead of ``set`` so the matrix is immutable at
#: runtime — accidental mutation by guard code or tests is impossible.
ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset(Permission),  # ADMIN receives every permission

    UserRole.MANAGER: frozenset({
        Permission.USERS_READ,
        Permission.INVENTORY_READ,
        Permission.INVENTORY_WRITE,
        Permission.SALES_READ,
        Permission.SALES_WRITE,
        Permission.REPORTS_READ,
        Permission.SUPPLIERS_READ,
        Permission.SUPPLIERS_WRITE,
        Permission.SETTINGS_READ,
    }),

    UserRole.STAFF: frozenset({
        Permission.INVENTORY_READ,
        Permission.SALES_READ,
        Permission.SALES_WRITE,
        Permission.SUPPLIERS_READ,
    }),
}


# --------------------------------------------------------------------------- #
# Public helper                                                                 #
# --------------------------------------------------------------------------- #


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Return ``True`` if *role* includes *permission*.

    Pure function with no side-effects — safe to call from any layer.

    Args:
        role: The :class:`~app.models.user.UserRole` to check against.
        permission: The specific :class:`Permission` capability to test.

    Returns:
        ``True`` if ``permission`` is in ``ROLE_PERMISSIONS[role]``,
        ``False`` otherwise.

    Example::

        from app.core.permissions import Permission, has_permission
        from app.models.user import UserRole

        assert has_permission(UserRole.ADMIN, Permission.SETTINGS_WRITE)
        assert not has_permission(UserRole.STAFF, Permission.REPORTS_READ)
    """
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
