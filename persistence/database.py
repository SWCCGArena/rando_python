"""
Database Initialization and Session Management

Handles SQLite database setup for the Rando Cal bot.
"""

import os
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from .models import Base

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine = None
_SessionFactory = None


def get_database_path() -> str:
    """Get the path to the SQLite database file"""
    # Import here to avoid circular imports
    from config import config
    return os.path.join(config.DATA_DIR, 'rando.db')


def init_db(database_url: str = None) -> None:
    """
    Initialize the database, creating tables if they don't exist.

    Args:
        database_url: Optional custom database URL. If None, uses default SQLite path.
    """
    global _engine, _SessionFactory

    if database_url is None:
        db_path = get_database_path()
        database_url = f'sqlite:///{db_path}'

    logger.info(f"Initializing database at: {database_url}")

    # Create engine with SQLite optimizations
    _engine = create_engine(
        database_url,
        echo=False,  # Set to True for SQL debugging
        connect_args={'check_same_thread': False},  # Required for SQLite with threads
        poolclass=StaticPool,  # Better for SQLite
    )

    # Enable foreign key support for SQLite
    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables
    Base.metadata.create_all(_engine)

    # Create session factory
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    logger.info("Database initialized successfully")


def get_session() -> Session:
    """
    Get a new database session.

    Returns:
        SQLAlchemy Session object
    """
    global _SessionFactory

    if _SessionFactory is None:
        init_db()

    return _SessionFactory()


@contextmanager
def session_scope():
    """
    Context manager for database sessions.

    Automatically handles commit/rollback and cleanup.

    Usage:
        with session_scope() as session:
            session.add(...)
            session.query(...)
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error, rolling back: {e}")
        raise
    finally:
        session.close()


def get_engine():
    """Get the database engine (for advanced usage)"""
    global _engine
    if _engine is None:
        init_db()
    return _engine


def close_db():
    """Close the database connection (for cleanup)"""
    global _engine, _SessionFactory

    if _engine:
        _engine.dispose()
        _engine = None
        _SessionFactory = None
        logger.info("Database connection closed")
