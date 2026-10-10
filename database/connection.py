"""
database/connection.py
Async PostgreSQL connection manager using asyncpg.

This module intentionally mimics the small subset of the aiosqlite API the rest
of the codebase relies on, so services did not have to be rewritten:

    db = await get_db()
    await db.execute("INSERT ...", (a, b))
    async with db.execute("SELECT ...", (a,)) as cursor:
        row = await cursor.fetchone()

Placeholders use the SQLite-style "?" which is translated to Postgres "$1, $2"
on the fly. Only "?" characters that are not inside single-quoted string literals
are translated, so SQL containing a literal '?' still works.
"""

import logging
from datetime import date, datetime
from typing import Any, Iterable, Sequence

import asyncpg

from config.settings import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# Minimum / maximum pool size. A Telegram bot is I/O bound with a handful of
# concurrent updates, so a small pool keeps Postgres connection count low.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10


# ── Value coercion ────────────────────────────────────────────────────────────

def as_date(value: Any) -> date | None:
    """Coerce a value into a `date` so asyncpg can bind it to a DATE column.

    asyncpg rejects ISO strings for DATE/TIMESTAMPTZ parameters, so every date
    passed as a query argument must go through here.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return datetime.fromisoformat(text).date()


def as_datetime(value: Any) -> datetime | None:
    """Coerce a value into a `datetime` for a TIMESTAMPTZ column."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


# ── Placeholder translation ──────────────────────────────────────────────────

def translate_placeholders(sql: str) -> str:
    """Rewrite SQLite-style "?" placeholders to Postgres "$n" placeholders.

    Skips "?" occurrences inside single-quoted literals so values such as
    'apakah?' are left untouched.
    """
    out: list[str] = []
    index = 0
    n = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "'":
            # Consume the whole string literal (handles '' escapes).
            out.append(char)
            index += 1
            while index < length:
                out.append(sql[index])
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        out.append(sql[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == "?":
            n += 1
            out.append(f"${n}")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


class QueryResult:
    """Cursor-like result object.

    Usable both as an awaitable (`await db.execute(...)`) and as an async
    context manager (`async with db.execute(...) as cursor`), which is exactly
    how the services were written against aiosqlite.
    """

    def __init__(self, pool: asyncpg.Pool, sql: str, args: Sequence[Any]):
        self._pool = pool
        self._stmt = translate_placeholders(sql)
        self._args = tuple(args)
        self._rows: list[asyncpg.Record] | None = None

    async def _run(self) -> "QueryResult":
        if self._rows is None:
            async with self._pool.acquire() as conn:
                self._rows = await conn.fetch(self._stmt, *self._args)
        return self

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> "QueryResult":
        await self._run()
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    # ── Cursor API ────────────────────────────────────────────────────────────
    async def fetchone(self) -> asyncpg.Record | None:
        await self._run()
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[asyncpg.Record]:
        await self._run()
        return list(self._rows)

    async def fetchval(self) -> Any:
        """Return the first column of the first row, else None."""
        await self._run()
        if not self._rows:
            return None
        return self._rows[0][0]

    @property
    def rowcount(self) -> int:
        """Rows produced by the statement (works with `RETURNING`)."""
        if self._rows is None:
            return 0
        return len(self._rows)

    @property
    def lastrowid(self) -> Any:
        """Compat shim: first column of the first row.

        Callers that need a generated id should use `INSERT ... RETURNING id`
        together with `fetchval()`, which is explicit and race-free.
        """
        if self._rows:
            return self._rows[0][0]
        return None


class Database:
    """Thin async wrapper around an asyncpg connection pool."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def execute(self, sql: str, args: Sequence[Any] | Iterable[Any] = ()) -> QueryResult:
        """Execute a statement. Returns a cursor-like object (await it)."""
        return QueryResult(self._pool, sql, tuple(args))

    async def commit(self) -> None:
        """No-op kept for API compatibility.

        asyncpg runs each statement in autocommit mode, so writes are already
        durable once `execute()` resolves. Call sites keep their `commit()`
        calls so they read naturally (and so adding explicit transactions later
        is a one-line change).
        """

    async def rollback(self) -> None:
        """No-op kept for API compatibility (see `commit`)."""

    # ── Convenience helpers used by the newer agentic code paths ─────────────
    async def fetchone(self, sql: str, args: Sequence[Any] | Iterable[Any] = ()) -> asyncpg.Record | None:
        return await self.execute(sql, args).fetchone()

    async def fetchall(self, sql: str, args: Sequence[Any] | Iterable[Any] = ()) -> list[asyncpg.Record]:
        return await self.execute(sql, args).fetchall()

    async def fetchval(self, sql: str, args: Sequence[Any] | Iterable[Any] = ()) -> Any:
        return await self.execute(sql, args).fetchval()

    def transaction(self) -> "_Transaction":
        """Run a block of statements atomically.

        Usage:
            async with db.transaction() as tx:
                await tx.execute("DELETE ...")
                await tx.execute("INSERT ...")
        """
        return _Transaction(self._pool)


class _Transaction:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx: Any = None

    async def __aenter__(self) -> "_Transaction":
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    def execute(self, sql: str, args: Sequence[Any] | Iterable[Any] = ()) -> QueryResult:
        assert self._conn is not None, "transaction not started"
        return _ConnectionQuery(self._conn, sql, tuple(args))

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        assert self._tx is not None
        if exc_type is None:
            await self._tx.commit()
        else:
            await self._tx.rollback()
        await self._pool.release(self._conn)
        self._conn = None
        return False


class _ConnectionQuery(QueryResult):
    """QueryResult bound to a specific connection (inside a transaction)."""

    def __init__(self, conn: asyncpg.Connection, sql: str, args: Sequence[Any]):
        # Bypass Database.__init__/pool handling by calling the helpers directly.
        self._pool = None  # type: ignore[assignment]
        self._stmt = translate_placeholders(sql)
        self._args = tuple(args)
        self._rows = None
        self._conn = conn

    async def _run(self) -> "QueryResult":
        if self._rows is None:
            self._rows = await self._conn.fetch(self._stmt, *self._args)
        return self


# ── Module-level singleton ───────────────────────────────────────────────────

async def init_db() -> Database:
    """Create the connection pool. Safe to call repeatedly."""
    global _pool
    if _pool is not None:
        return Database(_pool)

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Create a Postgres database (Neon, Supabase, "
            "or local) and export DATABASE_URL before starting the bot."
        )

    logger.info("Connecting to PostgreSQL...")
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=30,
    )
    logger.info("PostgreSQL connection pool ready (min=%d max=%d).", POOL_MIN_SIZE, POOL_MAX_SIZE)
    return Database(_pool)


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed.")


async def get_db() -> Database:
    """Return the shared database handle."""
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return Database(_pool)
