from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core_types import ErrorCode, RiskLevel, ToolDefinition

from app.core.errors import AppError
from app.core.time import utc_now_iso
from app.services.checkpoints import rollback_availability_for_tool
from app.services.safety_policy import classify_action_category

if TYPE_CHECKING:
    from app.schemas.tasks import ToolExecuteRequest


@dataclass(frozen=True)
class ToolSafetyEnvelope:
    risk_level: RiskLevel
    organization_id: str
    boundary_decision: Any | None
    safety_decision: Any
    policy_snapshot: dict[str, Any]
    terminal_command_policy: dict[str, Any] | None


class ToolSafetyBridge:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def handle_unknown_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        task_id: str | None,
        member_id: str,
        trace_id: str | None,
    ) -> None:
        if self._runtime._boundary is not None:
            await self._runtime._boundary.decide_tool_action(
                organization_id="org_default",
                tool_name=tool_name,
                source="unknown",
                requested_risk_level=RiskLevel.R7,
                args=args,
                task_id=task_id,
                member_id=member_id,
                tool_call_id=None,
                trace_id=trace_id,
            )
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "未知工具默认拒绝执行",
            status_code=403,
        )

    async def evaluate(
        self,
        *,
        request: Any,
        tool: ToolDefinition,
        tool_call_id: str,
        trace_id: str | None,
        handle_ids: list[str],
        risk_level: RiskLevel,
        organization_id: str,
        terminal_command_policy: dict[str, Any] | None,
    ) -> ToolSafetyEnvelope:
        from app.services.tools import _max_risk, _safety_request_for_tool, _should_defer_to_safety

        boundary_decision = None
        if self._runtime._boundary is not None:
            boundary_decision = await self._runtime._boundary.decide_tool_action(
                organization_id=organization_id,
                tool_name=tool.tool_name,
                source=tool.source,
                requested_risk_level=risk_level,
                args=request.args,
                task_id=request.task_id,
                member_id=request.member_id,
                tool_call_id=tool_call_id,
                trace_id=trace_id,
            )
            risk_level = _max_risk(risk_level, boundary_decision.effective_risk_level)
            boundary_snapshot = {
                **boundary_decision.policy_snapshot,
                "boundary_decision_id": boundary_decision.decision_id,
                "boundary_decision": boundary_decision.decision,
                "boundary_reason_codes": boundary_decision.reason_codes,
                "required_controls": boundary_decision.required_controls,
            }
            await self._runtime._repo.update_tool_call(
                tool_call_id,
                {
                    "policy_snapshot": boundary_snapshot,
                    "risk_level": risk_level.value,
                    "updated_at": utc_now_iso(),
                },
            )
            if boundary_decision.decision == "deny" and not _should_defer_to_safety(
                tool.tool_name,
                boundary_decision.reason_codes,
            ):
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "执行边界策略拒绝该工具动作",
                    status_code=403,
                    details={
                        "decision_id": boundary_decision.decision_id,
                        "reason_codes": boundary_decision.reason_codes,
                    },
                )

        safety_decision = await self._runtime._safety_decisions.evaluate(
            _safety_request_for_tool(
                request=request,
                tool=tool,
                risk_level=risk_level,
                organization_id=organization_id,
                handle_ids=handle_ids,
            ),
            trace_id=trace_id,
        )
        risk_level = _max_risk(risk_level, safety_decision.risk_level)
        policy_snapshot = {
            **(
                {
                    **boundary_decision.policy_snapshot,
                    "boundary_decision_id": boundary_decision.decision_id,
                    "boundary_decision": boundary_decision.decision,
                    "boundary_reason_codes": boundary_decision.reason_codes,
                }
                if boundary_decision is not None
                else {}
            ),
            "risk_level": risk_level.value,
            "required_controls": safety_decision.required_controls,
            "policy_sources": safety_decision.policy_sources,
            "decision": safety_decision.decision,
            "rollback_availability": rollback_availability_for_tool(
                tool.tool_name,
                request.args,
            ),
        }
        safety_decision_payload = self._runtime._redact_payload(
            safety_decision.model_dump(mode="json")
        )
        await self._runtime._repo.update_tool_call(
            tool_call_id,
            {
                "safety_decision_id": safety_decision.safety_decision_id,
                "safety_decision": (
                    safety_decision_payload if isinstance(safety_decision_payload, dict) else {}
                ),
                "policy_snapshot": policy_snapshot,
                "risk_level": risk_level.value,
                "updated_at": utc_now_iso(),
            },
        )
        if not safety_decision.allowed:
            raise AppError(
                ErrorCode.SAFETY_BLOCKED,
                "安全策略阻断了该工具动作",
                status_code=403,
                details={
                    "safety_decision_id": safety_decision.safety_decision_id,
                    "reason": safety_decision.reason,
                },
            )
        return ToolSafetyEnvelope(
            risk_level=risk_level,
            organization_id=organization_id,
            boundary_decision=boundary_decision,
            safety_decision=safety_decision,
            policy_snapshot=policy_snapshot,
            terminal_command_policy=terminal_command_policy,
        )

    async def approval_if_required(
        self,
        *,
        request: ToolExecuteRequest,
        tool: ToolDefinition,
        tool_call_id: str,
        organization_id: str,
        risk_level: RiskLevel,
        terminal_command_policy: dict[str, Any] | None,
        trace_id: str | None,
    ) -> Any | None:
        if request.approval_id:
            approval = await self._runtime._approvals.get(request.approval_id)
            if approval.status in {"approved", "edited"}:
                if approval.edited_payload:
                    from app.services.tools import _normalize_approval_args

                    request.args.update(_normalize_approval_args(approval.edited_payload))
                return None
            if approval.status == "denied":
                raise AppError(ErrorCode.APPROVAL_DENIED, "审批已拒绝", status_code=409)
            raise AppError(ErrorCode.TOOL_APPROVAL_REQUIRED, "工具动作需要审批", status_code=409)
        from app.services.tools import _risk_order

        if _risk_order(risk_level) < _risk_order(RiskLevel.R3):
            return None
        if self._runtime._safety_policy is not None:
            policy = await self._runtime._safety_policy.get_policy(
                organization_id=organization_id
            )
            if policy.should_skip_approval(
                action=tool.tool_name,
                risk_level=risk_level,
                action_category=classify_action_category(
                    action=tool.tool_name,
                    tool_name=tool.tool_name,
                    destination=str(
                        request.args.get("destination")
                        or request.args.get("url")
                        or request.args.get("path")
                        or request.args.get("command")
                        or ""
                    ),
                ),
                payload=request.args,
                terminal_command_policy=terminal_command_policy or {},
            ):
                return None
        task_id = request.task_id
        if task_id is None:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "高风险工具必须绑定任务并创建审批",
                status_code=409,
            )
        rollback = rollback_availability_for_tool(tool.tool_name, request.args)
        summary_suffix = (
            "；本地 checkpoint 可用于回滚受控任务工件"
            if rollback.get("rollback_available")
            else "；该动作无法由本地 checkpoint 自动撤销"
        )
        return await self._runtime._approvals.create_approval(
            task_id=task_id,
            organization_id=organization_id,
            step_id=request.step_id,
            tool_call_id=tool_call_id,
            requested_action=tool.tool_name,
            risk_level=risk_level,
            summary=f"需要确认执行 {tool.tool_name}{summary_suffix}",
            payload=self._runtime._redact_payload(
                {**request.args, "rollback_availability": rollback}
            ),
            trace_id=trace_id,
        )
