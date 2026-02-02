"""
Database session and configuration for mobius-user.

The user module owns its own database (mobius_user). Consumers (mobius-os, mobius-chat)
pass the database URL via config; this module provides the session factory.
"""

from mobius_user.db.session import (
    Base,
    get_db_session,
    init_db,
    get_engine,
    close_db_session,
    rollback_session,
)

__all__ = [
    "Base",
    "get_db_session",
    "init_db",
    "get_engine",
    "close_db_session",
    "rollback_session",
]
