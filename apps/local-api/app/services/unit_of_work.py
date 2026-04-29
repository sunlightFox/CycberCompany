from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.db.session import Database


class UnitOfWork:
    def __init__(self, db: Database) -> None:
        self.db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self.db.transaction():
            yield
