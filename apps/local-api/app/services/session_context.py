from __future__ import annotations

from typing import Any

from app.schemas.chat_quality import PresenceState
from app.schemas.context_runtime import SessionContext


class SessionContextCuratorService:
    def curate(
        self,
        *,
        presence_state: PresenceState,
        user_profile: dict[str, Any],
        latest_continuity: dict[str, Any],
        recent_messages: list[dict[str, Any]],
        memory_candidates: list[dict[str, Any]],
    ) -> SessionContext:
        identity = presence_state.identity_state
        relationship = presence_state.relationship_state
        conversation = presence_state.conversation_state
        action_state = presence_state.action_state
        override = bool(conversation.get("latest_instruction_override"))
        summary = str(
            conversation.get("user_goal") or conversation.get("active_topic")
            if override
            else (latest_continuity.get("continuity_summary") or conversation.get("active_topic") or "")
        )
        canonical_memory_items = _canonical_memory_items(memory_candidates, latest_instruction_override=override)
        return SessionContext(
            stable_identity_block=(
                f"{identity.get('display_name','助手')}不是现实真人，不虚构隐藏账号，不把未执行动作说成完成。"
            ),
            stable_user_profile_block=_user_profile_block(
                user_profile,
                memory_candidates=canonical_memory_items,
            ),
            current_conversation_summary=summary,
            current_open_loops=_open_loops(conversation, action_state, latest_continuity),
            current_commitments=[] if override else list(latest_continuity.get("assistant_commitments") or []),
            relevant_recent_messages=list(recent_messages[-4:]),
            relevant_memory_items=canonical_memory_items,
            current_action_facts={
                "pending_approval": bool(action_state.get("pending_approval")),
                "running_task": bool(action_state.get("running_task")),
                "interaction_posture": presence_state.interaction_posture,
                "user_pressure": relationship.get("user_pressure"),
                "continuity_mode": conversation.get("continuity_mode"),
                "latest_topic_anchor": conversation.get("active_topic"),
            },
            compaction_recovery_summary=summary,
            latest_instruction_override=override,
            reason_codes=_session_reason_codes(
                override=override,
                action_state=action_state,
                latest_continuity=latest_continuity,
            ),
        )


def _user_profile_block(
    user_profile: dict[str, Any],
    *,
    memory_candidates: list[dict[str, Any]],
) -> str:
    preference_memories = [
        item for item in memory_candidates
        if str(item.get("memory_class") or "") == "preference"
        and str(item.get("durability") or "") == "durable"
        and str(item.get("freshness_state") or "") == "fresh"
    ]
    if preference_memories:
        parts = [f"稳定偏好：{item.get('summary_text')}" for item in preference_memories[:2]]
        return "；".join(parts)
    if not user_profile:
        return "当前没有稳定用户画像，优先服从这轮显式要求。"
    parts: list[str] = []
    if user_profile.get("reply_preference"):
        parts.append(f"回复顺序偏好：{user_profile['reply_preference']}")
    if user_profile.get("explanation_density"):
        parts.append(f"解释密度：{user_profile['explanation_density']}")
    if user_profile.get("interaction_preference"):
        parts.append(f"互动偏好：{user_profile['interaction_preference']}")
    if user_profile.get("summary_structure_preference"):
        parts.append(f"总结结构偏好：{user_profile['summary_structure_preference']}")
    if user_profile.get("style_avoidances"):
        parts.append(f"避免风格：{'、'.join(user_profile['style_avoidances'])}")
    return "；".join(parts) if parts else "当前没有稳定用户画像，优先服从这轮显式要求。"


def _canonical_memory_items(
    memory_candidates: list[dict[str, Any]],
    *,
    latest_instruction_override: bool,
) -> list[dict[str, Any]]:
    def _priority(item: dict[str, Any]) -> tuple[int, float, float]:
        freshness = str(item.get("freshness_state") or "fresh")
        freshness_rank = {"fresh": 0, "aging": 1, "stale": 2, "superseded": 3, "expired": 4}.get(
            freshness,
            5,
        )
        return (
            freshness_rank,
            -float(item.get("evidence_strength", 0.0) or 0.0),
            -float(item.get("selection_confidence", 0.0) or 0.0),
        )

    filtered = [
        dict(item)
        for item in memory_candidates
        if str(item.get("memory_class") or "") in {"preference", "fact", "experience"}
        and str(item.get("freshness_state") or "fresh") not in {"superseded", "expired"}
    ]
    if latest_instruction_override:
        filtered = [
            item for item in filtered
            if str(item.get("memory_class") or "") != "preference"
            or not bool(item.get("cross_session"))
        ]
    filtered.sort(key=_priority)
    return filtered[:4]


def _open_loops(
    conversation: dict[str, Any],
    action_state: dict[str, Any],
    latest_continuity: dict[str, Any],
) -> list[str]:
    loops: list[str] = []
    if conversation.get("latest_instruction_override"):
        loops.append("latest_instruction_overrides_previous_goal")
        return loops
    if action_state.get("pending_approval"):
        loops.append("pending_approval_not_completed")
    loops.extend(str(item) for item in latest_continuity.get("followup_candidates") or [])
    return list(dict.fromkeys(loops))


def _session_reason_codes(
    *,
    override: bool,
    action_state: dict[str, Any],
    latest_continuity: dict[str, Any],
) -> list[str]:
    reason_codes = ["session_context_runtime"]
    if override:
        reason_codes.append("latest_instruction_override")
    if action_state.get("pending_approval"):
        reason_codes.append("pending_approval_present")
    if action_state.get("running_task"):
        reason_codes.append("running_task_present")
    if latest_continuity.get("followup_candidates"):
        reason_codes.append("continuity_followup_present")
    return reason_codes
