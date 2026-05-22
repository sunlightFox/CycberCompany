from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_phase76_chat_mainline_readiness_endpoint_exposes_control_plane_truth(
    client: TestClient,
) -> None:
    response = client.get("/api/system/chat-mainline-readiness")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["phase76_control_plane_version"] == "phase76.chat_mainline_control_plane.v1"
    assert payload["mainline_path_declared"] == [
        "SessionRuntime",
        "ChatRuntime",
        "ContextGateway",
        "Brain",
        "Safety",
        "Tool/Task",
        "ResponseComposer",
        "Memory/Trace",
    ]
    assert "phase77_runtime_closure" in payload["phase_readiness"]
    assert "phase86_runtime_host_uniqueness" in payload["phase_readiness"]
    assert "phase88_channel_reliability" in payload["phase_readiness"]
    assert "phase89_false_interception_governance" in payload["phase_readiness"]
    assert "phase90_compat_cleanup_release_gate" in payload["phase_readiness"]
    assert "phase91_host_decomposition_governance" in payload["phase_readiness"]
    assert "phase108_runtime_host_decomposition_closure" in payload["phase_readiness"]
    assert "phase109_real_world_maturity_recheck" in payload["phase_readiness"]
    assert "phase114_mainline_observability_closure" in payload["phase_readiness"]
    assert "phase115_golden_extension_packages" in payload["phase_readiness"]
    assert "phase116_maturity_dashboard_unification" in payload["phase_readiness"]
    assert "phase85_execution_batches" in payload["phase_readiness"]
    assert payload["runtime_facts"]["session_runtime_role"] == "entry_runtime"
    assert payload["runtime_facts"]["chat_service_host"].endswith("/chat.py")
    assert payload["runtime_facts"]["phase_docs_present"]["phase77_runtime_closure"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase86_runtime_host_uniqueness"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase88_channel_reliability"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase89_false_interception_governance"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase90_compat_cleanup_release_gate"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase91_host_decomposition_governance"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase108_runtime_host_decomposition_closure"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase109_real_world_maturity_recheck"] is True
    assert payload["runtime_facts"]["phase_docs_present"]["phase85_execution_batches"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase75_quality_takeover_rollout"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase86_runtime_host_uniqueness"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase89_false_interception_governance"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase90_compat_cleanup_release_gate"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase91_host_decomposition_governance"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase108_runtime_host_decomposition_closure"] is True
    assert payload["runtime_facts"]["phase_tests_present"]["phase109_real_world_maturity_recheck"] is True
    assert payload["runtime_facts"]["presence_runtime_rollout_visible"] is True
    assert payload["phase_readiness"]["phase82_ledger_memory"]["status"] in {"ready", "partial"}
    assert payload["phase_readiness"]["phase83_hooks"]["status"] in {"ready", "partial"}
    assert payload["phase_readiness"]["phase84_acceptance_matrix"]["status"] in {"ready", "partial"}
    phase85 = payload["phase_readiness"]["phase85_execution_batches"]
    assert phase85["details"]["execution_batches_version"] == "phase85.execution_batches_control_plane.v1"
    assert phase85["details"]["next_batch"]
    assert len(phase85["details"]["batches"]) == 7
    assert len(phase85["details"]["recommended_pr_order"]) == 14


def test_phase76_readiness_and_runtime_topology_are_consistent(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    topology = client.get("/api/system/runtime-topology").json()["items"]
    by_name = {item["name"]: item for item in topology}

    assert by_name["session"]["runtime"] == "session_runtime"
    assert by_name["session"]["details"]["delegates_to"] == "agent_runtime"
    assert by_name["chat_execution_batches"]["runtime"] == "chat_execution_batches_control_plane"
    assert by_name["channel_ingress"]["status"] == "runtime_native"
    assert by_name["wechat_gateway"]["status"] == "compat_shell"
    assert by_name["feishu_gateway"]["status"] == "compat_shell"
    assert readiness["runtime_facts"]["runtime_topology_consistent"] is True
    phase77 = readiness["phase_readiness"]["phase77_runtime_closure"]
    assert phase77["status"] in {"ready", "partial"}
    phase86 = readiness["phase_readiness"]["phase86_runtime_host_uniqueness"]
    assert phase86["status"] in {"ready", "partial"}
    assert "apps/local-api/app/services/session_runtime.py" in phase77["source_of_truth"]


def test_phase76_release_summary_includes_chat_mainline_readiness(
    client: TestClient,
) -> None:
    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    release_gate_id = created.json()["release_gate_id"]

    report = client.get(f"/api/release-gates/{release_gate_id}/report")
    assert report.status_code == 200, report.text
    summary = report.json()["summary"]

    assert "chat_mainline_readiness" in summary
    readiness = summary["chat_mainline_readiness"]
    assert "runtime_topology_consistent" in readiness
    assert "prompt_contract_coverage" in readiness
    assert "acceptance_matrix_version" in readiness
    assert "acceptance_groups" in readiness
    assert "execution_batches_version" in readiness
    assert "next_batch" in readiness
    assert "phase88_channel_reliability_status" in readiness
    assert "phase88_failure_reason_counts" in readiness
    assert "phase89_false_interception_governance_status" in readiness
    assert "phase90_compat_cleanup_release_gate_status" in readiness
    assert "phase91_host_decomposition_governance_status" in readiness
    assert "phase108_runtime_host_decomposition_closure_status" in readiness
    assert "phase109_real_world_maturity_recheck_status" in readiness
    assert "phase109_no_turn_diagnostics" in readiness
    assert "phase114_mainline_observability_closure_status" in readiness
    assert "phase114_mainline_rates" in readiness
    assert "phase114_top_blockers" in readiness
    assert "phase115_golden_extension_packages_status" in readiness
    assert "phase115_golden_package_inventory" in readiness
    assert "strict_format_continuity_gate" in readiness
    assert "persona_20_quality_gate" in readiness
    assert "persona_20_case_count" in readiness
    assert "persona_20_pass_count" in readiness
    assert "persona_20_fail_count" in readiness
    assert "false_boundary_rate" in readiness
    assert "natural_continuation_pass_rate" in readiness
    assert "no_turn_count" in readiness
    assert "evidence_no_turn_group_counts" in readiness["phase109_no_turn_diagnostics"]
    assert "remediation_queue" in readiness["phase109_no_turn_diagnostics"]
    assert "phase_docs_present" in readiness
    assert "phase_tests_present" in readiness
    assert "phase86_runtime_host_uniqueness" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase88_channel_reliability" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase89_false_interception_governance" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase90_compat_cleanup_release_gate" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase91_host_decomposition_governance" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase108_runtime_host_decomposition_closure" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase109_real_world_maturity_recheck" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase114_mainline_observability_closure" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase115_golden_extension_packages" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]
    assert "phase116_maturity_dashboard_unification" in client.get(
        "/api/system/chat-mainline-readiness"
    ).json()["phase_readiness"]


def test_phase76_registry_service_matches_api(client: TestClient) -> None:
    registry = client.app.state.registry
    service_payload = _run_async(registry.chat_mainline_readiness_service.diagnostic)
    api_payload = client.get("/api/system/chat-mainline-readiness").json()

    assert service_payload["phase76_control_plane_version"] == api_payload["phase76_control_plane_version"]
    assert service_payload["runtime_facts"]["phase_docs_present"] == api_payload["runtime_facts"]["phase_docs_present"]


def _run_async(fn: Any) -> Any:
    import asyncio

    return asyncio.run(fn())
