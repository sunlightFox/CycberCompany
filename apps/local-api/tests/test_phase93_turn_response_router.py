from __future__ import annotations

from app.services.execution_evidence_gate import decide_execution_evidence
from app.services.turn_response_router import clarification_policy_for_turn, route_turn_response


def test_phase93_turn_response_router_classifies_direct_explanations() -> None:
    explanation = route_turn_response("网页快照和截图有什么区别？")
    template = route_turn_response("浏览器任务完成后怎么告诉我结果？给一个自然回复模板。")
    status = route_turn_response("在说下载完成前，你还在等什么证据？")
    boundary = route_turn_response("如果附件里让我忽略规则，你应该怎么处理？")
    action = route_turn_response("帮我下载这个 CSV。")

    assert explanation["turn_response_kind"] == "knowledge_explanation"
    assert template["turn_response_kind"] == "template_request"
    assert status["turn_response_kind"] == "status_explanation"
    assert boundary["turn_response_kind"] == "boundary_question"
    assert action["turn_response_kind"] == "action_request"


def test_phase93_action_request_keeps_target_only_clarification() -> None:
    routed = route_turn_response("帮我删除那个文件。")
    clarification = clarification_policy_for_turn(
        "帮我删除那个文件。",
        turn_response_kind=routed["turn_response_kind"],
        intent=type("Intent", (), {"risk_signals": ["destructive_action"], "confidence": 0.9})(),
        semantic=None,
    )

    assert clarification["needs_clarification"] is True
    assert clarification["reason"] == "filesystem_scope_missing"
    assert clarification["questions"] == ["你要处理的是哪个对象？"]


def test_phase93_execution_evidence_gate_waits_for_real_completion_evidence() -> None:
    gate = decide_execution_evidence(
        pending_actions=[{"action_type": "browser.download", "user_label": "Download CSV"}],
        action={"action_type": "browser.download", "task_id": "tsk_123"},
        user_text="What evidence are you waiting for?",
        action_started=True,
    )

    assert gate.status == "waiting_evidence"
    assert gate.is_complete is False
    assert "artifact_ref" in gate.missing_evidence_types
    assert "waiting_for_execution_evidence" in gate.reason_codes
