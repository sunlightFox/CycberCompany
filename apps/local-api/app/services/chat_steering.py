from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


INTERRUPT_MARKERS = ("停", "停止", "暂停", "先别", "别做了", "取消", "不用了")
STEER_MARKERS = ("改成", "改为", "换成", "重来", "按这个", "改口")
FOLLOWUP_MARKERS = ("补充", "另外", "还有", "再加", "顺便", "对了", "补一句")
RESUME_MARKERS = ("继续", "接着", "恢复", "继续刚才", "接着做")


@dataclass(slots=True)
class SteeringDecision:
    queue_policy: str = "immediate"
    detected: bool = False
    control_intent: str | None = None
    resolution_policy: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    target_turn_id: str | None = None
    target_task_id: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def metadata(self, *, source_channel_semantics: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "control_intent": self.control_intent,
            "target_turn_id": self.target_turn_id,
            "target_task_id": self.target_task_id,
            "resolution_policy": self.resolution_policy,
            "reason_codes": list(self.reason_codes),
            "source_channel_semantics": dict(source_channel_semantics or {}),
            "diagnostics": dict(self.diagnostics),
        }


class ChatSteeringCoordinator:
    def decide(
        self,
        *,
        user_text: str,
        queue_policy: str,
        active_turn: dict[str, Any] | None,
        working_state: dict[str, Any] | None,
        explicit_steering: dict[str, Any] | None = None,
    ) -> SteeringDecision:
        text = str(user_text or "").strip()
        state = dict(working_state or {})
        steering = dict(explicit_steering or {})
        active_turn_id = active_turn.get("turn_id") if active_turn else None
        if not text:
            return SteeringDecision(queue_policy=queue_policy)

        if queue_policy in {"followup", "steer", "interrupt"}:
            return self._decision_from_policy(
                queue_policy=queue_policy,
                active_turn_id=active_turn_id,
                state=state,
                steering=steering,
                text=text,
            )

        normalized = text.lower()
        if _matches_any(normalized, INTERRUPT_MARKERS):
            if _matches_any(normalized, STEER_MARKERS):
                return SteeringDecision(
                    queue_policy="steer",
                    detected=bool(active_turn_id),
                    control_intent="steer_replace",
                    resolution_policy="cancel_and_supersede" if active_turn_id else "queue_after_current",
                    reason_codes=["explicit_interrupt", "explicit_steer"],
                    target_turn_id=active_turn_id,
                    diagnostics={"active_run_found": bool(active_turn_id)},
                )
            return SteeringDecision(
                queue_policy="interrupt",
                detected=bool(active_turn_id),
                control_intent="pause_current" if "暂停" in normalized or "先别" in normalized else "cancel_current",
                resolution_policy="pause_then_resume" if active_turn_id else "queue_after_current",
                reason_codes=["explicit_interrupt"],
                target_turn_id=active_turn_id,
                diagnostics={"active_run_found": bool(active_turn_id)},
            )

        if _matches_any(normalized, STEER_MARKERS):
            return SteeringDecision(
                queue_policy="steer",
                detected=bool(active_turn_id),
                control_intent="steer_replace",
                resolution_policy="cancel_and_supersede" if active_turn_id else "queue_after_current",
                reason_codes=["explicit_steer"],
                target_turn_id=active_turn_id,
                diagnostics={"active_run_found": bool(active_turn_id)},
            )

        if _matches_any(normalized, RESUME_MARKERS) and dict(state.get("pending_execution_resume") or {}):
            pending = dict(state.get("pending_execution_resume") or {})
            payload = dict(pending.get("payload") or {})
            return SteeringDecision(
                queue_policy="followup",
                detected=True,
                control_intent="resume",
                resolution_policy="queue_after_current",
                reason_codes=["resume_pending_execution"],
                target_turn_id=str(pending.get("source_turn_id") or "") or None,
                target_task_id=str(payload.get("target_task_id") or "") or None,
                diagnostics={"pending_execution_resume": True},
            )

        if active_turn_id and (_matches_any(normalized, FOLLOWUP_MARKERS) or _short_followup(text)):
            return SteeringDecision(
                queue_policy="followup",
                detected=True,
                control_intent="followup_append",
                resolution_policy="merge_current",
                reason_codes=["active_run_followup"],
                target_turn_id=active_turn_id,
                diagnostics={"active_run_found": True, "short_followup": _short_followup(text)},
            )

        return SteeringDecision(queue_policy=queue_policy)

    def _decision_from_policy(
        self,
        *,
        queue_policy: str,
        active_turn_id: str | None,
        state: dict[str, Any],
        steering: dict[str, Any],
        text: str,
    ) -> SteeringDecision:
        target_turn_id = (
            steering.get("target_turn_id")
            or active_turn_id
            or dict(state.get("pending_execution_resume") or {}).get("source_turn_id")
        )
        target_task_id = steering.get("target_task_id")
        source_intent = steering.get("control_intent")
        if queue_policy == "followup":
            return SteeringDecision(
                queue_policy=queue_policy,
                detected=bool(target_turn_id),
                control_intent=str(source_intent or "followup_append"),
                resolution_policy="merge_current" if active_turn_id else "queue_after_current",
                reason_codes=["channel_followup_policy"],
                target_turn_id=target_turn_id,
                target_task_id=target_task_id,
                diagnostics={"active_run_found": bool(active_turn_id), "text_chars": len(text)},
            )
        if queue_policy == "steer":
            return SteeringDecision(
                queue_policy=queue_policy,
                detected=bool(target_turn_id),
                control_intent=str(source_intent or "steer_replace"),
                resolution_policy="cancel_and_supersede" if active_turn_id else "queue_after_current",
                reason_codes=["channel_steer_policy"],
                target_turn_id=target_turn_id,
                target_task_id=target_task_id,
                diagnostics={"active_run_found": bool(active_turn_id)},
            )
        return SteeringDecision(
            queue_policy=queue_policy,
            detected=bool(target_turn_id),
            control_intent=str(source_intent or "pause_current"),
            resolution_policy="pause_then_resume" if active_turn_id else "queue_after_current",
            reason_codes=["channel_interrupt_policy"],
            target_turn_id=target_turn_id,
            target_task_id=target_task_id,
            diagnostics={"active_run_found": bool(active_turn_id)},
        )


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _short_followup(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact or len(compact) > 28:
        return False
    return any(
        marker in compact
        for marker in ("然后", "另外", "还有", "顺便", "补充", "再加", "改下", "再补")
    )
