from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core_types import RiskLevel, TraceSpanStatus, TraceSpanType, TraceStatus
from trace_service import TraceService, redact

from app.core.time import utc_now_iso
from app.services.audit import AuditEventService
from app.services.checkpoints import CheckpointService
from app.services.memory import MemoryService
from app.services.notifications import NotificationGatewayService
from app.services.scheduled_tasks import ScheduledTaskService
from app.services.tasks import TaskEngine

WorkerHandler = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass
class WorkerState:
    name: str
    enabled: bool = True
    tick_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_status: str = "never_run"
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_heartbeat_at: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None
    last_trace_id: str | None = None
    last_duration_ms: int = 0
    consecutive_failure_count: int = 0
    last_result: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "tick_count": self.tick_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_status": self.last_status,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "last_trace_id": self.last_trace_id,
            "last_duration_ms": self.last_duration_ms,
            "consecutive_failure_count": self.consecutive_failure_count,
            "last_result": redact(self.last_result),
        }


class BackgroundWorkerService:
    """Local single-process worker supervisor with deterministic manual ticks."""

    def __init__(
        self,
        *,
        scheduled_tasks: ScheduledTaskService,
        notifications: NotificationGatewayService,
        checkpoints: CheckpointService,
        task_engine: TaskEngine,
        memory_service: MemoryService,
        agent_workbench_service: Any | None = None,
        wechat_gateway: Any | None = None,
        feishu_gateway: Any | None = None,
        trace_service: TraceService,
        audit_service: AuditEventService,
        enabled: bool = False,
        interval_seconds: float = 5.0,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._scheduled_tasks = scheduled_tasks
        self._notifications = notifications
        self._checkpoints = checkpoints
        self._task_engine = task_engine
        self._memory = memory_service
        self._agent_workbench = agent_workbench_service
        self._channel_gateways: dict[str, Any] = {}
        if wechat_gateway is not None:
            self._channel_gateways["wechat"] = wechat_gateway
        if feishu_gateway is not None:
            self._channel_gateways["feishu"] = feishu_gateway
        self._trace = trace_service
        self._audit = audit_service
        self._enabled = enabled
        self._interval_seconds = max(0.5, interval_seconds)
        self._timeout_seconds = max(1.0, timeout_seconds)
        self._loop_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._loop_started_at: str | None = None
        self._loop_stopped_at: str | None = None
        self._last_loop_started_at: str | None = None
        self._last_loop_finished_at: str | None = None
        self._last_loop_error: str | None = None
        self._handlers: dict[str, WorkerHandler] = {
            "scheduled_due_worker": self._scheduled_due_worker,
            "notification_retry_worker": self._notification_retry_worker,
            "wechat_inbound_worker": self._wechat_inbound_worker,
            "feishu_inbound_worker": self._feishu_inbound_worker,
            "checkpoint_cleanup_worker": self._checkpoint_cleanup_worker,
            "stale_recovery_worker": self._stale_recovery_worker,
            "agent_workbench_reflection_worker": self._agent_workbench_reflection_worker,
        }
        self._states = {
            name: WorkerState(name=name, enabled=True) for name in self._handlers
        }

    def set_channel_gateway(self, provider: str, gateway: Any) -> None:
        self._channel_gateways[provider] = gateway

    def set_wechat_gateway(self, wechat_gateway: Any) -> None:
        self.set_channel_gateway("wechat", wechat_gateway)

    def set_feishu_gateway(self, feishu_gateway: Any) -> None:
        self.set_channel_gateway("feishu", feishu_gateway)

    async def start(self) -> None:
        if not self._enabled or self._loop_task is not None:
            return
        self._loop_started_at = utc_now_iso()
        self._loop_stopped_at = None
        self._last_loop_error = None
        self._loop_task = asyncio.create_task(self._run_loop(), name="cycber-background-workers")

    async def stop(self) -> None:
        task = self._loop_task
        self._loop_task = None
        self._loop_stopped_at = utc_now_iso()
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def manual_tick(self, worker_name: str | None = None) -> dict[str, Any]:
        async with self._lock:
            names = [worker_name] if worker_name else list(self._handlers)
            results: dict[str, Any] = {}
            for name in names:
                if name not in self._handlers:
                    results[name] = {"status": "unknown_worker"}
                    continue
                results[name] = await self._run_worker(name, trigger="manual")
            return {
                "status": "completed",
                "trigger": "manual",
                "worker_count": len(names),
                "results": results,
                "health": self.health(),
            }

    def health(self) -> dict[str, Any]:
        running = self._loop_task is not None and not self._loop_task.done()
        return {
            "component": "BackgroundWorkerService",
            "enabled": self._enabled,
            "running": running,
            "loop_status": "running" if running else "disabled" if not self._enabled else "stopped",
            "loop_started_at": self._loop_started_at,
            "loop_stopped_at": self._loop_stopped_at,
            "last_loop_started_at": self._last_loop_started_at,
            "last_loop_finished_at": self._last_loop_finished_at,
            "last_loop_error": self._last_loop_error,
            "interval_seconds": self._interval_seconds,
            "timeout_seconds": self._timeout_seconds,
            "worker_count": len(self._states),
            "healthy_worker_count": sum(
                1 for state in self._states.values() if state.last_status != "failed"
            ),
            "degraded": any(state.last_status == "failed" for state in self._states.values()),
            "workers": {name: state.as_payload() for name, state in self._states.items()},
        }

    async def _run_loop(self) -> None:
        while True:
            self._last_loop_started_at = utc_now_iso()
            try:
                async with self._lock:
                    for name in self._handlers:
                        await self._run_worker(name, trigger="loop")
                self._last_loop_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_loop_error = str(redact(str(exc)))
            finally:
                self._last_loop_finished_at = utc_now_iso()
            await asyncio.sleep(self._interval_seconds)

    async def _run_worker(self, name: str, *, trigger: str) -> dict[str, Any]:
        state = self._states[name]
        state.tick_count += 1
        state.last_started_at = utc_now_iso()
        state.last_heartbeat_at = state.last_started_at
        started = time.perf_counter()
        trace_id: str | None = None
        span_id: str | None = None
        try:
            trace_id = await self._trace.start_trace()
            state.last_trace_id = trace_id
            span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BACKGROUND_WORKER,
                name=name,
                input_data={
                    "worker": name,
                    "trigger": trigger,
                    "timeout_seconds": self._timeout_seconds,
                },
            )
            result = await asyncio.wait_for(
                self._handlers[name](trace_id),
                timeout=self._timeout_seconds,
            )
            state.last_duration_ms = int((time.perf_counter() - started) * 1000)
            state.success_count += 1
            state.consecutive_failure_count = 0
            state.last_status = "healthy"
            state.last_error = None
            state.last_error_code = None
            state.last_result = redact(result)
            await self._trace.end_span(
                span_id,
                output_data=redact({**result, "duration_ms": state.last_duration_ms}),
            )
            await self._trace.end_trace(trace_id)
            await self._audit.write_event(
                actor_type="system",
                action=f"background_worker.{name}.tick",
                object_type="background_worker",
                object_id=name,
                summary="后台 worker tick 已完成",
                risk_level=RiskLevel.R1,
                payload={"trigger": trigger, "result": redact(result)},
                trace_id=trace_id,
            )
            return {
                "status": "healthy",
                **result,
                "duration_ms": state.last_duration_ms,
                "trace_id": trace_id,
            }
        except asyncio.CancelledError:
            await self._record_worker_failure(
                state,
                name=name,
                trigger=trigger,
                trace_id=trace_id,
                span_id=span_id,
                error="worker cancelled",
                error_code="worker_cancelled",
            )
            raise
        except TimeoutError:
            return await self._record_worker_failure(
                state,
                name=name,
                trigger=trigger,
                trace_id=trace_id,
                span_id=span_id,
                error=f"worker timed out after {self._timeout_seconds:.1f}s",
                error_code="worker_timeout",
            )
        except Exception as exc:
            return await self._record_worker_failure(
                state,
                name=name,
                trigger=trigger,
                trace_id=trace_id,
                span_id=span_id,
                error=str(redact(str(exc))),
                error_code=exc.__class__.__name__,
            )
        finally:
            state.last_finished_at = utc_now_iso()
            state.last_heartbeat_at = state.last_finished_at
            state.last_duration_ms = int((time.perf_counter() - started) * 1000)

    async def _record_worker_failure(
        self,
        state: WorkerState,
        *,
        name: str,
        trigger: str,
        trace_id: str | None,
        span_id: str | None,
        error: str,
        error_code: str,
    ) -> dict[str, Any]:
        state.failure_count += 1
        state.consecutive_failure_count += 1
        state.last_status = "failed"
        state.last_error = error
        state.last_error_code = error_code
        state.last_result = {"error": error, "error_code": error_code}
        if trace_id is not None and span_id is not None:
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": error, "error_code": error_code},
            )
        if trace_id is not None:
            await self._trace.end_trace(trace_id, status=TraceStatus.FAILED)
        if trace_id is not None:
            await self._audit.write_event(
                actor_type="system",
                action=f"background_worker.{name}.failed",
                object_type="background_worker",
                object_id=name,
                summary="后台 worker tick 失败",
                risk_level=RiskLevel.R1,
                payload={"trigger": trigger, "error": error, "error_code": error_code},
                trace_id=trace_id,
            )
        return {
            "status": "failed",
            "error": error,
            "error_code": error_code,
            "trace_id": trace_id,
        }

    async def _scheduled_due_worker(self, trace_id: str) -> dict[str, Any]:
        runs = await self._scheduled_tasks.scan_due(trace_id=trace_id)
        return {
            "due_runs": len(runs),
            "run_ids": [run.run_id for run in runs],
            "task_ids": [run.task_id for run in runs if run.task_id],
        }

    async def _notification_retry_worker(self, trace_id: str) -> dict[str, Any]:
        messages = await self._notifications.retry_due(trace_id=trace_id)
        return {
            "processed_messages": len(messages),
            "notification_ids": [item.notification_id for item in messages],
            "statuses": [item.status for item in messages],
        }

    async def _wechat_inbound_worker(self, trace_id: str) -> dict[str, Any]:
        gateway = self._channel_gateways.get("wechat")
        if gateway is None:
            return {"status": "skipped", "reason": "wechat_gateway_unavailable"}
        inbound = await gateway.poll_once(trace_id=trace_id)
        outbound = await gateway.deliver_due(trace_id=trace_id)
        return {
            "inbound": inbound.model_dump(mode="json"),
            "outbound": outbound.model_dump(mode="json"),
        }

    async def _feishu_inbound_worker(self, trace_id: str) -> dict[str, Any]:
        gateway = self._channel_gateways.get("feishu")
        if gateway is None:
            return {"status": "skipped", "reason": "feishu_gateway_unavailable"}
        inbound = await gateway.poll_once(trace_id=trace_id)
        outbound = await gateway.deliver_due(trace_id=trace_id)
        return {
            "inbound": inbound.model_dump(mode="json"),
            "outbound": outbound.model_dump(mode="json"),
        }

    async def _checkpoint_cleanup_worker(self, trace_id: str) -> dict[str, Any]:
        expired = await self._checkpoints.expire_due_checkpoints(trace_id=trace_id)
        return {"expired_checkpoints": expired, "mode": "mark_expired"}

    async def _stale_recovery_worker(self, trace_id: str) -> dict[str, Any]:
        await self._task_engine.recover_stale_jobs()
        memory_restored = await self._memory.recover_stale_jobs()
        memory_processed = await self._memory.process_pending_jobs(limit=10)
        scheduled_runs_recovered = await self._scheduled_tasks.recover_stale_runs(
            trace_id=trace_id
        )
        return {
            "task_jobs_recovered": "best_effort",
            "memory_jobs_restored": memory_restored,
            "memory_jobs_processed": memory_processed,
            "scheduled_runs_recovered": scheduled_runs_recovered,
        }

    async def _agent_workbench_reflection_worker(self, trace_id: str) -> dict[str, Any]:
        if self._agent_workbench is None:
            return {"status": "skipped", "reason": "agent_workbench_unavailable"}
        restored = await self._agent_workbench.recover_stale_jobs()
        processed = await self._agent_workbench.process_pending_jobs(
            limit=10,
            trace_id=trace_id,
        )
        return {
            "agent_workbench_jobs_restored": restored,
            "agent_workbench_jobs_processed": processed,
        }
