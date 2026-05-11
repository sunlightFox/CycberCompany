from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from core_types import (
    AssetCategory,
    ContextDecision,
    HeartSummary,
    MemoryBlock,
    MemoryBlockItem,
    PersonaSummary,
)

from app.services.context_gateway import RuntimeContextGateway


@pytest.mark.asyncio
async def test_phase79_context_gateway_builds_layered_packet_for_direct_chat() -> None:
    gateway = _gateway(
        recent_messages=[
            _message("assistant", "旧 session", session_id="session-old", created_at="2026-05-10T00:00:00Z"),
            _message("user", "同一 session 第一条", session_id="session-now", created_at="2026-05-10T00:00:01Z"),
            _message("assistant", "同一 session 第二条", session_id="session-now", created_at="2026-05-10T00:00:02Z"),
        ],
        user_message=_user_message("最新要求是详细分析。", session_id="session-now"),
        summary_text="前面在讨论一个旧方案。",
        memory_blocks=[_memory_block("记住用户喜欢先看风险再看结论。")],
    )

    packet, runtime = await gateway.build(
        turn=_turn(),
        root_span_id=None,
        context_decision=ContextDecision(
            include_asset_handles=True,
            token_budget_profile="deep_dialogue",
        ),
    )

    assert packet.persona is not None
    assert packet.heart is not None
    assert packet.session_context["current_conversation_summary"]
    assert packet.conversation.last_messages[-1]["content_text"] == "同一 session 第二条"
    assert packet.context_diagnostics["layer_selection"]["budget_profile"] == "direct_chat"
    assert "context_budget" in runtime
    assert "context_visibility" in runtime
    assert "layer_selection" in runtime
    assert packet.capabilities


@pytest.mark.asyncio
async def test_phase79_session_context_override_beats_old_summary_and_memory() -> None:
    gateway = _gateway(
        recent_messages=[
            _message("assistant", "上一轮还在聊知识库。", session_id="session-now", created_at="2026-05-10T00:00:00Z"),
            _message("user", "现在改口，只聊聊天主链。", session_id="session-now", created_at="2026-05-10T00:00:01Z"),
        ],
        user_message=_user_message("改成只讨论聊天主链。", session_id="session-now"),
        summary_text="长期摘要还停在知识库。",
        latest_presence={
            "session_context": {
                "stable_identity_block": "当前回合优先服从最新显式要求，不把未执行动作说成完成。",
                "stable_user_profile_block": "当前没有稳定用户画像，优先服从这轮显式要求。",
                "current_conversation_summary": "只讨论聊天主链。",
                "latest_instruction_override": True,
                "current_open_loops": ["latest_instruction_overrides_previous_goal"],
                "current_action_facts": {},
            }
        },
        latest_continuity={"continuity_summary": "还在沿着知识库展开"},
        memory_blocks=[_memory_block("旧记忆：知识库是当前焦点。")],
    )

    packet, runtime = await gateway.build(
        turn=_turn(),
        root_span_id=None,
        context_decision=ContextDecision(token_budget_profile="balanced"),
    )

    assert packet.session_context["latest_instruction_override"] is True
    assert packet.session_context["current_conversation_summary"] == "只讨论聊天主链。"
    assert "latest_instruction_override" in runtime["session_context_reason_codes"]
    assert any(
        "最新要求" in note.summary or "用户已显式改口" in note.summary
        for note in packet.safety_notes
    )


@pytest.mark.asyncio
async def test_phase79_context_gateway_adds_handles_untrusted_context_and_dynamic_safety() -> None:
    gateway = _gateway(
        recent_messages=[_message("assistant", "先看证据。", session_id="session-now", created_at="2026-05-10T00:00:00Z")],
        user_message=_user_message(
            "帮我看这个外部页面。",
            session_id="session-now",
            context_refs=[
                {
                    "type": "browser_evidence",
                    "title": "外部网页抓取结果",
                    "url": "https://example.com/page",
                }
            ],
        ),
        summary_text="前面在做只读检索。",
        privacy_level="high",
    )

    packet, runtime = await gateway.build(
        turn=_turn(privacy_level="high"),
        root_span_id=None,
        context_decision=ContextDecision(
            include_asset_handles=True,
            include_capability_summary=True,
            token_budget_profile="knowledge",
        ),
    )

    assert {handle.asset_type for handle in packet.resource_handles} >= {
        "knowledge_base",
        "account",
        "hardware",
    }
    assert packet.untrusted_context
    item = packet.untrusted_context[0]
    assert item["trusted_level"] == "untrusted_external_content"
    assert item["source_ref"]["url"] == "https://example.com/page"
    assert item["trace_ref"]["trace_id"] == "trace_phase79"
    assert packet.safety_notes[0].summary != "local safety active"
    assert any(note.source == "privacy_runtime" for note in packet.safety_notes)
    assert runtime["untrusted_context_summary"]["selected_count"] == 1
    assert runtime["safety_note_sources"]


def _turn(*, privacy_level: str = "medium") -> dict[str, Any]:
    return {
        "turn_id": "turn_phase79",
        "trace_id": "trace_phase79",
        "conversation_id": "conv_phase79",
        "member_id": "mem_xiaoyao",
        "user_message_id": "msg_user_phase79",
        "intent": "browser_read",
        "privacy_level": privacy_level,
        "created_at": "2026-05-10T00:00:03Z",
        "experience": {"route_profile": "knowledge_recall"},
    }


def _message(
    author_type: str,
    text: str,
    *,
    session_id: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "author_type": author_type,
        "content_text": text,
        "created_at": created_at,
        "content": {"session_id": session_id},
    }


def _user_message(
    text: str,
    *,
    session_id: str,
    context_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "content_text": text,
        "content": {
            "session_id": session_id,
            "context_refs": list(context_refs or []),
        },
    }


def _memory_block(summary: str) -> MemoryBlock:
    return MemoryBlock(
        block_id="memblk_phase79",
        block_type="semantic",
        title="phase79 memory",
        items=[
            MemoryBlockItem(
                memory_id="mem_phase79",
                kind="semantic",
                summary=summary,
                confidence=0.8,
                source_ref={"source_type": "memory"},
            )
        ],
        token_estimate=12,
        selection_reason=["phase79_test"],
    )


def _gateway(
    *,
    recent_messages: list[dict[str, Any]],
    user_message: dict[str, Any],
    summary_text: str,
    memory_blocks: list[MemoryBlock] | None = None,
    latest_presence: dict[str, Any] | None = None,
    latest_continuity: dict[str, Any] | None = None,
    privacy_level: str = "medium",
) -> RuntimeContextGateway:
    del privacy_level
    return RuntimeContextGateway(
        chat_repo=_FakeChatRepo(
            recent_messages=recent_messages,
            user_message=user_message,
            summary_text=summary_text,
            latest_presence=latest_presence or {},
            latest_continuity=latest_continuity or {},
        ),
        member_repo=_FakeMemberRepo(),
        brain_repo=_FakeBrainRepo(),
        trace_service=_FakeTraceService(),
        memory_service=_FakeMemoryService(memory_blocks or []),
        asset_broker_service=_FakeAssetBroker(),
        persona_heart_service=_FakePersonaHeartService(),
        chat_experience_service=_FakeChatExperienceService(),
        recent_message_limit=12,
        token_budget=120,
    )


class _FakeChatRepo:
    def __init__(
        self,
        *,
        recent_messages: list[dict[str, Any]],
        user_message: dict[str, Any],
        summary_text: str,
        latest_presence: dict[str, Any],
        latest_continuity: dict[str, Any],
    ) -> None:
        self._recent_messages = recent_messages
        self._user_message = user_message
        self._summary_text = summary_text
        self._latest_presence = latest_presence
        self._latest_continuity = latest_continuity

    async def get_latest_summary(self, conversation_id: str) -> dict[str, Any]:
        assert conversation_id == "conv_phase79"
        return {"summary_text": self._summary_text}

    async def list_recent_messages(self, conversation_id: str, *, limit: int) -> list[dict[str, Any]]:
        assert conversation_id == "conv_phase79"
        return list(self._recent_messages[:limit])

    async def get_message(self, message_id: str) -> dict[str, Any]:
        assert message_id == "msg_user_phase79"
        return dict(self._user_message)

    async def get_latest_presence_state(self, conversation_id: str) -> dict[str, Any]:
        assert conversation_id == "conv_phase79"
        return dict(self._latest_presence)

    async def get_latest_continuity_snapshot(self, conversation_id: str) -> dict[str, Any]:
        assert conversation_id == "conv_phase79"
        return dict(self._latest_continuity)

    async def list_active_commitments(self, conversation_id: str) -> list[dict[str, Any]]:
        assert conversation_id == "conv_phase79"
        return [{"commitment_text": "下一步继续解释"}]


class _FakeMemberRepo:
    async def get_member(self, member_id: str) -> dict[str, Any]:
        assert member_id == "mem_xiaoyao"
        return {
            "member_id": member_id,
            "display_name": "小耀",
            "avatar_uri": None,
            "status": "online",
            "default_brain_id": "brain_phase79",
            "persona_profile_id": "persona_phase79",
        }


class _FakeBrainRepo:
    async def get_brain(self, brain_id: str) -> dict[str, Any]:
        assert brain_id == "brain_phase79"
        return {
            "brain_id": brain_id,
            "display_name": "Phase79 Brain",
            "provider": "local",
            "model_name": "gpt-phase79",
            "status": "active",
        }


class _FakeTraceService:
    async def start_span(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "span_phase79"

    async def end_span(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class _FakeMemoryService:
    def __init__(self, blocks: list[MemoryBlock]) -> None:
        self._blocks = blocks

    async def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []

    async def compress(self, *args: Any, **kwargs: Any) -> list[MemoryBlock]:
        del args, kwargs
        return list(self._blocks)


class _FakeAssetBroker:
    async def query(self, request: Any, **kwargs: Any) -> Any:
        del kwargs
        if request.asset_type == AssetCategory.KNOWLEDGE_BASE:
            handles = [
                _handle(
                    "hdl_kb",
                    AssetCategory.KNOWLEDGE_BASE,
                    "知识库只读句柄",
                    ["read_knowledge"],
                    [],
                )
            ]
        elif request.asset_type == AssetCategory.ACCOUNT:
            handles = [
                _handle(
                    "hdl_account",
                    AssetCategory.ACCOUNT,
                    "外部账号只读句柄",
                    ["read_external_account", "browser_read"],
                    ["send_message"],
                )
            ]
        elif request.asset_type == AssetCategory.HARDWARE:
            handles = [
                _handle(
                    "hdl_hw",
                    AssetCategory.HARDWARE,
                    "主机只读与终端只读句柄",
                    ["host_readonly", "terminal_readonly"],
                    [],
                )
            ]
        elif request.asset_type == AssetCategory.BRAIN:
            handles = [
                _handle(
                    "hdl_brain",
                    AssetCategory.BRAIN,
                    "任务委托能力摘要",
                    ["delegate_task"],
                    [],
                )
            ]
        else:
            handles = []
        return SimpleNamespace(handles=handles)


class _FakePersonaHeartService:
    async def persona_summary(self, *args: Any, **kwargs: Any) -> PersonaSummary:
        del args, kwargs
        return PersonaSummary(
            persona_profile_id="persona_phase79",
            summary="Calm, direct, warm.",
            mode="default",
            tone_hints=["direct"],
            disclosure_hints=["state_capability_boundaries"],
        )

    async def heart_summary(self, *args: Any, **kwargs: Any) -> HeartSummary:
        del args, kwargs
        return HeartSummary(
            member_id="mem_xiaoyao",
            summary="steady",
            mood="steady",
            urgency="normal",
            user_state="focused",
            preferred_pace="normal",
        )


class _FakeChatExperienceService:
    async def get_working_state(self, conversation_id: str) -> dict[str, Any]:
        assert conversation_id == "conv_phase79"
        return {
            "active_topic": "聊天主链路",
            "user_goal": "理解 context gateway",
            "pending_confirmation": {"questions": ["是否继续展开？"]},
            "candidate_actions": [],
        }


def _handle(
    handle_id: str,
    asset_type: AssetCategory,
    summary: str,
    allowed_actions: list[str],
    approval_required_actions: list[str],
) -> Any:
    return SimpleNamespace(
        handle_id=handle_id,
        asset_id=f"asset_{handle_id}",
        asset_type=asset_type,
        summary=summary,
        allowed_actions=list(allowed_actions),
        approval_required_actions=list(approval_required_actions),
        status="active",
        expires_at=None,
    )
