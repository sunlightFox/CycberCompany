from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brain.adapters import estimate_text_tokens
from core_types import (
    AssetCategory,
    BrainSummary,
    ContextDecision,
    ContextPacket,
    ConversationContext,
    ErrorCode,
    MemberSummary,
    PersonaSummary,
    ResourceHandleSummary,
    RiskLevel,
    SafetyNote,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id
from app.db.repositories.brain_repo import BrainRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.schemas.assets import AssetQueryRequest
from app.schemas.memory import MemorySearchApiRequest
from app.services.asset_broker import AssetBrokerService
from app.services.memory import MemoryService

if TYPE_CHECKING:
    from app.services.agent_workbench import AgentWorkbenchService
    from app.services.chat_experience import ChatExperienceService
    from app.services.design_alignment import PersonaHeartService


class RuntimeContextGateway:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        member_repo: MemberRepository,
        brain_repo: BrainRepository,
        trace_service: TraceService,
        memory_service: MemoryService,
        asset_broker_service: AssetBrokerService | None = None,
        persona_heart_service: PersonaHeartService | None = None,
        chat_experience_service: ChatExperienceService | None = None,
        agent_workbench_service: AgentWorkbenchService | None = None,
        recent_message_limit: int = 12,
        token_budget: int = 6000,
    ) -> None:
        self._chat_repo = chat_repo
        self._members = member_repo
        self._brains = brain_repo
        self._trace = trace_service
        self._memory = memory_service
        self._assets = asset_broker_service
        self._persona_heart = persona_heart_service
        self._chat_experience = chat_experience_service
        self._agent_workbench = agent_workbench_service
        self._recent_message_limit = recent_message_limit
        self._token_budget = token_budget

    async def build(
        self,
        *,
        turn: dict[str, Any],
        root_span_id: str | None,
        context_decision: ContextDecision | None = None,
    ) -> ContextPacket:
        member = await self._members.get_member(turn["member_id"])
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)

        include_summary = (
            context_decision is None or context_decision.include_session_summary
        )
        include_working_state = (
            context_decision is None or context_decision.include_conversation_state
        )
        include_recent = (
            context_decision is None or context_decision.include_recent_messages
        )
        include_memory = context_decision is None or context_decision.include_memory
        include_handles = (
            context_decision is not None and context_decision.include_asset_handles
        )
        include_persona = context_decision is None or context_decision.include_persona
        include_heart = context_decision is None or context_decision.include_heart

        summary = None
        if include_summary:
            summary_span = await self._trace.start_span(
                turn["trace_id"],
                span_type=TraceSpanType.CONTEXT_SUMMARY_READ,
                name="read conversation summary",
                parent_span_id=root_span_id,
            )
            summary = await self._chat_repo.get_latest_summary(turn["conversation_id"])
            await self._trace.end_span(summary_span, output_data={"summary_exists": bool(summary)})
        working_state = (
            await self._chat_experience.get_working_state(turn["conversation_id"])
            if self._chat_experience is not None and include_working_state
            else None
        )

        recent_messages = (
            await self._chat_repo.list_recent_messages(
                turn["conversation_id"],
                limit=self._recent_message_limit,
            )
            if include_recent
            else []
        )
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        session_id = _session_id_from_message(user_message)
        trimmed_messages = _same_session_messages(
            self._trim_messages(recent_messages),
            user_message,
        )
        query_text = str(redact(user_message.get("content_text") if user_message else ""))
        memory_blocks = []
        if include_memory:
            memory_search = await self._memory.search(
                MemorySearchApiRequest(
                    query=query_text or "current conversation",
                    member_id=turn["member_id"],
                    conversation_id=turn["conversation_id"],
                    intent=turn.get("intent"),
                    layers=_memory_layers(context_decision),
                    limit=_memory_limit(context_decision),
                ),
                trace_id=turn["trace_id"],
                turn_id=turn["turn_id"],
            )
            memory_blocks = await self._memory.compress(
                memory_search,
                token_budget=max(500, self._token_budget // 5),
                trace_id=turn["trace_id"],
            )
        resource_handles = (
            await self._resource_handles(
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                query_text=query_text,
                trace_id=turn["trace_id"],
            )
            if include_handles
            else []
        )
        brain = await self._brain_summary(member.get("default_brain_id"))
        persona = (
            await self._persona_summary(
                member,
                trace_id=turn["trace_id"],
                parent_span_id=root_span_id,
            )
            if include_persona
            else None
        )
        heart = (
            await self._persona_heart.heart_summary(
                turn["member_id"],
                text=query_text,
                source_turn_id=turn["turn_id"],
                trace_id=turn["trace_id"],
                parent_span_id=root_span_id,
            )
            if self._persona_heart is not None and include_heart
            else None
        )
        workbench = await self._workbench_context(
            member_id=turn["member_id"],
            conversation_id=turn["conversation_id"],
        )
        return ContextPacket(
            context_packet_id=new_id("ctx"),
            member=MemberSummary(
                member_id=member["member_id"],
                display_name=member["display_name"],
                avatar_uri=member["avatar_uri"],
                status=member["status"],
                default_brain_id=member["default_brain_id"],
            ),
            brain=brain,
            persona=persona,
            heart=heart,
            conversation=ConversationContext(
                conversation_id=turn["conversation_id"],
                recent_summary=_summary_with_working_state(
                    summary["summary_text"] if summary else None,
                    working_state,
                    session_id,
                ),
                last_messages=trimmed_messages,
            ),
            memories=memory_blocks,
            resource_handles=resource_handles,
            safety_notes=[SafetyNote(risk_level=RiskLevel.R1, summary="local safety active")],
            untrusted_context=[],
            workbench=workbench,
        )

    async def _workbench_context(
        self,
        *,
        member_id: str,
        conversation_id: str | None,
    ):
        if self._agent_workbench is None:
            return None
        try:
            return await self._agent_workbench.latest_workbench_context(
                member_id=member_id,
                conversation_id=conversation_id,
            )
        except Exception:
            return None

    async def _persona_summary(
        self,
        member: dict[str, Any],
        *,
        trace_id: str,
        parent_span_id: str | None,
    ) -> PersonaSummary:
        profile_id = member["persona_profile_id"]
        if self._persona_heart is None:
            return PersonaSummary(
                persona_profile_id=profile_id,
                summary="Calm, direct, warm, conclusion-first.",
                mode="default",
                tone_hints=["concise", "direct", "warm"],
                disclosure_hints=["state_capability_boundaries"],
            )
        return await self._persona_heart.persona_summary(
            profile_id,
            member_id=member["member_id"],
            trace_id=trace_id,
            parent_span_id=parent_span_id,
        )

    async def _brain_summary(self, brain_id: str | None) -> BrainSummary | None:
        if not brain_id:
            return None
        brain_row = await self._brains.get_brain(brain_id)
        if not brain_row:
            return None
        return BrainSummary(
            brain_id=brain_row["brain_id"],
            display_name=brain_row["display_name"],
            provider=brain_row["provider"],
            model_name=brain_row["model_name"],
            status=brain_row["status"],
        )

    async def _resource_handles(
        self,
        *,
        member_id: str,
        conversation_id: str,
        query_text: str,
        trace_id: str,
    ) -> list[ResourceHandleSummary]:
        if self._assets is None:
            return []
        try:
            response = await self._assets.query(
                AssetQueryRequest(
                    subject_type="member",
                    subject_id=member_id,
                    conversation_id=conversation_id,
                    asset_type=AssetCategory.KNOWLEDGE_BASE,
                    requested_actions=["read_knowledge"],
                    keywords=_keywords(query_text),
                ),
                trace_id=trace_id,
                raise_on_denied=False,
            )
        except AppError as exc:
            if exc.code == ErrorCode.ASSET_ACCESS_DENIED.value:
                return []
            raise
        return [
            ResourceHandleSummary(
                handle_id=handle.handle_id,
                asset_id=handle.asset_id,
                asset_type=handle.asset_type.value,
                summary=handle.summary,
                allowed_actions=handle.allowed_actions,
                approval_required_actions=handle.approval_required_actions,
            )
            for handle in response.handles
        ]

    def _trim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        token_total = 0
        for item in reversed(messages):
            raw_text = str(item.get("content_text") or "")
            redacted_text = str(redact(raw_text))
            token_total += estimate_text_tokens(redacted_text)
            if token_total > self._token_budget and selected:
                break
            selected.append(
                {
                    "author_type": item["author_type"],
                    "content_text": redacted_text,
                    "model_safe_content_text": redacted_text,
                    "redaction_summary": {
                        "applied": redacted_text != raw_text,
                        "raw_chars": len(raw_text),
                        "model_safe_chars": len(redacted_text),
                    },
                    "created_at": item["created_at"],
                    "session_id": (
                        item.get("content", {}).get("session_id")
                        if isinstance(item.get("content"), dict)
                        else None
                    ),
                }
            )
        return list(reversed(selected))


def _keywords(text: str) -> list[str]:
    return [part for part in text.replace("：", " ").replace(":", " ").split()[:8] if part]


def _same_session_messages(
    messages: list[dict[str, Any]],
    user_message: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    session_id = _session_id_from_message(user_message)
    if not session_id:
        return messages
    filtered = [
        message
        for message in messages
        if message.get("session_id") in {None, session_id}
    ]
    return filtered or messages[-2:]


def _session_id_from_message(message: dict[str, Any] | None) -> str | None:
    content = message.get("content") if message else {}
    value = content.get("session_id") if isinstance(content, dict) else None
    return str(value) if value else None


def _memory_limit(context_decision: ContextDecision | None) -> int:
    if context_decision is None:
        return 8
    configured = context_decision.memory_query.get("max_items")
    if configured is None:
        return 8
    try:
        return max(0, min(int(configured), 12))
    except (TypeError, ValueError):
        return 8


def _memory_layers(context_decision: ContextDecision | None) -> list[Any]:
    if context_decision is None:
        return []
    configured = context_decision.memory_query.get("layers")
    if not isinstance(configured, list):
        return []
    return [item for item in configured if isinstance(item, str)]


def _summary_with_working_state(
    summary_text: str | None,
    working_state: dict[str, Any] | None,
    session_id: str | None = None,
) -> str | None:
    if not working_state:
        return summary_text
    state_session_id = working_state.get("session_id")
    if session_id and state_session_id and str(state_session_id) != str(session_id):
        return summary_text
    lines = []
    if summary_text:
        lines.append(summary_text)
    state_bits = []
    if working_state.get("active_topic"):
        state_bits.append(f"主题={working_state['active_topic']}")
    if working_state.get("user_goal"):
        state_bits.append(f"目标={working_state['user_goal']}")
    decisions = working_state.get("decisions_made") or []
    if decisions:
        state_bits.append(f"已定结论={'; '.join(str(item) for item in decisions[-3:])}")
    constraints = working_state.get("known_constraints") or []
    if constraints:
        state_bits.append(f"约束={'; '.join(str(item) for item in constraints[-3:])}")
    if state_bits:
        lines.append("当前对话工作状态：" + "；".join(state_bits))
    return "\n".join(lines) if lines else None
