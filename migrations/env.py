"""
Alembic migration environment for mobius-user.

Uses USER_DATABASE_URL from environment.
Run: USER_DATABASE_URL=postgresql://... alembic upgrade head
"""

import os
from logging.config import fileConfig

from sqlalchemy import pool, create_engine
from alembic import context

# Add parent to path so we can import mobius_user
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mobius_user.db.session import Base
from mobius_user import models  # noqa: F401 - registers models with Base

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    url = os.getenv("USER_DATABASE_URL", "")
    if not url:
        raise ValueError(
            "USER_DATABASE_URL must be set. "
            "e.g. USER_DATABASE_URL=postgresql://user:pass@localhost/mobius_user"
        )
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://")
    return url


def run_migrations_offline():
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
