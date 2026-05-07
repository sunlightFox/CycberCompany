from __future__ import annotations

from typing import Any

from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.schemas.context_runtime import SilentContinuityRecord


class SilentContinuityService:
    def __init__(self, *, chat_repo: ChatRepository) -> None:
        self._chat_repo = chat_repo

    async def capture_turn(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        assistant_text: str,
        presence_payload: dict[str, Any],
        response_plan: dict[str, Any],
        status: str,
    ) -> SilentContinuityRecord:
        profile_updates = _profile_updates_from_turn(user_text)
        commitments: list[str] = []
        topic_anchor = _topic_anchor(user_text, presence_payload)
        record = SilentContinuityRecord(
            continuity_summary=_continuity_summary(user_text, assistant_text, topic_anchor, status),
            user_state_hint=_user_state_hint(user_text, presence_payload),
            assistant_commitments=commitments,
            followup_candidates=[],
            topic_anchor=topic_anchor,
            expiry_policy={"type": "session", "ttl_hours": 24},
            source_turn_id=str(turn["turn_id"]),
            trace_id=turn.get("trace_id"),
            profile_updates=profile_updates,
        )
        now = utc_now_iso()
        await self._chat_repo.insert_continuity_snapshot(
            {
                "snapshot_id": new_id("cont"),
                "conversation_id": turn["conversation_id"],
                "source_turn_id": turn["turn_id"],
                "summary_text": record.continuity_summary,
                "user_state_hint": record.user_state_hint,
                "assistant_commitments": record.assistant_commitments,
                "followup_candidates": record.followup_candidates,
                "topic_anchor": record.topic_anchor,
                "expiry_policy": record.expiry_policy,
                "trace_id": record.trace_id,
                "status": status,
                "created_at": now,
                "updated_at": now,
            }
        )
        if profile_updates:
            await self._chat_repo.upsert_user_profile(
                {
                    "profile_id": new_id("cup"),
                    "conversation_id": turn["conversation_id"],
                    "member_id": turn["member_id"],
                    "profile_type": "ephemeral_preference",
                    "profile_data": profile_updates,
                    "source_turn_id": turn["turn_id"],
                    "trace_id": turn.get("trace_id"),
                    "status": "active",
                    "expires_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        return record

    async def capture_compaction(
        self,
        *,
        turn: dict[str, Any],
        summary_text: str,
    ) -> None:
        now = utc_now_iso()
        await self._chat_repo.insert_continuity_snapshot(
            {
                "snapshot_id": new_id("cont"),
                "conversation_id": turn["conversation_id"],
                "source_turn_id": turn["turn_id"],
                "summary_text": str(redact(summary_text))[:1200],
                "user_state_hint": "compaction_recovery",
                "assistant_commitments": [],
                "followup_candidates": [],
                "topic_anchor": None,
                "expiry_policy": {"type": "compaction_recovery", "ttl_hours": 24},
                "trace_id": turn.get("trace_id"),
                "status": "compaction",
                "created_at": now,
                "updated_at": now,
            }
        )


def _profile_updates_from_turn(user_text: str) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    text = str(user_text)
    explicit_preference = any(marker in text for marker in ["记住", "以后都", "我的偏好", "回复偏好"])
    if not explicit_preference:
        return updates
    if "先给结论" in text and "风险" in text:
        updates["reply_preference"] = "conclusion_then_risk"
    if "先看风险" in text and "结论" in text:
        updates["reply_preference"] = "risk_then_conclusion"
    if any(marker in text for marker in ["别铺太多背景", "简洁", "三行内"]):
        updates["explanation_density"] = "short"
    if any(marker in text for marker in ["详细", "展开", "深入"]):
        updates["explanation_density"] = "expanded"
    avoidances: list[str] = []
    if "不要模板" in text or "模板腔" in text:
        avoidances.append("template_tone")
    if "不要表情" in text:
        avoidances.append("emoji")
    if avoidances:
        updates["style_avoidances"] = avoidances
    return updates
def _topic_anchor(user_text: str, presence_payload: dict[str, Any]) -> str | None:
    conversation_state = dict(presence_payload.get("presence_state", {}).get("conversation_state", {}))
    return str(conversation_state.get("active_topic") or user_text[:48]).strip() or None


def _continuity_summary(user_text: str, assistant_text: str, topic_anchor: str | None, status: str) -> str:
    return f"主题={topic_anchor or '当前对话'}；用户={str(redact(user_text))[:120]}；回复={str(redact(assistant_text))[:160]}；状态={status}"


def _user_state_hint(user_text: str, presence_payload: dict[str, Any]) -> str | None:
    relationship_state = dict(presence_payload.get("presence_state", {}).get("relationship_state", {}))
    pressure = str(relationship_state.get("user_pressure") or "")
    if pressure and pressure != "steady":
        return pressure
    if any(marker in user_text for marker in ["急", "赶", "焦虑"]):
        return "urgent"
    return None
