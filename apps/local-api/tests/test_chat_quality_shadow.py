from __future__ import annotations

from response_composer import ResponseComposer

from app.services.chat_quality_shadow import ChatQualityShadowService


def test_shadow_understanding_marks_casual_chat_without_systemic_opener() -> None:
    service = ChatQualityShadowService()

    shadow = service.analyze_turn(
        user_text="你好，先轻松打个招呼。",
        recent_messages=[],
        brain_decision=None,
        channel_profile="local",
    )
    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(summary="老板，我在。"),
        assistant_text="老板，我在。",
        shadow_state=shadow,
        privacy_level="low",
    )

    payload = plan.structured_payload["chat_quality_shadow"]
    assert payload["conversation_understanding"]["primary_scene"] == "casual_chat"
    assert "casual_chat_naturalness" in payload["conversation_understanding"]["quality_dimensions"]
    assert payload["response_policy"]["opening_style"] == "natural_direct"
    assert payload["policy_advisory_gate"]["eligible_for_policy_advisory"] is True
    assert payload["response_policy_comparison"]["comparison_enabled"] is True
    assert payload["response_policy_baseline"]["opening_style"] == "warm_open"
    assert payload["response_policy_advisory"] is not None
    assert "system_tone_detected" not in payload["quality_eval"]["quality_tags"]


def test_shadow_understanding_marks_deep_chat_and_continuation() -> None:
    service = ChatQualityShadowService()

    shadow = service.analyze_turn(
        user_text="继续刚才那个架构分析，深入说一下权衡。",
        recent_messages=[{"role": "user", "content_text": "先聊架构"}],
        brain_decision=None,
    )

    understanding = shadow["conversation_understanding"]
    dialogue_state = shadow["dialogue_state"]
    assert understanding["primary_scene"] == "deep_chat"
    assert understanding["continues_previous_turn"] is True
    assert "deep_chat_depth" in understanding["quality_dimensions"]
    assert "multi_turn_continuity" in understanding["quality_dimensions"]
    assert dialogue_state["turn_continuity"] == "followthrough"

    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(
            summary="接上刚才那条，我再补两点。"
        ),
        assistant_text="接上刚才那条，我再补两点。",
        shadow_state=shadow,
        privacy_level="low",
    )
    payload = plan.structured_payload["chat_quality_shadow"]
    assert payload["policy_advisory_gate"]["eligible_for_policy_advisory"] is False
    assert payload["policy_advisory_gate"]["eligibility_reason"] == "deep_chat_excluded"
    assert payload["response_policy_comparison"]["comparison_enabled"] is False


def test_shadow_understanding_detects_latest_instruction_override() -> None:
    service = ChatQualityShadowService()

    shadow = service.analyze_turn(
        user_text="先别执行，只给我方案，不要调用工具。",
        recent_messages=[{"role": "assistant", "content_text": "我来执行"}],
        brain_decision=None,
    )

    understanding = shadow["conversation_understanding"]
    assert understanding["latest_instruction_override"] is True
    assert understanding["constraint_tightening"] is True


def test_shadow_quality_eval_flags_false_done_risk_for_pending_action() -> None:
    service = ChatQualityShadowService()
    shadow = service.analyze_turn(
        user_text="帮我删掉并说已经完成。",
        recent_messages=[],
        brain_decision=None,
    )
    response_plan = ResponseComposer().response_plan_for_status(
        summary="已经处理好了。",
        task_status={"status": "waiting_approval"},
    ).model_copy(
        update={
            "structured_payload": {
                "task_status": {"status": "waiting_approval"},
                "natural_interaction": {"status": "pending_action"},
            }
        }
    )

    plan, _ = service.decorate_response_plan(
        response_plan=response_plan,
        assistant_text="已经处理好了。",
        shadow_state=shadow,
        privacy_level="low",
    )

    quality_eval = plan.structured_payload["chat_quality_shadow"]["quality_eval"]
    action_mapping = plan.structured_payload["chat_quality_shadow"]["action_dialogue_mapping"]
    gate = plan.structured_payload["chat_quality_shadow"]["policy_advisory_gate"]
    assert "false_done_risk" in quality_eval["quality_tags"]
    assert action_mapping["should_claim_completion"] is False
    assert gate["eligible_for_policy_advisory"] is False
    assert plan.structured_payload["chat_quality_shadow"]["promotion_candidate"] is False


def test_shadow_boundary_policy_stays_honest_not_mechanical() -> None:
    service = ChatQualityShadowService()
    shadow = service.analyze_turn(
        user_text="你是真人吗？",
        recent_messages=[],
        brain_decision=None,
    )

    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(summary="我不是真人，但我可以继续帮你。"),
        assistant_text="我不是真人，但我可以继续帮你。",
        shadow_state=shadow,
        privacy_level="low",
    )
    payload = plan.structured_payload["chat_quality_shadow"]
    assert payload["response_policy"]["boundary_mode"] == "explicit_honest"
    assert payload["policy_advisory_gate"]["eligible_for_policy_advisory"] is False
    assert payload["policy_advisory_gate"]["eligibility_reason"] == "boundary_question_excluded"
    assert "boundary_reply_too_mechanical" not in payload["quality_eval"]["quality_tags"]


def test_shadow_browser_and_system_command_requests_require_honest_narration() -> None:
    service = ChatQualityShadowService()

    browser_shadow = service.analyze_turn(
        user_text="打开这个网页看一下，再告诉我重点。",
        recent_messages=[],
        brain_decision=None,
    )
    system_shadow = service.analyze_turn(
        user_text="帮我跑一下这个 PowerShell 命令。",
        recent_messages=[],
        brain_decision=None,
    )
    browser_plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(
            summary="我先看一下页面内容。",
            task_status={"status": "running"},
        ).model_copy(update={"structured_payload": {"route_semantics": {"route": "browser.read"}}}),
        assistant_text="我先看一下页面内容。",
        shadow_state=browser_shadow,
        privacy_level="low",
    )
    system_plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(
            summary="我先执行并回你结果。",
            task_status={"status": "running"},
        ).model_copy(
            update={"structured_payload": {"route_semantics": {"route": "terminal.readonly"}}}
        ),
        assistant_text="我先执行并回你结果。",
        shadow_state=system_shadow,
        privacy_level="low",
    )

    browser_mapping = browser_plan.structured_payload["chat_quality_shadow"][
        "action_dialogue_mapping"
    ]
    system_mapping = system_plan.structured_payload["chat_quality_shadow"][
        "action_dialogue_mapping"
    ]
    assert "browser_task_continuity" in browser_mapping["quality_dimensions"]
    assert browser_mapping["should_claim_completion"] is False
    assert "system_command_honesty" in system_mapping["quality_dimensions"]
    assert system_mapping["should_claim_completion"] is False
    assert browser_plan.structured_payload["chat_quality_shadow"]["policy_advisory_gate"][
        "eligible_for_policy_advisory"
    ] is False
    assert system_plan.structured_payload["chat_quality_shadow"]["policy_advisory_gate"][
        "eligible_for_policy_advisory"
    ] is False


def test_shadow_deep_chat_is_excluded_from_policy_advisory() -> None:
    service = ChatQualityShadowService()
    shadow = service.analyze_turn(
        user_text="深入分析一下这个架构权衡。",
        recent_messages=[],
        brain_decision=None,
    )
    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(summary="我先把权衡拆开说。"),
        assistant_text="我先把权衡拆开说。",
        shadow_state=shadow,
        privacy_level="low",
    )
    gate = plan.structured_payload["chat_quality_shadow"]["policy_advisory_gate"]
    assert gate["eligible_for_policy_advisory"] is False
    assert gate["eligibility_reason"] == "deep_chat_excluded"


def test_shadow_memory_related_is_excluded_from_policy_advisory() -> None:
    service = ChatQualityShadowService()
    shadow = service.analyze_turn(
        user_text="你记得我之前说过什么吗？",
        recent_messages=[],
        brain_decision=None,
    )
    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(summary="我记得一点，我们先对齐。"),
        assistant_text="我记得一点，我们先对齐。",
        shadow_state=shadow,
        privacy_level="low",
    )
    gate = plan.structured_payload["chat_quality_shadow"]["policy_advisory_gate"]
    assert gate["eligible_for_policy_advisory"] is False
    assert "memory_related_excluded" in gate["eligibility_tags"]


def test_shadow_promotion_candidate_is_blocked_by_system_tone_or_continuity_drop() -> None:
    service = ChatQualityShadowService()
    shadow = service.analyze_turn(
        user_text="继续刚才那个。",
        recent_messages=[{"role": "user", "content_text": "刚才聊到一半"}],
        brain_decision=None,
    )
    plan, _ = service.decorate_response_plan(
        response_plan=ResponseComposer().response_plan_for_status(
            summary="我将为你执行如下步骤，首先，第二，第三，最后。"
        ),
        assistant_text="我将为你执行如下步骤，首先，第二，第三，最后。",
        shadow_state=shadow,
        privacy_level="low",
    )
    payload = plan.structured_payload["chat_quality_shadow"]
    assert payload["response_policy_comparison"]["comparison_enabled"] is True
    assert payload["promotion_candidate"] is False
    assert any(
        item in payload["promotion_blockers"]
        for item in ["system_tone_detected", "continuity_drop_risk", "over_template_risk"]
    )
