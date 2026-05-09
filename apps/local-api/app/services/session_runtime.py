from __future__ import annotations

from typing import Any

from core_types import ChatTurnRequest, ChatTurnResponse


class SessionRuntime:
    def __init__(self, *, chat_service: Any) -> None:
        self._chat = chat_service

    async def create_turn(
        self,
        request: ChatTurnRequest,
        *,
        retry_of_turn_id: str | None = None,
    ) -> ChatTurnResponse:
        return await self._chat._create_turn_impl(
            request,
            retry_of_turn_id=retry_of_turn_id,
        )

    async def run_turn(self, turn_id: str) -> None:
        await self._chat._run_turn_impl(turn_id)

    async def stream_turn_events(self, turn_id: str) -> Any:
        async for event in self._chat._stream_turn_events_impl(turn_id):
            yield event

    async def cancel_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._chat._cancel_turn_impl(turn_id)

    async def retry_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._chat._retry_turn_impl(turn_id)

    async def recover_incomplete_turns(self) -> int:
        return await self._chat._recover_incomplete_turns_impl()

    async def diagnostic(self) -> dict[str, Any]:
        running_turns = await self._chat._chat_repo.list_running_turns()
        return {
            "runtime": "session_runtime",
            "executor": "turn_execution_manager",
            "ingress": "chat_ingress_service",
            "route_selectors": [
                "brain_decision_service",
                "chat_task_coordinator",
                "chat_continuation_coordinator",
                "scheduled_task_service",
            ],
            "running_turn_count": len(running_turns),
        }
