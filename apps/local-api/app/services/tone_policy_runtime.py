from __future__ import annotations

from typing import Any


class TonePolicyRuntimeService:
    CONTRACT_VERSION = "phase117.tone_policy_runtime.v1"

    def __init__(self, *, persona_heart_service: Any) -> None:
        self._persona_heart_service = persona_heart_service

    async def resolve(self, *args: Any, **kwargs: Any) -> Any:
        return await self._persona_heart_service.resolve_tone_policy(*args, **kwargs)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "tone_policy_runtime",
            "plane": "policy_plane",
            "owner": "tone_policy_runtime",
            "contract_version": self.CONTRACT_VERSION,
            "delegates_to": ["persona_heart_service.resolve_tone_policy"],
        }
