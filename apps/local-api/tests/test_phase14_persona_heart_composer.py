from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_phase14_contracts_eval_and_preview_api(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    task_count_before = len(client.get("/api/tasks").json()["items"])

    preview_response = client.post(
        "/api/response-composer/preview",
        json={
            "scenario": "approval_required",
            "risk_level": "R5",
            "user_text": "删除这个文件",
            "result_summary": "需要确认后才能继续。token=phase14-secret-value",
            "notices": {
                "approval_prompt": {
                    "summary": "确认后才会进入受控任务。",
                    "private_note": "api_key=phase14-approval-secret",
                },
                "safety_notice": "不要外发 token=phase14-notice-secret",
                "follow_up_options": ["只生成计划", "补充范围"],
            },
        },
    )
    preview = preview_response.json()
    trace = client.get(f"/api/traces/{preview_response.headers['X-Trace-Id']}").json()
    task_count_after = len(client.get("/api/tasks").json()["items"])

    assert by_name["PersonaEngine"]["status"] == "implemented"
    assert by_name["HeartService"]["status"] == "implemented"
    assert "suite_phase14_persona_heart_composer" in {
        item["suite_id"] for item in suites
    }
    assert preview["response_plan"]["approval_prompt"]["summary"] == "确认后才会进入受控任务。"
    assert preview["response_plan"]["safety_notice"]
    assert preview["response_plan"]["action_buttons"]
    assert preview["response_plan"]["tone_metadata"]["deescalation_required"] is True
    assert preview["response_plan"]["redaction_summary"]["applied"] is True
    assert "phase14-secret-value" not in json.dumps(preview, ensure_ascii=False)
    assert "phase14-approval-secret" not in json.dumps(preview, ensure_ascii=False)
    assert "phase14-notice-secret" not in json.dumps(preview, ensure_ascii=False)
    assert task_count_after == task_count_before
    assert any(span["span_type"] == "response.compose" for span in trace["spans"])


def test_phase14_persona_profile_policy_update_and_validation(
    client: TestClient,
) -> None:
    profile_id = client.get("/api/persona/profiles").json()["items"][0][
        "persona_profile_id"
    ]
    updated = client.patch(
        f"/api/persona/profiles/{profile_id}",
        json={
            "summary": "Calm, exact, warm, and careful with boundaries.",
            "tone_policy": {
                "conciseness": 0.8,
                "warmth": 0.7,
                "humor": 0.1,
                "directness": 0.85,
                "technical_depth": 0.75,
            },
            "disclosure_policy": {
                "capability_boundary_disclosure": True,
                "uncertainty_disclosure": True,
                "tool_usage_notice": "when_tool_or_task_is_required",
            },
            "risk_tone_policy": {
                "approval_scene_tone": "clear_and_calm",
                "security_block_scene_tone": "firm_and_explanatory",
            },
            "allowed_modes": ["default", "concise", "safety_boundary"],
            "default_mode": "concise",
        },
    ).json()
    rejected = client.patch(
        f"/api/persona/profiles/{profile_id}",
        json={"tone_policy": {"bypass_safety": True}},
    )
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert updated["default_mode"] == "concise"
    assert updated["allowed_modes"] == ["default", "concise", "safety_boundary"]
    assert updated["risk_tone_policy"]["approval_scene_tone"] == "clear_and_calm"
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "persona.profile.updated" in audit_text
    assert "phase14-secret" not in audit_text


def test_phase14_heart_signal_varies_and_does_not_change_safety(
    client: TestClient,
) -> None:
    anxious = client.get(
        "/api/heart/state/mem_xiaoyao",
        params={"text": "我很焦虑，担心这个事情会失败"},
    ).json()
    angry = client.get(
        "/api/heart/state/mem_xiaoyao",
        params={"text": "我很生气，马上帮我删除这些文件"},
    ).json()
    happy = client.get(
        "/api/heart/state/mem_xiaoyao",
        params={"text": "太好了，这个方案很棒"},
    ).json()
    safety_payload = {
        "actor_id": "mem_xiaoyao",
        "action_type": "tool",
        "action": "terminal.run",
        "object_type": "tool",
        "tool_name": "terminal.run",
        "payload": {"command": "echo safe"},
        "risk_hints": ["R5"],
    }
    before = client.post("/api/safety/evaluate", json=safety_payload).json()
    client.get(
        "/api/heart/state/mem_xiaoyao",
        params={"text": "紧急转账支付一下"},
    )
    after = client.post("/api/safety/evaluate", json=safety_payload).json()

    assert anxious["mood"] == "anxious"
    assert anxious["user_state"] == "needs_reassurance"
    assert angry["deescalation_required"] is True
    assert angry["risk_tone_override"] == "clear_and_calm"
    assert happy["mood"] == "positive"
    assert anxious["confidence"] > 0.6
    assert before["decision"] == after["decision"] == "approval_required"


def test_phase14_release_report_contains_persona_heart_composer_summary(
    client: TestClient,
) -> None:
    client.get("/api/system/runtime-contracts")
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert completed["blocker_count"] == 0
    assert report["decision"] == "go"
    assert report["summary"]["phase14"]["persona_contract"] == 1
    assert report["summary"]["phase14"]["heart_contract"] == 1
    assert report["summary"]["phase14"]["response_plan_extended_fields"] is True
