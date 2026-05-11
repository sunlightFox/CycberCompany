from __future__ import annotations

from app.schemas.chat_quality import ActionDialogueDecision, ActionDialogueFacts
from response_composer import canonical_action_status


class ActionDialogueMapperService:
    def map(self, facts: ActionDialogueFacts) -> ActionDialogueDecision:
        route_semantics = dict(facts.route_semantics or {})
        natural = dict(facts.natural_interaction or {})
        task_status = dict(facts.task_status or {})
        route = str(route_semantics.get("route") or "")
        related_capabilities = [route] if route else []

        natural_status = canonical_action_status(natural.get("status"), default="")
        if facts.approval_pending or natural_status == "waiting_for_approval":
            return ActionDialogueDecision(
                action_status="waiting_for_approval",
                narration_style="approval_waiting",
                natural_transition="ask_for_confirmation",
                should_explain_pending=True,
                should_claim_completion=False,
                blocked_by_approval=True,
                visible_failure_strategy="boundary_helpful",
                related_capabilities=related_capabilities,
                reason_codes=["approval_pending"],
            )
        status = canonical_action_status(task_status.get("status"), default="")
        if status in {"planned", "executing"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style="brief_progress",
                natural_transition="status_update",
                should_explain_pending=True,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="defer_with_anchor",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if status in {"completed_with_evidence", "partially_completed"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style=(
                    "tool_contextual"
                    if any(marker in route for marker in ["browser", "terminal", "skill", "mcp", "host"])
                    else "result_first"
                ),
                natural_transition="deliver_result",
                should_explain_pending=False,
                should_claim_completion=True,
                blocked_by_approval=False,
                visible_failure_strategy="partial_honest",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if status in {"failed_with_reason", "blocked_by_boundary", "cancelled"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style=(
                    "tool_contextual"
                    if any(marker in route for marker in ["browser", "terminal", "skill", "mcp", "host"])
                    else "partial_honest"
                ),
                natural_transition="repair_or_retry",
                should_explain_pending=False,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="retry_softly",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if any(marker in route for marker in ["browser", "terminal", "skill", "mcp"]):
            return ActionDialogueDecision(
                action_status="tool_context",
                narration_style="tool_contextual",
                natural_transition="answer_with_action_context",
                should_explain_pending=False,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="partial_honest",
                related_capabilities=related_capabilities,
                reason_codes=["route_capability_context"],
            )
        return ActionDialogueDecision(reason_codes=["no_action"])
