from __future__ import annotations

from typing import Any


class ChannelApprovalBridge:
    def render_pending_action(
        self,
        *,
        response_plan: dict[str, Any] | None = None,
        task_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = dict(response_plan or {})
        approval_prompt = dict(plan.get("approval_prompt") or {})
        return {
            "status": str((task_status or {}).get("status") or approval_prompt.get("status") or "pending_action"),
            "plain_text": str(plan.get("plain_text") or plan.get("summary") or ""),
            "action_buttons": list(plan.get("action_buttons") or []),
            "approval_prompt": approval_prompt,
        }

    def render_approval_state(self, *, approval: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": approval.get("approval_id"),
            "status": approval.get("status"),
            "requested_action": approval.get("requested_action"),
            "risk_level": approval.get("risk_level"),
        }
