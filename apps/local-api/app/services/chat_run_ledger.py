from __future__ import annotations

from typing import Any

from core_types import ChatEventType

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.memory_repo import MemoryRepository


_TERMINAL_TURN_STATUSES = {"completed", "failed", "cancelled", "retried"}
_RUN_LEDGER_REF_TYPES = {
    "tool_call",
    "approval",
    "task",
    "memory_write",
    "artifact",
    "channel_delivery",
    "recovery_attempt",
}


class ChatRunLedgerService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        memory_repo: MemoryRepository,
    ) -> None:
        self._chat_repo = chat_repo
        self._memory_repo = memory_repo

    async def record_turn_created(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        session_id: str | None,
        member_id: str,
        trace_id: str | None,
        retry_of_turn_id: str | None,
        channel: str | None,
        source_message_id: str | None,
        created_at: str | None = None,
        trace_span_id: str | None = None,
    ) -> None:
        now = created_at or utc_now_iso()
        await self._chat_repo.upsert_turn_ledger(
            {
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "session_id": session_id,
                "member_id": member_id,
                "trace_id": trace_id,
                "status": "created",
                "route_type": None,
                "mode": None,
                "started_at": None,
                "ended_at": None,
                "retry_of_turn_id": retry_of_turn_id,
                "recovered_from_turn_id": None,
                "channel": channel,
                "source_message_id": source_message_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage="turn_accept",
            event_type="turn.accepted",
            status="accepted",
            summary="turn accepted and queued",
            payload={
                "conversation_id": conversation_id,
                "session_id": session_id,
                "channel": channel or "local",
                "source_message_id": source_message_id,
            },
            trace_span_id=trace_span_id,
        )

    async def mark_turn_started(
        self,
        turn_id: str,
        *,
        trace_id: str | None,
        started_at: str | None = None,
        trace_span_id: str | None = None,
    ) -> None:
        now = started_at or utc_now_iso()
        existing = await self._chat_repo.get_turn_ledger(turn_id)
        if existing is None:
            turn = await self._chat_repo.get_turn(turn_id)
            if turn is None:
                return
            await self._chat_repo.upsert_turn_ledger(
                {
                    "turn_id": turn_id,
                    "conversation_id": turn["conversation_id"],
                    "session_id": None,
                    "member_id": turn["member_id"],
                    "trace_id": trace_id or turn.get("trace_id"),
                    "status": "running",
                    "route_type": None,
                    "mode": None,
                    "started_at": now,
                    "ended_at": None,
                    "retry_of_turn_id": turn.get("retry_of_turn_id"),
                    "recovered_from_turn_id": None,
                    "channel": None,
                    "source_message_id": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        else:
            await self._chat_repo.upsert_turn_ledger(
                {
                    "turn_id": turn_id,
                    "conversation_id": existing["conversation_id"],
                    "session_id": existing.get("session_id"),
                    "member_id": existing["member_id"],
                    "trace_id": trace_id or existing.get("trace_id"),
                    "status": "running",
                    "route_type": existing.get("route_type"),
                    "mode": existing.get("mode"),
                    "started_at": now,
                    "ended_at": existing.get("ended_at"),
                    "retry_of_turn_id": existing.get("retry_of_turn_id"),
                    "recovered_from_turn_id": existing.get("recovered_from_turn_id"),
                    "channel": existing.get("channel"),
                    "source_message_id": existing.get("source_message_id"),
                    "created_at": existing["created_at"],
                    "updated_at": now,
                }
            )
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage="turn_execution",
            event_type="turn.started",
            status="running",
            summary="turn execution started",
            payload={},
            trace_span_id=trace_span_id,
        )

    async def record_chat_event(
        self,
        *,
        turn_id: str,
        trace_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        stage = _stage_for_event(event_type)
        status = _status_for_event(event_type, payload)
        ref_id, ref_type = _ref_for_event(event_type, payload)
        summary = _summary_for_event(event_type, payload)
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage=stage,
            event_type=event_type,
            status=status,
            ref_id=ref_id,
            ref_type=ref_type,
            summary=summary,
            payload=payload,
        )
        await self._sync_turn_ledger_from_event(
            turn_id=turn_id,
            trace_id=trace_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
        )

    async def record_memory_write_decision(
        self,
        *,
        turn_id: str | None,
        trace_id: str | None,
        conversation_id: str | None,
        memory_id: str | None,
        candidate_id: str | None,
        decision: str,
        source: dict[str, Any],
        summary: str,
    ) -> None:
        if not turn_id:
            return
        ref_id = memory_id or candidate_id
        payload = {
            "decision": decision,
            "conversation_id": conversation_id,
            "memory_id": memory_id,
            "candidate_id": candidate_id,
            "source": source,
            "summary": summary,
        }
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage="memory_write",
            event_type=f"memory.{decision}",
            status=decision,
            ref_id=ref_id,
            ref_type="memory_write",
            summary=summary,
            payload=payload,
        )

    async def record_channel_delivery(
        self,
        *,
        turn_id: str,
        trace_id: str | None,
        message_id: str,
        channel: str,
        summary: str,
    ) -> None:
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage="channel_delivery",
            event_type="channel.delivery",
            status="completed",
            ref_id=message_id,
            ref_type="channel_delivery",
            summary=summary,
            payload={"channel": channel, "assistant_message_id": message_id},
        )

    async def record_hook_execution(
        self,
        *,
        turn_id: str | None,
        trace_id: str | None,
        hook_stage: str,
        hook_name: str,
        status: str,
        reason_code: str | None,
        blocked: bool,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not turn_id:
            return
        await self.append_run_entry(
            turn_id=turn_id,
            trace_id=trace_id,
            stage=f"hook:{hook_stage}",
            event_type=f"hook.{hook_stage}",
            status=status,
            summary=summary or f"hook {hook_stage}:{hook_name} {status}",
            payload={
                "hook_stage": hook_stage,
                "hook_name": hook_name,
                "reason_code": reason_code,
                "blocked": blocked,
                **dict(payload or {}),
            },
        )

    async def append_run_entry(
        self,
        *,
        turn_id: str,
        trace_id: str | None,
        stage: str,
        event_type: str,
        status: str,
        summary: str | None,
        payload: dict[str, Any],
        ref_id: str | None = None,
        ref_type: str | None = None,
        trace_span_id: str | None = None,
    ) -> None:
        await self._chat_repo.insert_run_ledger(
            {
                "run_id": new_id("runlg"),
                "turn_id": turn_id,
                "trace_id": trace_id,
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "ref_id": ref_id,
                "ref_type": ref_type if ref_type in _RUN_LEDGER_REF_TYPES else None,
                "summary": summary,
                "payload": payload,
                "trace_span_id": trace_span_id,
                "created_at": utc_now_iso(),
            }
        )

    async def timeline(self, turn_id: str) -> dict[str, Any]:
        turn = await self._chat_repo.get_turn_ledger(turn_id)
        run_entries = await self._chat_repo.list_run_ledgers(turn_id)
        return {
            "turn": turn,
            "timeline": run_entries,
        }

    async def memory_source_chain(self, memory_id: str) -> dict[str, Any]:
        memory = await self._memory_repo.get_memory_item(memory_id)
        if memory is None:
            return {"memory": None, "source_chain": []}
        source = dict(memory.get("source") or {})
        turn_id = source.get("turn_id")
        timeline = await self._chat_repo.list_run_ledgers(turn_id) if turn_id else []
        return {
            "memory": memory,
            "source_chain": timeline,
        }

    async def _sync_turn_ledger_from_event(
        self,
        *,
        turn_id: str,
        trace_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        ledger = await self._chat_repo.get_turn_ledger(turn_id)
        if ledger is None:
            turn = await self._chat_repo.get_turn(turn_id)
            if turn is None:
                return
            ledger = {
                "turn_id": turn_id,
                "conversation_id": turn["conversation_id"],
                "session_id": None,
                "member_id": turn["member_id"],
                "trace_id": trace_id or turn.get("trace_id"),
                "status": turn["status"],
                "route_type": None,
                "mode": None,
                "started_at": None,
                "ended_at": None,
                "retry_of_turn_id": turn.get("retry_of_turn_id"),
                "recovered_from_turn_id": None,
                "channel": None,
                "source_message_id": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
        route_type = ledger.get("route_type")
        mode = ledger.get("mode")
        status = ledger.get("status") or "created"
        started_at = ledger.get("started_at")
        ended_at = ledger.get("ended_at")
        if event_type == ChatEventType.MODE_SELECTED.value:
            mode = str(payload.get("mode") or mode or "")
        elif event_type == ChatEventType.ROUTE_SELECTED.value:
            route_type = str(payload.get("route_taxonomy") or payload.get("route") or route_type or "")
        elif event_type == ChatEventType.TURN_STARTED.value:
            status = "running"
            started_at = started_at or created_at
        elif event_type == ChatEventType.TURN_COMPLETED.value:
            status = "completed"
            ended_at = created_at
        elif event_type == ChatEventType.TURN_FAILED.value:
            status = "failed"
            ended_at = created_at
        elif event_type == ChatEventType.TURN_CANCELLED.value:
            status = "cancelled"
            ended_at = created_at
        await self._chat_repo.upsert_turn_ledger(
            {
                "turn_id": turn_id,
                "conversation_id": ledger["conversation_id"],
                "session_id": ledger.get("session_id"),
                "member_id": ledger["member_id"],
                "trace_id": trace_id or ledger.get("trace_id"),
                "status": status,
                "route_type": route_type,
                "mode": mode,
                "started_at": started_at,
                "ended_at": ended_at,
                "retry_of_turn_id": ledger.get("retry_of_turn_id"),
                "recovered_from_turn_id": ledger.get("recovered_from_turn_id"),
                "channel": ledger.get("channel"),
                "source_message_id": ledger.get("source_message_id"),
                "created_at": ledger["created_at"],
                "updated_at": created_at,
            }
        )


def _stage_for_event(event_type: str) -> str:
    if event_type in {
        ChatEventType.CONTEXT_STARTED.value,
        ChatEventType.CONTEXT_READY.value,
        ChatEventType.CONTEXT_COMPACTION_STARTED.value,
        ChatEventType.CONTEXT_COMPACTION_COMPLETED.value,
    }:
        return "context_build"
    if event_type in {
        ChatEventType.INTENT_DETECTED.value,
        ChatEventType.MODE_SELECTED.value,
        ChatEventType.ROUTE_SELECTED.value,
        ChatEventType.MODEL_FALLBACK.value,
    }:
        return "brain_decision"
    if event_type.startswith("memory."):
        return "memory_write"
    if event_type.startswith("turn."):
        return "turn_execution"
    if event_type.startswith("response."):
        return "response_finalize"
    if event_type.startswith("model."):
        return "model_execution"
    return "runtime_event"


def _status_for_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == ChatEventType.TURN_FAILED.value:
        return "failed"
    if event_type == ChatEventType.TURN_CANCELLED.value:
        return "cancelled"
    if event_type == ChatEventType.TURN_COMPLETED.value:
        return "completed"
    if event_type == ChatEventType.TURN_STARTED.value:
        return "running"
    if event_type == ChatEventType.MODEL_FALLBACK.value:
        return "recovered"
    if event_type == ChatEventType.RESPONSE_COMPLETED.value:
        return str(payload.get("status") or "completed")
    return "completed"


def _ref_for_event(event_type: str, payload: dict[str, Any]) -> tuple[str | None, str | None]:
    if event_type.startswith("memory."):
        return (
            str(payload.get("memory_id") or payload.get("candidate_id") or "")
            or None,
            "memory_write",
        )
    route_semantics = payload.get("route_semantics")
    route_semantics = route_semantics if isinstance(route_semantics, dict) else {}
    tool_result = payload.get("tool_result_context")
    tool_result = tool_result if isinstance(tool_result, dict) else {}
    if tool_result.get("tool_call_id") or route_semantics.get("tool_call_id"):
        return (
            str(tool_result.get("tool_call_id") or route_semantics.get("tool_call_id")),
            "tool_call",
        )
    if route_semantics.get("approval_id"):
        return str(route_semantics.get("approval_id")), "approval"
    if route_semantics.get("task_id"):
        return str(route_semantics.get("task_id")), "task"
    return None, None


def _summary_for_event(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == ChatEventType.RESPONSE_COMPLETED.value:
        response_plan = payload.get("response_plan")
        if isinstance(response_plan, dict):
            return str(response_plan.get("plain_text") or "")[:400] or event_type
    if event_type == ChatEventType.TURN_FAILED.value:
        return str(payload.get("message") or payload.get("code") or "turn failed")
    if event_type.startswith("memory."):
        return str(payload.get("summary") or payload.get("reason") or event_type)
    return event_type
