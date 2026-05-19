from __future__ import annotations

from typing import Any

from core_types import ChatTurnRequest, ChatTurnResponse


class SessionRuntime:
    def __init__(self, *, chat_runtime: Any, chat_repo: Any, agent_runtime: Any | None = None) -> None:
        self._runtime = chat_runtime
        self._agent_runtime = agent_runtime
        self._chat_repo = chat_repo

    async def create_turn(
        self,
        request: ChatTurnRequest,
        *,
        retry_of_turn_id: str | None = None,
    ) -> ChatTurnResponse:
        return await self._runtime.create_turn(
            request,
            retry_of_turn_id=retry_of_turn_id,
        )

    async def run_turn(self, turn_id: str) -> None:
        runner = self._agent_runtime or self._runtime
        await runner.run_turn(turn_id)

    async def stream_turn_events(self, turn_id: str) -> Any:
        async for event in self._runtime.stream_turn_events(turn_id):
            yield event

    async def cancel_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._runtime.cancel_turn(turn_id)

    async def retry_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._runtime.retry_turn(turn_id)

    async def recover_incomplete_turns(self) -> int:
        runner = self._agent_runtime or self._runtime
        return await runner.recover_turns()

    async def diagnostic(self) -> dict[str, Any]:
        running_turns = await self._chat_repo.list_running_turns()
        return {
            "runtime": "session_runtime",
            "plane": "session_plane",
            "owner": "session_runtime",
            "contract_version": "phase117.session_runtime_proxy.v1",
            "executor": "turn_execution_manager",
            "ingress": "chat_ingress_service",
            "route_source": "session_runtime",
            "delegates_to": "agent_runtime" if self._agent_runtime is not None else "chat_runtime",
            "maturity": "runtime_native",
            "ownership_mode": "proxy_only",
            "state_machine_owner": "agent_runtime" if self._agent_runtime is not None else "chat_runtime",
            "event_source": "agent_runtime" if self._agent_runtime is not None else "chat_runtime",
            "business_logic_owner": "agent_runtime" if self._agent_runtime is not None else "chat_runtime",
            "growth_gate": "phase117_session_runtime_proxy_only",
            "public_entrypoints": [
                "create_turn",
                "run_turn",
                "stream_turn_events",
                "cancel_turn",
                "retry_turn",
                "recover_incomplete_turns",
            ],
            "route_selectors": [
                "session_runtime_entry_contract",
                "chat_runtime_dispatch",
            ],
            "running_turn_count": len(running_turns),
        }
