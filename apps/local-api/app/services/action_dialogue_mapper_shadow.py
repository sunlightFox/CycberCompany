from __future__ import annotations

from typing import Any

from app.schemas.chat_quality_shadow import ActionDialogueMappingShadow
from response_composer import canonical_action_status


class ActionDialogueMapperShadowService:
    def map(
        self,
        *,
        response_plan: dict[str, Any],
        understanding: Any,
    ) -> ActionDialogueMappingShadow:
        structured = response_plan if isinstance(response_plan, dict) else {}
        route_semantics = structured.get("route_semantics")
        natural = structured.get("natural_interaction")
        task_status = structured.get("task_status_semantics") or structured.get("task_status")

        related_capabilities: list[str] = []
        dimensions: list[str] = []
        risk_notes: list[str] = []

        if isinstance(route_semantics, dict):
            route = str(route_semantics.get("route") or "")
            if route:
                related_capabilities.append(route)
            if "browser" in route:
                dimensions.append("browser_task_continuity")
            if "terminal" in route or "host" in route or "system" in route:
                dimensions.append("system_command_honesty")
        if getattr(understanding, "action_request", False):
            dimensions.append("tool_call_narration")
        if any(
            marker in " ".join(related_capabilities).lower()
            for marker in ["skill", "mcp", "plugin"]
        ):
            dimensions.append("skill_mcp_transition_naturalness")

        natural_status = ""
        if isinstance(natural, dict):
            raw_natural_status = str(natural.get("status") or "")
            natural_status = (
                "pending_action"
                if raw_natural_status == "pending_action"
                else canonical_action_status(raw_natural_status, default="")
            )
        if natural_status in {"waiting_for_approval", "pending_action"}:
            return ActionDialogueMappingShadow(
                action_status=natural_status,
                narration_style="approval_waiting",
                should_explain_pending=True,
                should_claim_completion=False,
                natural_transition="ask_for_confirmation",
                blocked_by_approval=True,
                related_capabilities=related_capabilities,
                quality_dimensions=sorted(set(dimensions + ["anti_false_completion"])),
                risk_notes=["pending_action_requires_honest_waiting_language"],
            )

        if isinstance(task_status, dict):
            status = canonical_action_status(task_status.get("status"), default="")
            if status == "waiting_for_approval":
                return ActionDialogueMappingShadow(
                    action_status=status,
                    narration_style="approval_waiting",
                    should_explain_pending=True,
                    should_claim_completion=False,
                    natural_transition="ask_for_confirmation",
                    blocked_by_approval=True,
                    related_capabilities=related_capabilities,
                    quality_dimensions=sorted(set(dimensions + ["anti_false_completion"])),
                    risk_notes=["task_waiting_approval_should_not_sound_done"],
                )
            if status in {"executing", "planned", "paused"}:
                return ActionDialogueMappingShadow(
                    action_status=status,
                    narration_style="brief_progress",
                    should_explain_pending=True,
                    should_claim_completion=False,
                    natural_transition="status_update",
                    blocked_by_approval=False,
                    related_capabilities=related_capabilities,
                    quality_dimensions=sorted(set(dimensions)),
                    risk_notes=risk_notes,
                )
            if status in {"completed_with_evidence", "partially_completed"}:
                return ActionDialogueMappingShadow(
                    action_status=status,
                    narration_style="result_first",
                    should_explain_pending=False,
                    should_claim_completion=True,
                    natural_transition="deliver_result",
                    blocked_by_approval=False,
                    related_capabilities=related_capabilities,
                    quality_dimensions=sorted(set(dimensions)),
                    risk_notes=risk_notes,
                )

        return ActionDialogueMappingShadow(
            action_status="no_action",
            narration_style="answer_directly",
            should_explain_pending=False,
            should_claim_completion=False,
            natural_transition="none",
            blocked_by_approval=False,
            related_capabilities=related_capabilities,
            quality_dimensions=sorted(set(dimensions)),
            risk_notes=risk_notes,
        )
