from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

import aiosqlite

_T = TypeVar("_T")
_SQLITE_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


class Database:
    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._conn: aiosqlite.Connection | None = None
        self._transaction_depth: ContextVar[int] = ContextVar(
            f"database_transaction_depth_{id(self)}",
            default=0,
        )
        self._transaction_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open")
        return self._conn

    async def connect(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.sqlite_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 30000")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async def _run() -> int:
            cursor = await self._with_lock_retry(lambda: self.conn.execute(sql, tuple(params)))
            rowcount = cursor.rowcount
            await cursor.close()
            if self._transaction_depth.get() == 0:
                await self._commit_with_lock_retry()
            return rowcount

        if self._transaction_depth.get() > 0:
            return await _run()
        async with self._transaction_lock:
            return await _run()

    async def executescript(self, sql: str) -> None:
        async def _run() -> None:
            await self._with_lock_retry(lambda: self.conn.executescript(sql))
            if self._transaction_depth.get() == 0:
                await self._commit_with_lock_retry()

        if self._transaction_depth.get() > 0:
            await _run()
            return
        async with self._transaction_lock:
            await _run()

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async def _run() -> aiosqlite.Row | None:
            cursor = await self._with_lock_retry(lambda: self.conn.execute(sql, tuple(params)))
            row = await cursor.fetchone()
            await cursor.close()
            return row

        if self._transaction_depth.get() > 0:
            return await _run()
        async with self._transaction_lock:
            return await _run()

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async def _run() -> list[aiosqlite.Row]:
            cursor = await self._with_lock_retry(lambda: self.conn.execute(sql, tuple(params)))
            rows = await cursor.fetchall()
            await cursor.close()
            return list(rows)

        if self._transaction_depth.get() > 0:
            return await _run()
        async with self._transaction_lock:
            return await _run()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        depth = self._transaction_depth.get()
        is_outer = depth == 0
        if is_outer:
            await self._transaction_lock.acquire()
        token = self._transaction_depth.set(depth + 1)
        try:
            already_in_transaction = bool(getattr(self.conn, "in_transaction", False))
            if is_outer and not already_in_transaction:
                await self._with_lock_retry(lambda: self.conn.execute("BEGIN"))
            yield
        except Exception:
            if is_outer:
                await self.conn.rollback()
            raise
        else:
            if is_outer:
                await self._commit_with_lock_retry()
        finally:
            self._transaction_depth.reset(token)
            if is_outer:
                self._transaction_lock.release()

    async def _commit_with_lock_retry(self) -> None:
        await self._with_lock_retry(self.conn.commit)

    async def _with_lock_retry(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        for attempt, delay in enumerate((*_SQLITE_LOCK_RETRY_DELAYS, 0.0), start=1):
            try:
                return await operation()
            except sqlite3.OperationalError as exc:
                if not _sqlite_lock_error(exc) or attempt > len(_SQLITE_LOCK_RETRY_DELAYS):
                    raise
                await asyncio.sleep(delay)
        raise RuntimeError("sqlite lock retry exhausted")


def _sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message
