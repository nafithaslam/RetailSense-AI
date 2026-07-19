"""
RetailSense AI — Alembic Migration Environment
===============================================
Configures Alembic for SQLAlchemy 2.x Async (asyncpg driver).

Key design decisions
--------------------
* Async engine   — ``create_async_engine`` is used so the asyncpg driver is
  never bypassed.  The sync bridge ``connection.run_sync`` hands a plain
  ``Connection`` to Alembic's internal machinery, which is the officially
  supported pattern for async engines in Alembic ≥ 1.9.

* No URL in alembic.ini — ``DATABASE_URL`` is loaded from the project's
  ``.env`` file via the existing ``app.core.config.settings`` singleton.
  This prevents credentials from living in a committed file.

* Metadata discovery — ``import app.models`` is executed before
  ``target_metadata`` is assigned.  SQLAlchemy registers a table into
  ``Base.metadata`` only when its ORM class is first imported, so this
  import is mandatory for ``--autogenerate`` to detect all tables.

* Offline mode — generates a plain SQL script without a live DB connection.
  The asyncpg driver is not involved; the URL is passed as a plain string
  with the ``+asyncpg`` dialect prefix stripped for compatibility.

Running migrations
------------------
    # Always run from the backend/ directory so .env resolves correctly.
    alembic revision --autogenerate -m "<description>"
    alembic upgrade head
    alembic downgrade base
    alembic current
    alembic history
"""

from __future__ import annotations

import asyncio
import re
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

from alembic import context

# ---------------------------------------------------------------------------
# Alembic config object — access to values in alembic.ini
# ---------------------------------------------------------------------------

config = context.config

# Set up Python logging from alembic.ini's [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------

# Load the DATABASE_URL from .env via the pydantic-settings singleton.
# This must happen before any SQLAlchemy engine is created.
from app.core.config import settings  # noqa: E402

# Import all ORM model modules so every mapped class registers itself into
# Base.metadata.  Without this, autogenerate produces an empty migration.
# New models must be added to app/models/__init__.py — not here.
import app.models  # noqa: E402, F401

# Import Base *after* the models so metadata is fully populated.
from app.database.session import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Metadata target — required for --autogenerate
# ---------------------------------------------------------------------------

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Naming conventions
# ---------------------------------------------------------------------------
# Explicit constraint naming lets Alembic generate DROP/ALTER statements
# reliably, even for constraints that PostgreSQL created with auto-names.
# These names match what SQLAlchemy recommends in its migration docs.

target_metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# ---------------------------------------------------------------------------
# Helper — strip async dialect prefix for offline mode
# ---------------------------------------------------------------------------

def _sync_url(url: str) -> str:
    """Return a sync-driver URL for offline SQL generation.

    asyncpg cannot be used without a live connection, so offline mode
    uses the ``postgresql`` dialect instead of ``postgresql+asyncpg``.
    """
    return re.sub(r"\+asyncpg", "", url, count=1)


# ---------------------------------------------------------------------------
# Offline mode — emit raw SQL without a DB connection
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates a plain SQL script that can be inspected or applied manually.
    No database connection is required; the asyncpg driver is not used.
    """
    url = _sync_url(settings.DATABASE_URL)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include the schema name in CREATE/DROP statements when needed.
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect async, run migrations via run_sync bridge
# ---------------------------------------------------------------------------

def _do_run_migrations(connection) -> None:
    """Synchronous inner function called by run_sync.

    Receives a raw ``Connection`` (not async) from SQLAlchemy's bridge and
    hands it to Alembic's context machinery to execute the migrations.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        # Render server defaults in the migration so autogenerate catches them.
        render_as_batch=False,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine.

    Creates a short-lived ``AsyncEngine`` with ``NullPool`` (no connection
    pooling) so each ``alembic`` CLI invocation gets a fresh connection and
    releases it cleanly on exit.
    """
    connectable = create_async_engine(
        settings.DATABASE_URL,
        # NullPool is correct for CLI/migration contexts — connections must
        # not be reused across migration steps or kept alive after the run.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
