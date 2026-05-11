from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase85_readiness_exposes_structured_execution_batches(client: TestClient) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase85 = readiness.json()["phase_readiness"]["phase85_execution_batches"]
    details = phase85["details"]
    batches = details["batches"]

    assert phase85["next_owner_module"] == "apps/local-api/app/services/chat_mainline_readiness.py"
    assert details["execution_batches_version"] == "phase85.execution_batches_control_plane.v1"
    assert details["next_batch"]
    assert len(batches) == 7
    assert details["recommended_pr_order"][0].startswith("PR1 ")
    assert details["recommended_pr_order"][-1].startswith("PR14 ")

    by_id = {item["batch_id"]: item for item in batches}
    assert by_id["batch1_runtime_entry_closure"]["depends_on"] == []
    assert by_id["batch2_channel_session_semantics"]["depends_on"] == [
        "batch1_runtime_entry_closure"
    ]
    assert by_id["batch7_hook_contract_integration"]["depends_on"]
    assert all(item["minimum_test_files"] for item in batches)


def test_phase85_batch_statuses_follow_phase77_to_phase83_readiness(client: TestClient) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()["phase_readiness"]
    phase85 = readiness["phase85_execution_batches"]["details"]["batches"]
    by_id = {item["batch_id"]: item for item in phase85}

    mapping = {
        "batch1_runtime_entry_closure": readiness["phase77_runtime_closure"]["status"],
        "batch2_channel_session_semantics": readiness["phase78_session_channel_semantics"]["status"],
        "batch3_context_gateway_layering": readiness["phase79_context_gateway_enhancement"]["status"],
        "batch4_single_turn_tool_loop": readiness["phase80_tool_loop"]["status"],
        "batch5_response_visibility_governance": readiness["phase81_response_visibility"]["status"],
        "batch6_ledger_memory_unification": readiness["phase82_ledger_memory"]["status"],
        "batch7_hook_contract_integration": readiness["phase83_hooks"]["status"],
    }
    expected = {"ready": "covered", "partial": "in_progress", "blocked": "blocked"}

    for batch_id, phase_status in mapping.items():
        assert by_id[batch_id]["status"] == expected[phase_status]


def test_phase85_topology_and_release_summary_share_same_truth(client: TestClient) -> None:
    topology = client.get("/api/system/runtime-topology").json()["items"]
    batches = next(item for item in topology if item["name"] == "chat_execution_batches")

    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()
    summary = report["summary"]["chat_mainline_readiness"]

    assert batches["details"]["execution_batches_version"] == summary["execution_batches_version"]
    assert batches["details"]["next_batch"] == summary["next_batch"]
    assert batches["details"]["recommended_pr_order"] == summary["recommended_pr_order"]
    assert isinstance(summary["compat_cleanup_window"], dict)


def test_phase85_compat_cleanup_window_requires_phase84_and_covered_batch(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()["phase_readiness"]
    phase84 = readiness["phase84_acceptance_matrix"]
    phase85 = readiness["phase85_execution_batches"]["details"]
    windows = phase85["compat_cleanup_window"]
    covered = {item["batch_id"] for item in phase85["batches"] if item["status"] == "covered"}

    assert windows["phase84_acceptance_ready"] == (phase84["status"] == "ready")
    assert windows["phase77_batch1_removal_open"] == (
        phase84["status"] == "ready" and "batch1_runtime_entry_closure" in covered
    )
    assert windows["phase81_batch5_removal_open"] == (
        phase84["status"] == "ready" and "batch5_response_visibility_governance" in covered
    )
    assert windows["phase83_batch7_removal_open"] == (
        phase84["status"] == "ready" and "batch7_hook_contract_integration" in covered
    )
