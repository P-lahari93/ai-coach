"""
Alembic environment configuration.

Uses synchronous psycopg2 driver for migrations (Alembic doesn't support async).
The DATABASE_URL from Settings is converted from asyncpg → psycopg2 here.
All models are imported via app.models so autogenerate detects every table.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Import all models so Base.metadata is populated ──────────────────────────
from app.database.base import Base
import app.models  # noqa: F401 — registers all ORM models with Base

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# ── Database URL ─────────────────────────────────────────────────────────────

def get_sync_url() -> str:
    """
    Convert the async DATABASE_URL (asyncpg) to a sync URL (psycopg2)
    for Alembic's synchronous migration runner.
    """
    from app.core.config import settings

    url = settings.DATABASE_URL
    # Replace async driver with sync driver
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    return url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — emit SQL to stdout/file.
    Useful for reviewing migrations before applying to production.
    """
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — apply directly to the database.
    
    Supports non-transactional mode via -x non_transactional=true flag
    for migrations that cannot run inside a transaction (e.g. HNSW index
    creation with CREATE INDEX CONCURRENTLY).
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Check if this migration requires non-transactional mode
        # Invocation: alembic upgrade <revision> -x non_transactional=true
        non_transactional = context.get_x_argument(
            as_dictionary=True
        ).get("non_transactional", "false").lower() == "true"

        if non_transactional:
            # AUTOCOMMIT mode — required for CREATE INDEX CONCURRENTLY
            connection = connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                transaction_per_migration=False,  # No transaction wrapping
                compare_type=True,
                compare_server_default=True,
            )
            # No explicit transaction — each statement auto-commits
            context.run_migrations()
        else:
            # Standard transactional mode (default)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
