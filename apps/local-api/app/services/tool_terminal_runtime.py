from __future__ import annotations

from typing import Any

from core_types import ErrorCode

from app.core.errors import AppError
from app.services.terminal_lane import (
    TERMINAL_LANES,
    classify_terminal_execution_semantics,
    select_terminal_lane,
)


class ToolTerminalRuntime:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def execute(
        self,
        request: Any,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> Any:
        if request.tool_name == "terminal.stop":
            return self._normalize_outcome(
                result={
                    "status": "not_running",
                    "message": "当前运行时以同步子进程执行，暂无可停止的后台终端进程。",
                },
                lane=select_terminal_lane(
                    tool_name=request.tool_name,
                    command="",
                    terminal_policy=None,
                ),
                terminal_policy=None,
                approval_id=request.approval_id,
                tool_name=request.tool_name,
                command="",
            )
        if request.tool_name == "terminal.read_log":
            return await self.read_log(request, approval_id=request.approval_id)
        if request.tool_name != "terminal.run":
            return self._normalize_outcome(
                result={"status": "not_running"},
                lane=select_terminal_lane(
                    tool_name=request.tool_name,
                    command="",
                    terminal_policy=None,
                ),
                terminal_policy=None,
                approval_id=request.approval_id,
                tool_name=request.tool_name,
                command="",
            )
        return await self._execute_run(
            request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            trace_id=trace_id,
        )

    async def read_log(self, request: Any, *, approval_id: str | None = None) -> Any:
        from app.services.tools import ToolRunOutcome

        lane = select_terminal_lane(
            tool_name="terminal.read_log",
            command="",
            terminal_policy=None,
        )
        artifact_id = request.args.get("artifact_id")
        if artifact_id:
            artifact, preview = await self._runtime._artifacts.read_preview(str(artifact_id))
            if artifact.artifact_type != "terminal_log":
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "指定工件不是终端日志",
                    status_code=403,
                )
            return self._normalize_outcome(
                result={
                    "status": "completed",
                    "reason_code": "terminal_log_available",
                    "log_artifact_id": artifact.artifact_id,
                    "content_preview": preview,
                    "recoverable": False,
                    "next_step": None,
                },
                lane=lane,
                terminal_policy=None,
                approval_id=approval_id,
                tool_name="terminal.read_log",
                command="",
                artifacts=[],
            )
        if not request.task_id:
            return self._normalize_outcome(
                result={
                    "status": "unavailable",
                    "reason_code": "task_id_required",
                    "log_artifact_id": None,
                    "content_preview": None,
                    "recoverable": True,
                    "next_step": "提供 task_id 或 log artifact_id 后重试。",
                },
                lane=lane,
                terminal_policy=None,
                approval_id=approval_id,
                tool_name="terminal.read_log",
                command="",
                artifacts=[],
            )
        logs = [
            artifact
            for artifact in await self._runtime._repo.list_artifacts(request.task_id)
            if artifact["artifact_type"] == "terminal_log"
        ]
        if not logs:
            reason = "terminal_log_missing"
            next_step = "先执行 terminal.run 并通过审批；执行完成后再读取日志。"
            task = await self._runtime._repo.get_task(request.task_id)
            task_status = str((task or {}).get("status") or "")
            steps = await self._runtime._repo.list_steps(request.task_id)
            terminal_steps = [
                step
                for step in steps
                if str((step.get("input") or {}).get("tool_name") or "") == "terminal.run"
            ]
            latest_terminal_step = terminal_steps[-1] if terminal_steps else None
            approval_status = ""
            if latest_terminal_step and latest_terminal_step.get("approval_id"):
                approval = await self._runtime._repo.get_approval(str(latest_terminal_step["approval_id"]))
                approval_status = str((approval or {}).get("status") or "")
            if task_status == "waiting_approval":
                reason = "waiting_approval"
                next_step = "先确认终端命令；确认前不会产生终端日志。"
                if approval_status in {"approved", "edited"}:
                    reason = "approval_resolved_pending_resume"
                    next_step = "审批已通过，终端命令正在恢复执行；稍后再读取日志。"
            elif latest_terminal_step and str(latest_terminal_step.get("status") or "") == "running":
                reason = "executing_after_approval"
                next_step = "终端命令正在执行；执行完成后再读取日志。"
            elif task_status in {"planned", "pending"}:
                reason = "terminal_not_executed"
                next_step = "先启动任务或执行 terminal.run。"
            elif task_status in {"failed", "cancelled", "paused"}:
                reason = f"task_{task_status}"
                next_step = "查看任务回放了解失败/暂停原因，或重新发起命令。"
            elif latest_terminal_step and str(latest_terminal_step.get("status") or "") == "completed":
                reason = "completed_but_log_missing"
                next_step = "终端命令已结束，但日志工件缺失；请查看任务回放和 tool_call 输出。"
            return self._normalize_outcome(
                result={
                    "status": "unavailable",
                    "reason_code": reason,
                    "log_artifact_id": None,
                    "content_preview": None,
                    "recoverable": True,
                    "next_step": next_step,
                    "task_status": task_status or None,
                    "step_status": (
                        str(latest_terminal_step.get("status") or "")
                        if latest_terminal_step
                        else None
                    ),
                    "approval_status": approval_status or None,
                },
                lane=lane,
                terminal_policy=None,
                approval_id=approval_id,
                tool_name="terminal.read_log",
                command="",
                artifacts=[],
            )
        artifact, preview = await self._runtime._artifacts.read_preview(logs[-1]["artifact_id"])
        return self._normalize_outcome(
            result={
                "status": "completed",
                "reason_code": "terminal_log_available",
                "log_artifact_id": artifact.artifact_id,
                "content_preview": preview,
                "recoverable": False,
                "next_step": None,
            },
            lane=lane,
            terminal_policy=None,
            approval_id=approval_id,
            tool_name="terminal.read_log",
            command="",
            artifacts=[],
        )

    async def stop(self, request: Any, *, approval_id: str | None = None) -> Any:
        del request
        return await self.execute(
            type("TerminalStopRequest", (), {"tool_name": "terminal.stop", "approval_id": approval_id})(),
            tool_call_id="call_terminal_stop",
            organization_id="org_default",
            trace_id=None,
        )

    def enrich_policy_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        sandbox_result: Any,
        log_artifact_id: str,
        dlp_report_id: str | None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        next_snapshot = dict(snapshot)
        next_snapshot["terminal_sandbox_result"] = {
            "selected_backend": sandbox_result.backend,
            "backend": sandbox_result.backend,
            "backend_status": sandbox_result.backend_status,
            "fallback_chain": sandbox_result.fallback_chain,
            "degraded_reason": sandbox_result.degraded_reason,
            "timed_out": sandbox_result.timed_out,
            "output_truncated": sandbox_result.output_truncated,
            "resource_usage": sandbox_result.resource_usage,
            "cleanup": sandbox_result.cleanup,
            "approval_binding": approval_id,
            "log_artifact_id": log_artifact_id,
            "dlp_report_id": dlp_report_id,
        }
        return next_snapshot

    def snapshot(self) -> dict[str, Any]:
        queue = self._runtime._terminal_queue.snapshot()
        return {
            "maturity": "runtime_native",
            "execution_mode": "queued_sandboxed_sync",
            "queue_enabled": True,
            "lane_model": "in_memory_lanes_v1",
            "lanes": list(TERMINAL_LANES),
            "snapshot_supported": True,
            "reset_supported": True,
            "release_supported": True,
            "queue_snapshot": queue,
        }

    async def reset_lane(self, lane: str) -> int:
        return self._runtime._terminal_queue.reset_lane(lane)

    async def release_expired(self) -> int:
        return self._runtime._terminal_queue.release_expired()

    async def _execute_run(
        self,
        request: Any,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> Any:
        from app.services.tools import ToolRunOutcome, _terminal_command_policy

        if not request.task_id:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "终端工具必须绑定任务",
                status_code=409,
            )
        command = str(request.args.get("command") or "")
        if request.args.get("cwd"):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "终端工具不接受自定义 cwd，只能在任务工件沙箱中执行",
                status_code=403,
            )
        terminal_policy = (
            self._runtime._boundary.classify_terminal_command(command)
            if self._runtime._boundary is not None
            else _terminal_command_policy(command)
        )
        if terminal_policy["decision"] == "deny":
            raise AppError(
                ErrorCode.TOOL_OUTPUT_BLOCKED,
                "危险终端命令已被阻断",
                status_code=403,
                details={"reason": terminal_policy["reason"]},
            )
        if self._runtime._boundary is None:
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "执行边界服务未初始化，拒绝运行终端命令",
                status_code=500,
            )
        lane = select_terminal_lane(
            tool_name=request.tool_name,
            command=command,
            terminal_policy=terminal_policy,
        )
        cwd = self._runtime._artifacts.task_dir(request.task_id)
        cwd.mkdir(parents=True, exist_ok=True)

        async def _work() -> Any:
            sandbox_profile = await self._runtime._boundary.sandbox_profile()
            sandbox_result = await self._runtime._boundary.run_terminal_command(
                organization_id=organization_id,
                task_id=request.task_id,
                command=command,
                cwd=cwd,
                timeout_seconds=request.args.get("timeout_seconds"),
                max_output_bytes=request.args.get("max_output_bytes"),
                tool_call_id=tool_call_id,
                trace_id=trace_id,
            )
            output = sandbox_result.output
            dlp = await self._runtime._boundary.scan_output(
                organization_id=organization_id,
                source_type="terminal_output",
                source_id=tool_call_id,
                scan_target="stdout_stderr",
                value=output,
                tool_call_id=tool_call_id,
                task_id=request.task_id,
                trace_id=trace_id,
            )
            redacted_output = str(dlp.redacted_value)
            dlp_report_id = dlp.report.dlp_report_id
            sandbox_profile_result = sandbox_result.sandbox_profile_result()
            if sandbox_profile is not None:
                sandbox_profile_result["profile_id"] = sandbox_profile.profile_id
            sandbox_metadata = {
                "sandbox": "task_artifact",
                "sandbox_profile": {
                    **sandbox_profile_result,
                    "cwd": "task_artifact_sandbox",
                },
                "selected_backend": sandbox_result.backend,
                "backend_status": sandbox_result.backend_status,
                "fallback_chain": sandbox_result.fallback_chain,
                "degraded_reason": sandbox_result.degraded_reason,
                "resource_usage": sandbox_result.resource_usage,
                "cleanup": sandbox_result.cleanup,
                "env_policy": sandbox_result.env_policy,
                "filesystem_policy": sandbox_result.filesystem_policy,
                "network_policy": sandbox_result.network_policy,
                "output_truncated": sandbox_result.output_truncated,
                "timed_out": sandbox_result.timed_out,
                "command_class": terminal_policy["command_class"],
                "policy_snapshot": terminal_policy,
                "dlp_report_id": dlp_report_id,
                "cwd": "task_artifact_sandbox",
                "lane": lane,
            }
            artifact = await self._runtime._artifacts.write_text(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name="terminal.log",
                content=redacted_output,
                artifact_type="terminal_log",
                subdir="logs",
                metadata=sandbox_metadata,
                trace_id=trace_id,
            )
            await self._runtime._update_terminal_policy_snapshot(
                tool_call_id,
                sandbox_result=sandbox_result,
                log_artifact_id=artifact.artifact_id,
                dlp_report_id=dlp_report_id,
            )
            base_result = {
                "exit_code": sandbox_result.exit_code,
                "output_preview": redacted_output[:1000],
                "log_artifact_id": artifact.artifact_id,
                "sandbox_profile": sandbox_profile_result,
                "policy_snapshot": terminal_policy,
                "selected_backend": sandbox_result.backend,
                "backend_status": sandbox_result.backend_status,
                "fallback_chain": sandbox_result.fallback_chain,
                "degraded_reason": sandbox_result.degraded_reason,
                "output_truncated": sandbox_result.output_truncated,
                "timed_out": sandbox_result.timed_out,
                "resource_usage": sandbox_result.resource_usage,
                "cleanup": sandbox_result.cleanup,
                "dlp_report_id": dlp_report_id,
            }
            normalized = self._normalize_result(
                result=base_result,
                lane=lane,
                terminal_policy=terminal_policy,
                approval_id=request.approval_id,
                tool_name=request.tool_name,
                command=command,
            )
            if sandbox_result.timed_out:
                raise AppError(
                    ErrorCode.TOOL_TIMEOUT,
                    "终端命令超时，沙箱已尝试终止进程树",
                    status_code=504,
                    details={
                        "sandbox_profile": sandbox_profile_result,
                        "cleanup": sandbox_result.cleanup,
                        "terminal_result": normalized,
                    },
                )
            return ToolRunOutcome(result=normalized, artifacts=[artifact])

        return await self._runtime._terminal_queue.enqueue(
            lane,
            _work,
            tool_call_id=tool_call_id,
            timeout_seconds=request.args.get("timeout_seconds"),
        )

    def _normalize_outcome(
        self,
        *,
        result: dict[str, Any],
        lane: str,
        terminal_policy: dict[str, Any] | None,
        approval_id: str | None,
        tool_name: str,
        command: str,
        artifacts: list[Any] | None = None,
    ) -> Any:
        from app.services.tools import ToolRunOutcome

        return ToolRunOutcome(
            result=self._normalize_result(
                result=result,
                lane=lane,
                terminal_policy=terminal_policy,
                approval_id=approval_id,
                tool_name=tool_name,
                command=command,
            ),
            artifacts=list(artifacts or []),
        )

    def _normalize_result(
        self,
        *,
        result: dict[str, Any],
        lane: str,
        terminal_policy: dict[str, Any] | None,
        approval_id: str | None,
        tool_name: str,
        command: str,
    ) -> dict[str, Any]:
        normalized = dict(result)
        status = str(normalized.get("status") or ("timed_out" if normalized.get("timed_out") else "completed"))
        if status == "not_running":
            status = "completed"
        normalized["status"] = status
        normalized["execution_semantics"] = classify_terminal_execution_semantics(
            tool_name=tool_name,
            command=command,
            terminal_policy=terminal_policy,
            lane=lane,
        )
        normalized["evidence_refs"] = {
            "log_artifact_id": normalized.get("log_artifact_id"),
            "dlp_report_id": normalized.get("dlp_report_id"),
        }
        normalized["approval_state"] = {
            "status": "resolved" if approval_id else "not_required",
            "approval_id": approval_id,
        }
        normalized.setdefault("sandbox_profile", None)
        normalized.setdefault("backend_status", None)
        normalized.setdefault("degraded_reason", None)
        normalized.setdefault("resource_usage", {})
        normalized.setdefault("cleanup", {})
        normalized["retryable"] = bool(
            normalized.get("timed_out")
            or status in {"timed_out", "unavailable"}
            or normalized.get("degraded_reason")
        )
        return normalized
