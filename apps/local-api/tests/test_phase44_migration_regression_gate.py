from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.services.release import PHASE_MIGRATION_REQUIREMENTS
from fastapi.testclient import TestClient
from phase_contracts import (
    assert_phase_migration_contract,
    assert_required_migration_at_least,
)


def test_phase44_migration_helper_checks_required_floor_not_latest(
    tmp_path: Path,
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in [
        "023_mcp_runtime_isolation_protocol_hardening.sql",
        "031_media_runtime.sql",
        "099_future_phase.sql",
    ]:
        (migrations / name).write_text("-- phase44 fixture\n", encoding="utf-8")

    phase35 = assert_required_migration_at_least("phase35", migrations_dir=migrations)
    assert phase35["required_migration"] == "023_mcp_runtime_isolation_protocol_hardening.sql"
    assert phase35["current_latest_migration"] == "099_future_phase.sql"

    phase43 = assert_required_migration_at_least("phase43", migrations_dir=migrations)
    assert phase43["required_migration"] == "031_media_runtime.sql"
    assert phase43["current_latest_migration"] == "099_future_phase.sql"

    try:
        assert_required_migration_at_least("phase36", migrations_dir=migrations)
    except AssertionError as exc:
        assert "024_scheduled_tasks.sql" in str(exc)
    else:
        raise AssertionError("phase36 should fail when its required migration is absent")


def test_phase44_phase_contracts_tables_suites_and_case_prefixes(
    client: TestClient,
) -> None:
    assert client.get("/api/evals/suites").status_code == 200

    for phase in [f"phase{number}" for number in range(35, 44)]:
        contract = assert_phase_migration_contract(client, phase)
        assert contract["current_at_least_required"] is True

    registry = cast(Any, client.app).state.registry

    async def load_cases() -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in await registry.db.fetch_all(
                """
                SELECT suite_id, case_key
                FROM eval_cases
                WHERE suite_id IN (
                  'suite_phase35_chat_safety_state_semantics',
                  'suite_phase36_scheduled_background_tasks',
                  'suite_phase37_browser_sessions',
                  'suite_phase38_skill_governance',
                  'suite_phase39_task_checkpoints',
                  'suite_phase40_notification_gateway',
                  'suite_phase41_chat_quality_experience',
                  'suite_phase42_external_platform_actions',
                  'suite_phase43_media_runtime'
                )
                """
            )
        ]

    rows = cast(Any, client).portal.call(load_cases)
    by_suite: dict[str, list[str]] = {}
    for row in rows:
        by_suite.setdefault(str(row["suite_id"]), []).append(str(row["case_key"]))

    expected_prefixes = {
        "suite_phase35_chat_safety_state_semantics": "phase35.chat_safety_state_semantics.",
        "suite_phase36_scheduled_background_tasks": "phase36.scheduled_background_tasks.",
        "suite_phase37_browser_sessions": "phase37.browser_sessions.",
        "suite_phase38_skill_governance": "phase38.skill_governance.",
        "suite_phase39_task_checkpoints": "phase39.task_checkpoints.",
        "suite_phase40_notification_gateway": "phase40.notification_gateway.",
        "suite_phase41_chat_quality_experience": "phase41.chat_quality_experience.",
        "suite_phase42_external_platform_actions": "phase42.external_platform_actions.",
        "suite_phase43_media_runtime": "phase43.media_runtime.",
    }
    for suite_id, prefix in expected_prefixes.items():
        assert by_suite[suite_id], suite_id
        assert all(case_key.startswith(prefix) for case_key in by_suite[suite_id])


def test_phase44_release_report_and_diagnostic_include_migration_contracts(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    contracts = report["summary"]["migration_contracts"]
    assert set(PHASE_MIGRATION_REQUIREMENTS).issubset(contracts)
    assert contracts["phase35"]["required_migration"].startswith("023_")
    assert contracts["phase35"]["current_latest_migration"] == "031_media_runtime.sql"
    assert contracts["phase35"]["future_migrations_present"] is True
    assert contracts["phase36"]["required_tables"]["scheduled_tasks"] is True
    assert contracts["phase43"]["required_tables"]["media_assets"] is True
    assert report["summary"]["phase36"]["migration_contract"]["status"] == "implemented"
    assert report["summary"]["phase43"]["migration_contract"]["required_migration"] == (
        "031_media_runtime.sql"
    )

    assert diagnostic["current_latest_migration"] == "031_media_runtime.sql"
    assert diagnostic["phase_required_migrations"]["phase42"] == (
        "030_external_platform_actions.sql"
    )
    assert diagnostic["phase_migration_contracts"]["phase37"]["status"] == "implemented"
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "secret=",
        "token=phase44",
        "cookie=phase44",
        "private_key=phase44",
        "mnemonic=phase44",
        "c:\\users\\administrator\\phase44",
    ]
    return sum(1 for marker in forbidden if marker in serialized)
