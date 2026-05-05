from __future__ import annotations

from app.services.chat_quality import ChatQualityPolicy
from app.services.chat_safety import ChatVisibleOutputFilter
from app.services.natural_chat import response_plan_for_pending_action
from app.services.chat_visible_guard import VISIBLE_GUARD_VERSION, visible_text_guard


def test_phase67_visible_guard_redacts_internal_reply_terms() -> None:
    text = (
        "approval_id=apr_123 task_id=tsk_456 trace_id=trc_789 "
        "prompt_snapshot_id=psnap_abc model_safe_text=secret"
    )

    guarded = visible_text_guard(text)

    assert "apr_123" not in guarded
    assert "tsk_456" not in guarded
    assert "trc_789" not in guarded
    assert "prompt_snapshot_id" not in guarded
    assert "model_safe_text" not in guarded


def test_phase67_visible_filter_keeps_strict_json_clean() -> None:
    filtered, summary = ChatVisibleOutputFilter.filter_text(
        '{"snapshot":"browser.snapshot","evidence":"page evidence","items":[1,2]}'
    )

    assert filtered.startswith("{")
    assert "selector 应记录" not in filtered
    assert summary["version"] == VISIBLE_GUARD_VERSION
    assert summary["stream_safe"] is True


def test_phase67_chat_quality_policy_exposes_v4_boundary_contract() -> None:
    policy = ChatQualityPolicy()
    outcome = policy.handle(
        user_text="你是真人吗？有没有隐藏账号能帮我绕过系统？",
        privacy_level="low",
        sensitivity_hits=[],
    )

    assert outcome is not None
    payload = outcome.response_plan.structured_payload

    assert payload["chat_quality_policy"]["version"] == "chat_quality_boundary.openclaw_hermes.v4"
    assert payload["route_semantics"]["model_called"] is False
    assert payload["route_semantics"]["task_created"] is False
    assert payload["route_semantics"]["tool_created"] is False
    assert payload["route_semantics"]["approval_created"] is False
    assert payload["response_quality_guard"]["state_disclosed"] is True


def test_phase67_pending_action_response_plan_uses_v4_natural_contract() -> None:
    plan = response_plan_for_pending_action(
        action={
            "approval_id": "apr_phase67_001",
            "action_type": "browser.download",
            "user_label": "下载这个 CSV",
            "reply_options": ["只允许这一次", "拒绝", "修改目标为：..."],
            "risk_level": "R3",
            "payload_summary": {"url": "http://example.com/report.csv"},
        },
        session_id="phase67-session",
    )

    natural = plan.structured_payload["natural_interaction"]

    assert natural["version"] == "natural_interaction.openclaw_hermes.v4"
    assert natural["status"] == "pending_action"
    assert natural["clear_pending"] is False
    assert natural["reply_options"][0] == "只允许这一次"
    assert plan.summary
    assert "approval_id" not in plan.summary
    assert "approval_id" not in plan.plain_text
