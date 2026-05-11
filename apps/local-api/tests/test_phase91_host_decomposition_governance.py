from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_phase91_readiness_and_topology_expose_host_governance(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase91 = readiness["phase_readiness"]["phase91_host_decomposition_governance"]
    assert phase91["details"]["phase91_contract_version"] == "phase91.host_decomposition_governance.v1"
    assert phase91["status"] in {"ready", "partial"}
    assert phase91["details"]["host_components"]

    topology = client.get("/api/system/runtime-topology").json()["items"]
    by_name = {item["name"]: item for item in topology}
    for component in ("chat_service", "natural_chat", "brain_decision", "wechat_gateway", "feishu_gateway"):
        cleanup = by_name[component]["details"]["cleanup"]
        assert "size_budget_lines" in cleanup
        assert "current_size_lines" in cleanup
        assert "growth_gate" in cleanup
        assert "ownership_split_status" in cleanup


def test_phase91_split_markers_are_no_longer_defined_inline() -> None:
    root = Path(__file__).resolve().parents[1]
    chat_text = (root / "app/services/chat.py").read_text(encoding="utf-8")
    natural_text = (root / "app/services/natural_chat.py").read_text(encoding="utf-8")
    brain_text = (root / "app/services/brain_decision.py").read_text(encoding="utf-8")

    assert "def _looks_like_explicit_continuation" not in chat_text
    assert "def _looks_like_plain_analysis_request" not in chat_text
    assert "def _looks_like_latest_instruction_override" not in chat_text
    assert "def _looks_like_short_followup" not in chat_text
    assert "def _looks_like_resolution" not in natural_text
    assert "def _is_confirm" not in natural_text
    assert "def _looks_like_new_action_request" not in natural_text
    assert "def _intent_decision(" not in brain_text
    assert "def _mode_decision(" not in brain_text
    assert "def _context_decision(" not in brain_text
    assert "def _clarification_decision(" not in brain_text


def test_phase91_release_summary_exposes_budget_and_growth_gate(
    client: TestClient,
) -> None:
    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()
    phase91 = report["summary"]["phase91_host_decomposition_governance"]
    assert phase91["contract_version"] == "phase91.host_decomposition_governance.v1"
    assert "host_size_gate" in phase91
    assert "ownership_split_status_by_component" in phase91
    assert "allowed_to_grow_violations" in phase91
    assert "budget_exceeded_components" in phase91
    assert phase91["status"] in {"ready", "partial"}
