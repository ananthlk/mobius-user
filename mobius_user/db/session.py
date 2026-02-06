"""
PostgreSQL session factory for mobius-user.

Uses SQLAlchemy with psycopg3. The database URL is set via init_db()
or the USER_DATABASE_URL environment variable.
"""

import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

Base = declarative_base()

_engine = None
_session_factory = None
_database_url = None


def get_database_url() -> str:
    """Get database URL from env or previously set value."""
    global _database_url
    if _database_url:
        return _database_url
    url = os.getenv("USER_DATABASE_URL", "")
    if not url:
        raise ValueError(
            "USER_DATABASE_URL not set. Set it in .env or call init_db(database_url)."
        )
    return url


def init_db(database_url: str | None = None) -> None:
    """
    Initialize the database connection and create tables.
    
    Args:
        database_url: PostgreSQL URL (e.g. postgresql://user:pass@host/mobius_user).
                     If None, uses USER_DATABASE_URL from environment.
    """
    global _engine, _session_factory, _database_url
    
    _database_url = database_url or get_database_url()
    
    # Use psycopg dialect
    db_url = _database_url.replace("postgresql://", "postgresql+psycopg://")
    
    _engine = create_engine(
        db_url,
        echo=os.getenv("SQL_ECHO", "0") == "1",
        pool_pre_ping=True,
        pool_recycle=300,
        pool_reset_on_return="rollback",
        pool_size=2,  # Cap connections per instance when sharing a small DB
        max_overflow=3,
    )
    
    @event.listens_for(_engine, "checkout")
    def checkout_listener(dbapi_conn, connection_record, connection_proxy):
        try:
            cursor = dbapi_conn.cursor()
            cursor.execute("ROLLBACK")
            cursor.close()
        except Exception:
            pass
    
    _session_factory = scoped_session(
        sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    )


def get_engine():
    """Get the SQLAlchemy engine. Call init_db() first."""
    if _engine is None:
        init_db()
    return _engine


def get_db_session():
    """Get a scoped database session."""
    global _session_factory
    if _session_factory is None:
        init_db()
    return _session_factory()


def close_db_session(exception=None):
    """Remove the current session (call at end of request)."""
    if _session_factory is not None:
        try:
            _session_factory.rollback()
        except Exception:
            pass
        finally:
            try:
                _session_factory.remove()
            except Exception:
                pass


def rollback_session():
    """Explicitly rollback the current session."""
    if _session_factory is not None:
        try:
            session = _session_factory()
            if session.is_active:
                session.rollback()
        except Exception:
            try:
                _session_factory.remove()
            except Exception:
                pass
