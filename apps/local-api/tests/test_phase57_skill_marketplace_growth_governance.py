from __future__ import annotations

import json
from typing import Any

import anyio
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase57_catalog_detail_health_refresh_and_redaction(
    client: TestClient,
) -> None:
    search = client.get(
        "/api/skills/catalog/search",
        params={"q": "office", "repository_id": "clawhub", "limit": 10},
    )
    assert search.status_code == 200, search.text
    items = search.json()["items"]
    assert items
    package_ref = items[0]["package_ref"]

    detail = client.get(
        "/api/skills/catalog/package",
        params={"repository_id": "clawhub", "package_ref": package_ref},
    )
    assert detail.status_code == 200, detail.text
    package = detail.json()["package"]
    assert package["entry"]["repository_id"] == "clawhub"
    assert package["entry"]["source"]["uri_hash"].startswith("sha256:")
    assert "uri" not in package["entry"]["source"]
    assert "token" not in json.dumps(detail.json(), ensure_ascii=False)
    assert "cookie" not in json.dumps(detail.json(), ensure_ascii=False)

    health = client.post("/api/skills/catalog/clawhub/refresh-health")
    assert health.status_code == 200, health.text
    health_items = health.json()["items"]
    assert any(item["package_ref"] == package_ref for item in health_items)
    assert all(
        item["health_status"] in {"healthy", "degraded", "unavailable"}
        for item in health_items
    )
    assert "token" not in json.dumps(health.json(), ensure_ascii=False)
    assert "cookie" not in json.dumps(health.json(), ensure_ascii=False)


def test_phase57_install_records_dependency_graph_and_rollback(
    client: TestClient,
) -> None:
    install = client.post(
        "/api/skills/install",
        json={
            "source_type": "repository_ref",
            "source_uri": "clawhub:official/office/daily-brief",
            "requested_by_member_id": "mem_xiaoyao",
        },
    )
    assert install.status_code == 200, install.text
    payload = install.json()
    bundle = payload["bundle"]
    skill = payload["skills"][0]

    assert bundle["status"] == "installed_disabled"
    assert skill["status"] == "installed_disabled"

    install_records = client.get(
        "/api/skills/catalog/install-records",
        params={"repository_id": "clawhub", "package_ref": "official/office/daily-brief"},
    )
    assert install_records.status_code == 200, install_records.text
    record = install_records.json()["items"][0]
    assert record["status"] == "installed_disabled"
    assert record["gate_status"] == "preview_passed"
    assert record["source_uri_hash"].startswith("sha256:")
    assert record["requested_by_member_id"] == "mem_xiaoyao"
    assert "clawhub:official/office/daily-brief" not in json.dumps(
        install_records.json(),
        ensure_ascii=False,
    )

    deps = client.get(
        "/api/skills/dependencies",
        params={"source_type": "skill", "source_id": skill["skill_id"]},
    )
    assert deps.status_code == 200, deps.text
    dep_items = deps.json()["items"]
    assert dep_items
    assert all(item["source_id"] == skill["skill_id"] for item in dep_items)
    assert all("token" not in json.dumps(item, ensure_ascii=False) for item in dep_items)

    upgrade = client.post(
        f"/api/skills/{skill['skill_id']}/upgrade",
        json={
            "actor_member_id": "mem_xiaoyao",
            "bundle_revision": "57.1.0",
            "display_name": "Phase57 Upgraded",
            "reason": "phase57 upgrade",
        },
    )
    assert upgrade.status_code == 200, upgrade.text
    assert upgrade.json()["skill"]["display_name"] == "Phase57 Upgraded"

    rollback = client.post(
        f"/api/skills/{skill['skill_id']}/rollback",
        json={"actor_member_id": "mem_xiaoyao", "reason": "phase57 rollback"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["skill"]["display_name"] == skill["display_name"]

    registry = client.app.state.registry
    _run_async(
        client,
        registry.skill_repository_service.refresh_dependency_edges_for_skill,
        {
            "skill_id": "skill.phase57.wildcard",
            "bundle_id": "phase57-wildcard",
            "required_tools": ["file.write:*"],
            "required_assets": [],
        },
    )
    wildcard_edges = client.get(
        "/api/skills/dependencies",
        params={"source_type": "skill", "source_id": "skill.phase57.wildcard"},
    ).json()["items"]
    assert any(
        item["status"] == "blocked"
        and item["fail_closed_reason"] == "wildcard_tool_requires_review"
        for item in wildcard_edges
    )


def test_phase57_growth_candidates_from_phase56_experience(
    client: TestClient,
) -> None:
    consolidated = client.post(
        "/api/memory/experience/consolidate",
        json={
            "member_id": "mem_xiaoyao",
            "task_id": "task_phase57_growth",
            "conversation_id": "conv_phase57_growth",
            "outcome": "completed",
            "summary_text": (
                "Phase57 reusable lesson: build the migration, repository, service, API, "
                "release contract, and focused tests in order."
            ),
            "source": {
                "type": "task_experience",
                "trace_id": "trace_phase57_growth",
                "turn_id": "turn_phase57_growth",
                "message_id": "msg_phase57_growth",
            },
            "steps": [
                {"step_type": "migration", "status": "completed"},
                {"step_type": "service", "status": "completed"},
                {"step_type": "test", "status": "completed"},
            ],
            "evidence": {
                "result": "phase57 growth evidence",
                "token": "phase57-sensitive-token",
                "local_path": "C:/phase57/private-cookie.txt",
            },
        },
    )
    assert consolidated.status_code == 200, consolidated.text
    experience = consolidated.json()["experience"]

    growth = client.post(
        "/api/skills/growth-candidates/consolidate",
        json={
            "task_id": "task_phase57_growth",
            "experience_id": experience["experience_id"],
            "limit": 10,
        },
    )
    assert growth.status_code == 200, growth.text
    items = growth.json()["items"]
    assert items
    assert items[0]["decision"] in {"candidate_created", "governance_hint"}
    assert items[0]["candidate_id"] is not None
    assert "phase57-sensitive-token" not in json.dumps(growth.json(), ensure_ascii=False)
    assert "private-cookie.txt" not in json.dumps(growth.json(), ensure_ascii=False)

    listed = client.get(
        "/api/skills/growth-candidates",
        params={"candidate_id": items[0]["candidate_id"], "limit": 10},
    )
    assert listed.status_code == 200, listed.text
    assert any(
        item["evidence_id"] == items[0]["evidence_id"] for item in listed.json()["items"]
    )


def test_phase57_release_contracts_and_eval_suite(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase57")
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]

    assert migration_contract["required_migration"] == "042_skill_marketplace_growth_governance.sql"
    assert "suite_phase57_skill_marketplace_growth_governance" in {
        item["suite_id"] for item in client.get("/api/evals/suites").json()["items"]
    }
    for module in [
        "SkillMarketplaceCatalog",
        "SkillMarketplaceGovernance",
        "SkillDependencyGraph",
        "SkillGrowthCandidatePipeline",
    ]:
        assert by_name[module]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    phase57 = report["summary"]["phase57_skill_marketplace_growth_governance"]
    assert phase57["suite_id"] == "suite_phase57_skill_marketplace_growth_governance"
    assert phase57["registered_cases"] >= 8
    assert phase57["marketplace_matrix"]["catalog_api"] is True
    assert phase57["marketplace_matrix"]["governance_gate"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase57"]["registered"] is True
    assert any(
        item["source_type"] == "phase57_skill_marketplace_growth_governance"
        for item in evidence
    )
    serialized = json.dumps(
        {
            "report": report,
            "evidence": evidence,
            "phase57": phase57,
        },
        ensure_ascii=False,
    )
    assert "phase57-sensitive-token" not in serialized
    assert "private-cookie.txt" not in serialized
    assert "C:/phase57" not in serialized


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:
        return portal.call(func, *args, **kwargs)

    async def runner() -> Any:
        return await func(*args, **kwargs)

    return anyio.run(runner)
