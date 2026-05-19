from __future__ import annotations

from typing import Any


class AgentRuntime:
    CONTRACT_VERSION = "phase117.agent_runtime_owner.v1"

    def __init__(self, *, chat_runtime: Any) -> None:
        self._chat_runtime = chat_runtime

    async def run_turn(self, turn_id: str) -> None:
        await self._chat_runtime._run_turn_as_owner(turn_id)

    async def recover_turns(self) -> int:
        return await self._chat_runtime._recover_turns_as_owner()

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "agent_runtime",
            "plane": "agent_runtime_plane",
            "owner": "agent_runtime",
            "contract_version": self.CONTRACT_VERSION,
            "maturity": "runtime_native",
            "ownership_mode": "exclusive_execution_owner",
            "turn_execution_owner": "agent_runtime",
            "state_machine_owner": "agent_runtime",
            "delegates_to": [
                "chat_turn_execution_orchestrator",
                "chat_model_execution_service",
                "chat_turn_finalize_service",
            ],
            "public_entrypoints": [
                "run_turn",
                "recover_turns",
                "diagnostic",
            ],
            "compat_entrypoints": [
                "chat_runtime.run_turn",
                "session_runtime.run_turn",
            ],
        }
