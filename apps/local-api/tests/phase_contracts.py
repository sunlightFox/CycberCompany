from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from app.services.release import PHASE_MIGRATION_REQUIREMENTS

ROOT_DIR = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = ROOT_DIR / "apps" / "local-api" / "app" / "db" / "migrations"


def assert_required_migration_at_least(
    phase: str,
    *,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> dict[str, Any]:
    contract = PHASE_MIGRATION_REQUIREMENTS[phase]
    required_migration = str(contract["required_migration"])
    migration_names = sorted(path.name for path in migrations_dir.glob("*.sql"))
    assert migration_names, "no migrations found"
    assert required_migration in migration_names, (
        f"{phase} requires {required_migration}, but it is missing from migrations"
    )
    current_latest = migration_names[-1]
    assert _migration_version(current_latest) >= _migration_version(required_migration), (
        f"{phase} requires at least {required_migration}, "
        f"but latest migration is {current_latest}"
    )
    return {
        "phase": phase,
        "required_migration": required_migration,
        "current_latest_migration": current_latest,
        "current_at_least_required": True,
    }


def assert_tables_exist(client: Any, tables: list[str] | tuple[str, ...]) -> set[str]:
    registry = client.app.state.registry

    async def load_names() -> set[str]:
        rows = await registry.db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
        )
        return {str(row["name"]) for row in rows}

    portal = getattr(client, "portal", None)
    table_names = portal.call(load_names) if portal is not None else anyio.run(load_names)
    missing = sorted(set(tables) - table_names)
    assert not missing, f"missing required tables: {', '.join(missing)}"
    return table_names


def assert_phase_migration_contract(client: Any, phase: str) -> dict[str, Any]:
    result = assert_required_migration_at_least(phase)
    required_tables = tuple(PHASE_MIGRATION_REQUIREMENTS[phase].get("tables") or ())
    if required_tables:
        assert_tables_exist(client, required_tables)
    return {
        **result,
        "required_tables": list(required_tables),
    }


def _migration_version(name: str) -> int:
    prefix = name.split("_", 1)[0]
    return int(prefix)
