from __future__ import annotations

from typing import Any


class PersonaRuntimeService:
    CONTRACT_VERSION = "phase117.persona_runtime.v1"

    def __init__(self, *, persona_heart_service: Any) -> None:
        self._persona_heart_service = persona_heart_service

    async def summary(self, *args: Any, **kwargs: Any) -> Any:
        return await self._persona_heart_service.persona_summary(*args, **kwargs)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "persona_runtime",
            "plane": "policy_plane",
            "owner": "persona_runtime",
            "contract_version": self.CONTRACT_VERSION,
            "delegates_to": ["persona_heart_service.persona_summary"],
        }
