"""Shared psycopg connection pool (raw SQL paths: event log, LISTEN/NOTIFY, LangGraph saver).

SQLAlchemy (db.py) is used for ordinary CRUD; this pool is for the hot paths where we want
plain SQL and the settings LangGraph's checkpointer requires.
"""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from company_check.config import get_settings

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            get_settings().psycopg_dsn,
            min_size=1,
            max_size=10,
            open=False,
            # Required by langgraph-checkpoint-postgres.
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
