from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase108_readiness_closes_host_decomposition_loop(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase91 = readiness["phase_readiness"]["phase91_host_decomposition_governance"]
    phase108 = readiness["phase_readiness"]["phase108_runtime_host_decomposition_closure"]

    assert phase91["status"] == "ready"
    assert phase108["status"] == "ready"
    assert (
        phase108["details"]["phase108_contract_version"]
        == "phase108.runtime_host_decomposition_closure.v1"
    )
    assert "apps/local-api/app/services/chat_facade_shell.py" in phase108["details"]["shell_modules"]
    assert (
        "apps/local-api/app/services/natural_chat_response_plan.py"
        in phase108["details"]["shell_modules"]
    )


def test_phase108_release_summary_exposes_shell_modules(
    client: TestClient,
) -> None:
    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()

    phase108 = report["summary"]["phase108_runtime_host_decomposition_closure"]
    assert phase108["status"] == "ready"
    assert phase108["contract_version"] == "phase108.runtime_host_decomposition_closure.v1"
    assert "apps/local-api/app/services/chat_facade_shell.py" in phase108["shell_modules"]
