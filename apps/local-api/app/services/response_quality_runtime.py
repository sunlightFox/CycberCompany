from __future__ import annotations

from typing import Any


class ResponseQualityRuntimeService:
    CONTRACT_VERSION = "phase117.response_quality_runtime.v1"

    def __init__(self, *, persona_heart_service: Any) -> None:
        self._persona_heart_service = persona_heart_service

    async def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        return await self._persona_heart_service.evaluate_response_quality(*args, **kwargs)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "response_quality_runtime",
            "plane": "policy_plane",
            "owner": "response_quality_runtime",
            "contract_version": self.CONTRACT_VERSION,
            "delegates_to": ["persona_heart_service.evaluate_response_quality"],
        }
