from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._conn: aiosqlite.Connection | None = None
        self._transaction_depth = 0

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open")
        return self._conn

    async def connect(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.sqlite_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cursor = await self.conn.execute(sql, tuple(params))
        rowcount = cursor.rowcount
        await cursor.close()
        if self._transaction_depth == 0:
            await self.conn.commit()
        return rowcount

    async def executescript(self, sql: str) -> None:
        await self.conn.executescript(sql)
        if self._transaction_depth == 0:
            await self.conn.commit()

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        is_outer = self._transaction_depth == 0
        if is_outer:
            await self.conn.execute("BEGIN")
        self._transaction_depth += 1
        try:
            yield
        except Exception:
            self._transaction_depth -= 1
            if is_outer:
                await self.conn.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if is_outer:
                await self.conn.commit()
