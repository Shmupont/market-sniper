import contextlib
from typing import Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool
import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=settings.database_url,
        )
        log.info("db.pool.created", minconn=2, maxconn=10)
    return _pool


@contextlib.contextmanager
def get_db() -> Generator[psycopg2.extensions.connection, None, None]:
    """Acquire a connection from the pool, yield it, then release it back."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        log.info("db.pool.closed")
