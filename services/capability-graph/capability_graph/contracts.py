from __future__ import annotations

from core_types import CapabilityDecision, CapabilityRequest


class CapabilityGraph:
    async def decide(self, request: CapabilityRequest) -> CapabilityDecision:
        raise NotImplementedError("CapabilityGraph contract requires an application implementation")
