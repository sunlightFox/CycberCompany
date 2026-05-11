from __future__ import annotations

import pytest
from response_composer import ComposeRequest, ResponseComposer
from response_composer.chat_voice import render_continuation_revision_prompt

from app.services.natural_chat import response_plan_for_pending_action


@pytest.mark.asyncio
async def test_response_policy_can_restructure_visible_text_for_deep_reply() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(
        user_text="继续刚才那个方案，但别执行，只说风险。",
        result_summary="先说风险。第一，旧约束会压住当前目标。第二，确认前不能把执行说成已经完成。第三，下一步要先把口径定清。",
        response_policy={
            "opening_style": "judgment_first",
            "depth_mode": "deep",
            "followthrough_mode": "contextual",
            "boundary_mode": "none",
            "progress_mode": "answer_then_expand",
            "structure_mode": "structured_when_useful",
        },
        session_context={"current_conversation_summary": "刚才在聊同一个方案"},
        )
    )

    text = result.text
    payload = result.response_plan.structured_payload
    assert "我的判断是" not in text
    assert "接着刚才那条" not in text
    assert text.startswith("先说风险。")
    assert "\n" in text
    assert payload["response_policy"]["opening_style"] == "judgment_first"
    assert payload["session_context"]["current_conversation_summary"] == "刚才在聊同一个方案"


def test_pending_action_plan_keeps_action_metadata_without_runtime_applied_fields() -> None:
    plan = response_plan_for_pending_action(
        action={
            "approval_id": "apr_runtime_1",
            "action_type": "browser.download",
            "user_label": "下载这个 CSV",
            "reply_options": ["只允许这一次", "拒绝", "修改目标为：..."],
            "risk_level": "R3",
            "payload_summary": {"url": "http://example.com/report.csv"},
        },
        session_id="runtime-session",
        presence_runtime={
            "response_policy": {
                "opening_style": "steady_boundary",
                "depth_mode": "light",
                "followthrough_mode": "boundary",
                "boundary_mode": "explicit_honest",
                "progress_mode": "next_step_after_boundary",
                "structure_mode": "adaptive",
            }
        },
    )

    payload = plan.structured_payload
    assert payload["action_dialogue"]["action_status"] == "waiting_for_approval"
    assert payload["action_status_semantics"]["status"] == "waiting_for_approval"
    assert payload["response_policy"]["boundary_mode"] == "explicit_honest"
    assert "presence_runtime" not in payload


def test_continuation_revision_prompt_uses_quality_findings_only() -> None:
    prompt = render_continuation_revision_prompt(
        user_text="刚才那步没接稳，按我最新这句重来。",
        draft_text="下面是处理结果。",
        quality_tags=["robotic_template"],
        suggestions=["先把上一拍收住，再接当前这句。"],
        diagnostics={"voice": "warn"},
    )

    assert "robotic_template" in prompt
    assert "先把上一拍收住" in prompt
    assert "opening_style" not in prompt


@pytest.mark.asyncio
async def test_response_policy_reorients_to_latest_instruction_without_boundary_copy() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(
            user_text="对比闲聊、任务、工具三种回复风格的差异。",
            result_summary="闲聊重在接住情绪和语气。任务重在目标和下一步。工具类回复重在边界、状态和结果诚实。",
            response_policy={
                "opening_style": "judgment_first",
                "depth_mode": "deep",
                "followthrough_mode": "standalone",
                "boundary_mode": "none",
                "progress_mode": "answer_then_expand",
                "structure_mode": "structured_when_useful",
            },
            session_context={
                "current_conversation_summary": "前面在聊别的话题",
                "current_open_loops": ["latest_instruction_overrides_previous_goal"],
                "continuity_mode": "topic_switch",
            },
        )
    )

    text = result.text
    assert text.startswith("按你刚刚改的这句，")
    assert "这一步我先停在确认前" not in text
    assert "前面在聊别的话题" not in text


def test_compose_failure_uses_natural_language_for_model_route_and_context_failures() -> None:
    composer = ResponseComposer()

    model_text = composer.compose_failure("MODEL_ROUTE_NOT_FOUND", "没有可用模型路由")
    context_text = composer.compose_failure("CONTEXT_BUILD_FAILED", "上下文构建失败")

    assert "没有可用模型路由" not in model_text
    assert "没拿到能用的模型路由" in model_text
    assert "上下文构建失败" not in context_text
    assert "没把上下文接稳" in context_text
