from __future__ import annotations

import hashlib
from pathlib import Path

from app.db.session import Database


class MigrationError(RuntimeError):
    pass


async def run_migrations(db: Database, migrations_dir: Path) -> list[str]:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          checksum TEXT,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          status TEXT NOT NULL DEFAULT 'applied'
        )
        """
    )
    await _ensure_schema_migrations_columns(db)
    applied_rows = await db.fetch_all(
        "SELECT version, checksum, status FROM schema_migrations"
    )
    applied = {row["version"]: row for row in applied_rows}
    executed: list[str] = []

    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.stem.split("_", 1)[0]
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        row = applied.get(version)
        if row is not None:
            if row["status"] != "applied":
                raise MigrationError(f"Migration {version} is not applied cleanly")
            if row["checksum"] is None:
                await db.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                    (checksum, version),
                )
            elif row["checksum"] != checksum:
                raise MigrationError(f"Migration {version} checksum mismatch")
            continue
        async with db.transaction():
            for statement in _split_sql_statements(sql):
                await db.conn.execute(statement)
            await db.conn.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, status)
                VALUES (?, ?, ?, 'applied')
                """,
                (version, path.name, checksum),
            )
        executed.append(path.name)

    return executed


async def _ensure_schema_migrations_columns(db: Database) -> None:
    rows = await db.fetch_all("PRAGMA table_info(schema_migrations)")
    columns = {row["name"] for row in rows}
    if "checksum" not in columns:
        await db.execute("ALTER TABLE schema_migrations ADD COLUMN checksum TEXT")
    if "status" not in columns:
        await db.execute(
            "ALTER TABLE schema_migrations ADD COLUMN status TEXT NOT NULL DEFAULT 'applied'"
        )


def _split_sql_statements(sql: str) -> list[str]:
    return [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip()
    ]
