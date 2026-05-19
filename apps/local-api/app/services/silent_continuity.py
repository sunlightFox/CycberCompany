from __future__ import annotations

from typing import Any

from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.schemas.context_runtime import SilentContinuityRecord
from app.services.chat_continuity_kernel import (
    build_action_ledger_entry,
    build_evidence_ledger_entries,
)


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
        active_profile = await self._chat_repo.get_active_user_profile(turn["conversation_id"])
        merged_profile = {
            **dict((active_profile or {}).get("profile_data") or {}),
            **profile_updates,
        }
        topic_anchor = _topic_anchor(user_text, presence_payload)
        structured = dict(response_plan.get("structured_payload") or {})
        action_ledger = build_action_ledger_entry(
            turn=turn,
            response_plan=response_plan,
            assistant_text=assistant_text,
        )
        evidence_ledger = build_evidence_ledger_entries(
            action_ledger=action_ledger,
            evidence_gate=dict(
                structured.get("evidence_gate")
                or dict(structured.get("natural_interaction") or {}).get("evidence_gate")
                or {}
            ),
            response_plan=response_plan,
        )
        record = SilentContinuityRecord(
            continuity_summary=_continuity_summary(
                user_text,
                assistant_text,
                topic_anchor,
                status,
            ),
            user_state_hint=_user_state_hint(user_text, presence_payload),
            assistant_commitments=[],
            followup_candidates=_followup_candidates(
                user_text=user_text,
                assistant_text=assistant_text,
                action_ledger=action_ledger,
                evidence_ledger=evidence_ledger,
            ),
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
                    "profile_data": merged_profile,
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
    explicit_preference = any(
        marker in text for marker in ["记住", "以后都", "我的偏好", "回复偏好", "结构偏好", "总结偏好"]
    )
    preference_correction = any(marker in text for marker in ["修正一下", "改成", "换成", "接下来的总结"])
    structure_markers = ["标题", "一级标题", "二级标题", "表格", "两段", "一段", "段落", "不要表格"]
    if not explicit_preference and preference_correction and any(marker in text for marker in structure_markers):
        explicit_preference = True
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
    structure_preference = _summary_structure_preference(text)
    if structure_preference:
        updates["summary_structure_preference"] = structure_preference
    avoidances: list[str] = []
    if "不要模板" in text or "模板腔" in text:
        avoidances.append("template_tone")
    if "不要表情" in text:
        avoidances.append("emoji")
    if avoidances:
        updates["style_avoidances"] = avoidances
    return updates


def _summary_structure_preference(text: str) -> str | None:
    raw = str(text)
    if "不要表格" in raw and "标题" in raw and "两段" in raw:
        return "标题 + 两段段落"
    if "先标题" in raw and "表格" in raw and ("最后一段结论" in raw or "最后一段总结" in raw):
        return "先标题，再表格，最后一段结论"
    if "标题" in raw and "表格" in raw and "结论段落" in raw:
        return "标题 + 表格 + 结论段落"
    return None


def _topic_anchor(user_text: str, presence_payload: dict[str, Any]) -> str | None:
    conversation_state = dict(
        presence_payload.get("presence_state", {}).get("conversation_state", {})
    )
    return str(conversation_state.get("active_topic") or user_text[:48]).strip() or None


def _continuity_summary(
    user_text: str,
    assistant_text: str,
    topic_anchor: str | None,
    status: str,
) -> str:
    return (
        f"主题={topic_anchor or '当前对话'}；用户={str(redact(user_text))[:120]}；"
        f"回复={str(redact(assistant_text))[:160]}；状态={status}"
    )


def _user_state_hint(user_text: str, presence_payload: dict[str, Any]) -> str | None:
    relationship_state = dict(
        presence_payload.get("presence_state", {}).get("relationship_state", {})
    )
    pressure = str(relationship_state.get("user_pressure") or "")
    if pressure and pressure != "steady":
        return pressure
    if any(marker in user_text for marker in ["急", "赶", "焦虑"]):
        return "urgent"
    return None


def _followup_candidates(
    *,
    user_text: str,
    assistant_text: str,
    action_ledger: dict[str, Any] | None,
    evidence_ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not action_ledger:
        return []
    prompts = ["recent_result", "current_status", "missing_evidence"]
    if list(action_ledger.get("artifact_refs") or []):
        prompts.append("generated_artifact")
    if str(action_ledger.get("route_type") or "") == "browser_read_page":
        prompts.append("page_title")
    return [
        {
            "kind": "action_continuity",
            "prompt": prompt,
            "reply_preview": str(redact(assistant_text))[:120],
            "source_text": str(redact(user_text))[:120],
            "action_ledger": dict(action_ledger),
            "evidence_ledger": [
                dict(item) for item in evidence_ledger if isinstance(item, dict)
            ],
        }
        for prompt in prompts
    ][:6]
