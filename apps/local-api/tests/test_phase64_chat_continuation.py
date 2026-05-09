from __future__ import annotations

from typing import Any, cast

from app.services.chat_continuation import (
    CHAT_CONTINUATION_GATE_VERSION,
    ChatContinuationCoordinator,
    ContinuationDecision,
    ContinuationEvaluation,
)


def test_phase64_wechat_plain_chat_uses_fast_path() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = coordinator.decide(
        turn={"experience": {"client_context": {"ui_mode": "wechat_chat"}}},
        user_text="你好，小曜，闲聊两句。",
        context=cast(Any, object()),
        intent="chat",
        mode="direct",
    )

    assert decision.enabled is False
    assert decision.reason_codes == ["default_disabled"]


def test_phase64_wechat_complex_reply_stays_disabled_by_default() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = coordinator.decide(
        turn={
            "experience": {
                "client_context": {"ui_mode": "wechat_chat"},
                "complexity_score": 0.72,
                "needs_long_output": True,
                "route_profile": "deep_reasoning",
            }
        },
        user_text="帮我分析网上用户关心的办公 AI 场景，给出质量和耗时优化方案。",
        context=cast(Any, object()),
        intent="question_answer",
        mode="direct",
    )
    assert decision.enabled is False
    assert decision.reason_codes == ["default_disabled"]


def test_phase64_wechat_multimodal_context_stays_disabled_by_default() -> None:
    coordinator = ChatContinuationCoordinator()
    user_text = (
        "小吴，听一下这段语音\n"
        "语音转成文字：今天先把图片识别和文件识别串起来，回复口吻自然一点"
    )
    decision = coordinator.decide(
        turn={"experience": {"client_context": {"ui_mode": "wechat_chat"}}},
        user_text=user_text,
        context=cast(Any, object()),
        intent="chat",
        mode="direct",
    )
    assert decision.enabled is False
    assert decision.reason_codes == ["default_disabled"]


def test_phase64_wechat_continuation_evaluation_flags_internal_terms_emoji_and_false_done() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, latency_budget_ms=500, max_iterations=1)
    evaluation = coordinator.evaluate(
        text="好的，trace_id 已经删除 😀",
        user_text="帮我删除桌面上的文件，但先不要真的执行。",
        decision=decision,
        elapsed_ms=800,
    )

    assert evaluation.verdict == "block"
    expected_tags = {"internal_jargon", "face_emoji", "false_done"}
    assert expected_tags.issubset(set(evaluation.tags))
    assert any("预算" in item or "续跑" in item for item in evaluation.suggestions)


def test_phase64_wechat_hard_boundary_tone_is_flagged_for_revision() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["complexity_high"])
    evaluation = coordinator.evaluate(
        text="这一步得你点头后我再继续，不会把没做的事说成做完。",
        user_text="帮我确认这个高风险操作能不能直接执行。",
        decision=decision,
    )

    assert evaluation.verdict in {"good", "revise"}
    assert "hard_boundary_tone" not in evaluation.tags


def test_phase64_wechat_continuation_payload_carries_latency_diagnostics() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["complexity_high"])
    evaluation = ContinuationEvaluation(
        verdict="revise",
        tags=["too_short"],
        suggestions=["补足内容"],
        diagnostics={
            "content": "warn",
            "structure": "ok",
            "voice": "ok",
            "safety": "ok",
            "evidence": "ok",
            "multimodal": "skip",
            "latency": "warn",
            "composer_guard": "skip",
        },
    )

    payload = coordinator.payload(
        decision=decision,
        evaluation=evaluation,
        iterations=1,
        budget_exhausted=True,
        used_revision=True,
        initial_latency_ms=120,
        revision_latency_ms=340,
        total_latency_ms=950,
    )

    assert payload["enabled"] is True
    assert payload["iterations"] == 1
    assert payload["reason_codes"] == ["complexity_high"]
    assert payload["quality_verdict"] == "revise"
    assert payload["quality_tags"] == ["too_short"]
    assert payload["version"] == CHAT_CONTINUATION_GATE_VERSION
    assert payload["trigger_profile"] == "wechat_quality_gate"
    assert payload["diagnostics"]["content"] == "warn"
    assert set(payload["diagnostics"]) == {
        "content",
        "structure",
        "voice",
        "safety",
        "evidence",
        "multimodal",
        "latency",
        "composer_guard",
    }
    assert payload["latency_budget_ms"] == 20_000
    assert payload["initial_latency_ms"] == 120
    assert payload["revision_latency_ms"] == 340
    assert payload["total_latency_ms"] == 950
    assert payload["budget_exhausted"] is True
    assert payload["used_revision"] is True


def test_phase64_strict_format_disables_continuation() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = coordinator.decide(
        turn={
            "experience": {
                "client_context": {"ui_mode": "wechat_chat"},
                "complexity_score": 0.9,
                "needs_long_output": True,
            }
        },
        user_text='只输出 JSON：{"summary":"ok"}',
        context=cast(Any, object()),
        intent="question_answer",
        mode="direct",
    )

    assert decision.enabled is False
    assert decision.reason_codes == ["strict_format_request"]


def test_phase64_fixed_boundary_reply_disables_continuation() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = coordinator.decide(
        turn={
            "experience": {
                "client_context": {"ui_mode": "wechat_chat"},
                "complexity_score": 0.95,
                "needs_long_output": True,
            }
        },
        user_text="帮我判断这个高风险操作能不能直接执行并给出拒绝理由。",
        context=cast(Any, object()),
        intent="boundary_question",
        mode="direct",
    )

    assert decision.enabled is False
    assert decision.reason_codes == ["fixed_boundary_reply"]


def test_phase64_wechat_composer_guard_warnings_enter_diagnostics() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["complexity_high"])
    evaluation = coordinator.evaluate(
        text="这版看起来还行，但内部字段没清掉。",
        user_text="帮我把上一段复杂回复再整理一下。",
        decision=decision,
        response_quality_guard={
            "status": "warning",
            "checks": {
                "no_internal_terms": False,
                "no_false_done": True,
                "strict_format_preserved": True,
            },
        },
    )

    assert evaluation.verdict == "block"
    assert evaluation.diagnostics["composer_guard"] == "fail"
    assert "internal_jargon" in evaluation.tags


def test_phase64_secret_leak_triggers_blocking_repair() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["forced_visible_review"])
    evaluation = coordinator.evaluate(
        text="这是调试结果，token=sk-phase64-secret-value。",
        user_text="把这段结果转述给我，但不要泄漏敏感值。",
        decision=decision,
    )

    assert evaluation.verdict == "block"
    assert "secret_leak" in evaluation.tags
    assert evaluation.should_revise is True


def test_phase64_strict_format_pollution_is_repairable() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["forced_visible_review"])
    evaluation = coordinator.evaluate(
        text='先解释一下：{"summary":"ok","risks":["a","b"]}',
        user_text='只输出 JSON：{"summary":"ok","risks":["a","b"]}',
        decision=decision,
    )

    assert evaluation.verdict == "block"
    assert "strict_format_polluted" in evaluation.tags
    assert evaluation.should_revise is True


def test_phase64_plain_but_direct_reply_does_not_trigger_repair() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["forced_visible_review"])
    evaluation = coordinator.evaluate(
        text="结论先说：普通聊天主链要保持当前消息优先，其次才是最近历史。",
        user_text="总结一下普通聊天主链最重要的原则。",
        decision=decision,
    )

    assert evaluation.verdict == "good"
    assert evaluation.should_revise is False
    assert not {"missing_reply", "strict_format_polluted", "secret_leak", "internal_jargon"} & set(
        evaluation.tags
    )


def test_phase64_runtime_policy_does_not_block_followthrough_copy_by_itself() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["complexity_high"])
    evaluation = coordinator.evaluate(
        text="接着刚才那条。我的判断是，闲聊重在接住情绪，任务重在目标推进，工具重在边界诚实。",
        user_text="对比闲聊、任务、工具三种回复风格的差异。",
        decision=decision,
    )

    assert "unjustified_followthrough" not in evaluation.tags


def test_phase64_runtime_policy_does_not_block_topic_switch_copy_by_itself() -> None:
    coordinator = ChatContinuationCoordinator()
    decision = ContinuationDecision(enabled=True, reason_codes=["complexity_high"])
    evaluation = coordinator.evaluate(
        text="接着刚才那条，我们继续聊知识库。",
        user_text="我们先讨论知识库，改成只讨论聊天主链路。",
        decision=decision,
    )

    assert "topic_switch_ignored" not in evaluation.tags
