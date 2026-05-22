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

    assert decision.action_status == "waiting_for_approval"
    assert decision.should_claim_completion is False
    assert decision.blocked_by_approval is True
    assert decision.visible_failure_strategy == "boundary_helpful"


def test_action_dialogue_scheduled_task_reply_is_natural_and_non_technical() -> None:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            domain="scheduled_task",
            visible_goal="帮我创建一个定时任务，每天 09:00 提醒我整理今天待办。",
            route_semantics={"route": "scheduled_task"},
            task_status={
                "status": "completed_with_evidence",
                "schedule": {"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
            },
            task_created=True,
        )
    )

    assert decision.action_status == "scheduled_created"
    assert decision.visible_text == "好，以后每天早上 9 点提醒你整理今天待办。"
    assert "调度方式" not in decision.visible_text
    assert "下一次执行时间" not in decision.visible_text
    assert "Asia/Shanghai" not in decision.visible_text


def test_action_dialogue_scheduled_payment_reply_only_sets_reminder_boundary() -> None:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            domain="scheduled_task",
            visible_goal="明天下午 3 点提醒我给供应商付款 5000 元",
            route_semantics={"route": "scheduled_task"},
            task_status={
                "status": "completed_with_evidence",
                "schedule": {
                    "type": "once",
                    "run_at": "2026-05-23T15:00:00+08:00",
                    "timezone": "Asia/Shanghai",
                },
            },
            task_created=True,
        )
    )

    assert decision.visible_text == "好，明天下午 3 点提醒你给供应商付款 5000 元。这个我只提醒，不会自动付款。"
    assert decision.requires_user_confirmation is True
    assert "高风险动作" not in decision.visible_text
    assert "后台流程" not in decision.visible_text


def test_action_dialogue_scheduled_english_prefix_is_removed() -> None:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            domain="scheduled_task",
            visible_goal="Create a scheduled reminder: 每天 06:40 提醒我 stretch and drink water.",
            route_semantics={"route": "scheduled_task"},
            task_status={
                "status": "completed_with_evidence",
                "schedule": {"type": "daily", "time": "06:40", "timezone": "Asia/Shanghai"},
            },
            task_created=True,
        )
    )

    assert decision.visible_text == "好，以后每天早上 6:40 提醒你 stretch and drink water。"
    assert "Create a scheduled reminder" not in decision.visible_text
    assert "06:40 提醒我" not in decision.visible_text


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


def test_session_context_keeps_roleplay_contract_beyond_recent_tail() -> None:
    understanding = ConversationUnderstanding(
        conversation_mode="deep_talk",
        user_goal="继续角色聊天",
        relationship_expectation="companionship",
        current_turn_priority="reply_first",
        emotional_state="neutral",
    )
    presence = PresenceStateResolverService().resolve(
        PresenceStateRequest(
            turn_id="turn_roleplay",
            conversation_id="conv_1",
            member_id="mem_xiaowu",
            user_text="沿用角色提醒我今晚最该做的两件事。",
            understanding=understanding,
        )
    )

    context = SessionContextCuratorService().curate(
        presence_state=presence,
        user_profile={},
        latest_continuity={},
        recent_messages=[
            {"author_type": "user", "content_text": "角色扮演开始：接下来你要扮演可靠姐姐和我聊天。请自然带出「别硬撑」或明显身份词。"},
            {"author_type": "assistant", "content_text": "姐姐在。"},
            {"author_type": "user", "content_text": "我不想听大道理。"},
            {"author_type": "assistant", "content_text": "先稳住。"},
            {"author_type": "user", "content_text": "明天早上有重要安排。"},
            {"author_type": "assistant", "content_text": "先准备东西。"},
        ],
        memory_candidates=[],
    )

    serialized = "\n".join(str(item.get("content_text") or "") for item in context.relevant_recent_messages)
    assert "别硬撑" in serialized
    assert len(context.relevant_recent_messages) == 5


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


@pytest.mark.asyncio
async def test_silent_continuity_persists_and_merges_summary_structure_preference() -> None:
    repo = _FakeChatRepo()
    repo.active_profile = {"profile_data": {"reply_preference": "risk_then_conclusion"}}
    service = SilentContinuityService(chat_repo=repo)

    record = await service.capture_turn(
        turn={
            "turn_id": "turn_6",
            "conversation_id": "conv_1",
            "member_id": "mem_xiaowu",
            "trace_id": "trace_2",
        },
        user_text="记住这轮对话里的总结偏好：先标题，再表格，最后一段结论。",
        assistant_text="好，我记住了。",
        presence_payload={
            "presence_state": {
                "conversation_state": {"active_topic": "知识总结"},
                "relationship_state": {"user_pressure": "steady"},
            }
        },
        response_plan={"follow_up_options": []},
        status="completed",
    )

    assert record.profile_updates["summary_structure_preference"] == "先标题，再表格，最后一段结论"
    assert repo.profiles[-1]["profile_data"]["reply_preference"] == "risk_then_conclusion"
    assert repo.profiles[-1]["profile_data"]["summary_structure_preference"] == "先标题，再表格，最后一段结论"


@pytest.mark.asyncio
async def test_silent_continuity_treats_summary_structure_correction_as_preference_update() -> None:
    repo = _FakeChatRepo()
    service = SilentContinuityService(chat_repo=repo)

    record = await service.capture_turn(
        turn={
            "turn_id": "turn_7",
            "conversation_id": "conv_1",
            "member_id": "mem_xiaowu",
            "trace_id": "trace_3",
        },
        user_text="修正一下，这轮接下来的总结不要表格了，改成标题 + 两段段落。",
        assistant_text="收到，后面按标题加两段段落来。",
        presence_payload={
            "presence_state": {
                "conversation_state": {"active_topic": "知识总结"},
                "relationship_state": {"user_pressure": "steady"},
            }
        },
        response_plan={"follow_up_options": []},
        status="completed",
    )

    assert record.profile_updates["summary_structure_preference"] == "标题 + 两段段落"


class _FakeChatRepo:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.profiles: list[dict[str, Any]] = []
        self.commitments: list[dict[str, Any]] = []
        self.active_profile: dict[str, Any] | None = None

    async def insert_continuity_snapshot(self, data: dict[str, Any]) -> None:
        self.snapshots.append(data)

    async def upsert_user_profile(self, data: dict[str, Any]) -> None:
        self.profiles.append(data)
        self.active_profile = data

    async def get_active_user_profile(self, conversation_id: str) -> dict[str, Any] | None:
        del conversation_id
        return self.active_profile

    async def insert_assistant_commitment(self, data: dict[str, Any]) -> None:
        self.commitments.append(data)
