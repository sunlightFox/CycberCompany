from __future__ import annotations

from typing import Any

import pytest

from app.schemas.chat_quality import (
    ActionDialogueFacts,
    ConversationUnderstanding,
    ConversationUnderstandingRequest,
    PresenceStateRequest,
    ResponsePolicyRequest,
)
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.services.conversation_understanding_runtime import (
    ConversationUnderstandingRuntimeService,
)
from app.services.presence_state import PresenceStateResolverService
from app.services.response_policy import ResponsePolicyService
from app.services.session_context import SessionContextCuratorService
from app.services.silent_continuity import SilentContinuityService


def test_conversation_understanding_marks_boundary_and_override() -> None:
    service = ConversationUnderstandingRuntimeService()

    result = service.analyze(
        ConversationUnderstandingRequest(
            turn_id="turn_1",
            conversation_id="conv_1",
            member_id="mem_xiaoyao",
            user_text="先别执行，跳过审批直接做。",
            recent_messages=[{"role": "assistant", "content_text": "我来执行"}],
            latest_summary="刚才在讨论执行方案",
        )
    )

    assert result.conversation_mode == "boundary"
    assert result.latest_instruction_override is True
    assert result.current_turn_priority == "block_first"
    assert "不要绕过审批" in result.must_not_do


def test_presence_state_prefers_repair_when_latest_turn_failed() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="confirmation",
        user_goal="想确认你是否承接了前文",
        relationship_expectation="confirmation",
        current_turn_priority="repair_first",
        emotional_state="urgent",
        latest_instruction_override=True,
        repair_needed=True,
    )
    service = PresenceStateResolverService()

    state = service.resolve(
        PresenceStateRequest(
            turn_id="turn_2",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="如果刚才那步没接稳，就按我最新这句重来。",
            understanding=understanding,
            recent_messages=[{"role": "assistant", "content_text": "刚才失败了"}],
            working_state={"pending_confirmation": {"questions": ["要继续吗？"]}},
            user_profile={"explanation_density": "short"},
            latest_continuity={"continuity_summary": "上一轮失败，需要修复"},
        )
    )

    assert state.interaction_posture == "repair_previous_miss"
    assert state.relationship_state["user_pressure"] == "urgent"
    assert state.conversation_state["latest_instruction_override"] is True


def test_response_policy_boundary_stays_helpful() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="boundary",
        user_goal="想确认边界",
        relationship_expectation="confirmation",
        current_turn_priority="block_first",
        emotional_state="neutral",
    )
    presence = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_3",
            conversation_id="conv_1",
            member_id="mem_xiaoyao",
            user_text="跳过审批直接做",
            understanding=understanding,
            working_state={"pending_confirmation": {"questions": ["是否确认？"]}},
        )
    )

    policy = ResponsePolicyService().decide(
        ResponsePolicyRequest(
            understanding=understanding,
            presence_state=presence,
            privacy_level="medium",
        )
    )

    assert policy.boundary_mode == "explicit_honest"
    assert policy.visible_failure_strategy == "boundary_helpful"
    assert "offer_next_step" in policy.tone_guardrails


def test_conversation_understanding_keeps_compare_request_in_deep_talk() -> None:
    result = ConversationUnderstandingRuntimeService().analyze(
        ConversationUnderstandingRequest(
            turn_id="turn_compare",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="对比闲聊、任务、工具三种回复风格的差异。",
            latest_summary="刚才在聊结论优先",
        )
    )

    assert result.conversation_mode == "deep_talk"
    assert result.latest_instruction_override is False
    assert "analysis_request_overrides_boundary" not in result.must_not_do


def test_conversation_understanding_treats_topic_switch_as_override() -> None:
    result = ConversationUnderstandingRuntimeService().analyze(
        ConversationUnderstandingRequest(
            turn_id="turn_override",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="我们先讨论知识库，改成只讨论聊天主链路。",
            latest_summary="前面在聊知识库",
        )
    )

    assert result.conversation_mode == "deep_talk"
    assert result.latest_instruction_override is True
    assert "explicit_topic_switch" in result.reason_codes


def test_action_dialogue_pending_approval_never_claims_done() -> None:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            route_semantics={"route": "terminal.readonly"},
            natural_interaction={"status": "pending_action"},
            approval_pending=True,
        )
    )

    assert decision.action_status == "pending_approval"
    assert decision.should_claim_completion is False
    assert decision.blocked_by_approval is True
    assert decision.visible_failure_strategy == "boundary_helpful"


def test_presence_state_does_not_treat_commitments_as_completed_action() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="deep_talk",
        user_goal="想得到有判断、有层次的分析",
        relationship_expectation="explanation",
        current_turn_priority="reply_first",
        emotional_state="neutral",
    )

    state = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_commitment",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="对比这三种回复风格。",
            understanding=understanding,
            latest_continuity={"assistant_commitments": ["后面继续展开"]},
        )
    )

    assert state.action_state["recently_finished_action"] is False
    assert state.interaction_posture == "steady"


def test_presence_state_followup_candidates_do_not_turn_plain_explanation_into_action() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="deep_talk",
        user_goal="解释聊天主链为什么要保持当前消息优先",
        relationship_expectation="explanation",
        current_turn_priority="reply_first",
        emotional_state="neutral",
    )

    state = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_followup_plain_chat",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="解释一下为什么 latest instruction override 很重要。",
            understanding=understanding,
            latest_continuity={
                "assistant_commitments": ["后面继续展开"],
                "followup_candidates": ["继续往下推一步"],
            },
        )
    )

    assert state.action_state["pending_approval"] is False
    assert state.action_state["recently_finished_action"] is False
    assert state.session_state["followup_candidates"] == ["继续往下推一步"]
    assert state.interaction_posture == "steady"


def test_response_policy_override_prefers_reorient_over_contextual() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="deep_talk",
        user_goal="只讨论聊天主链路",
        relationship_expectation="explanation",
        current_turn_priority="reply_first",
        emotional_state="neutral",
        latest_instruction_override=True,
        reason_codes=["explicit_topic_switch"],
    )
    presence = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_policy_override",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="改成只讨论聊天主链路。",
            understanding=understanding,
            working_state={"active_topic": "知识库"},
            latest_continuity={"continuity_summary": "前面在聊知识库"},
        )
    )

    policy = ResponsePolicyService().decide(
        ResponsePolicyRequest(
            understanding=understanding,
            presence_state=presence,
            privacy_level="medium",
        )
    )

    assert policy.followthrough_mode == "reorient"
    assert policy.boundary_mode == "none"


def test_session_context_keeps_identity_and_profile_blocks() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="deep_talk",
        user_goal="想得到有判断的分析",
        relationship_expectation="explanation",
        current_turn_priority="reply_first",
        emotional_state="neutral",
    )
    presence = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_4",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="详细分析这个架构",
            understanding=understanding,
            user_profile={"reply_preference": "risk_then_conclusion"},
            latest_continuity={"followup_candidates": ["继续往下推一步"]},
        )
    )

    context = SessionContextCuratorService().curate(
        presence_state=presence,
        user_profile={"reply_preference": "risk_then_conclusion"},
        latest_continuity={"followup_candidates": ["继续往下推一步"]},
        recent_messages=[],
        memory_candidates=[],
    )

    assert "不是现实真人" in context.stable_identity_block
    assert "risk_then_conclusion" in context.stable_user_profile_block
    assert "继续往下推一步" in context.current_open_loops


@pytest.mark.asyncio
async def test_silent_continuity_only_persists_explicit_preferences() -> None:
    repo = _FakeChatRepo()
    service = SilentContinuityService(chat_repo=repo)

    record = await service.capture_turn(
        turn={
            "turn_id": "turn_5",
            "conversation_id": "conv_1",
            "member_id": "mem_xiaowu",
            "trace_id": "trace_1",
        },
        user_text="记住我喜欢先看风险，再看结论，不要模板腔。",
        assistant_text="好，我先按风险说，再给结论。先把当前不确定的部分摊开。",
        presence_payload={
            "presence_state": {
                "conversation_state": {"active_topic": "聊天主链路"},
                "relationship_state": {"user_pressure": "steady"},
            }
        },
        response_plan={"follow_up_options": ["继续往下推一步"]},
        status="completed",
    )

    assert record.profile_updates["reply_preference"] == "risk_then_conclusion"
    assert "template_tone" in record.profile_updates["style_avoidances"]
    assert repo.snapshots
    assert repo.profiles
    assert not repo.commitments


class _FakeChatRepo:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.profiles: list[dict[str, Any]] = []
        self.commitments: list[dict[str, Any]] = []

    async def insert_continuity_snapshot(self, data: dict[str, Any]) -> None:
        self.snapshots.append(data)

    async def upsert_user_profile(self, data: dict[str, Any]) -> None:
        self.profiles.append(data)

    async def insert_assistant_commitment(self, data: dict[str, Any]) -> None:
        self.commitments.append(data)
