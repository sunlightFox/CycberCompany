from __future__ import annotations

from core_types import ApiModel, ContextPacket, EntityId


class ContextBuildRequest(ApiModel):
    conversation_id: EntityId
    member_id: EntityId
    turn_id: EntityId
    user_text: str


class ContextGateway:
    async def build(self, request: ContextBuildRequest) -> ContextPacket:
        raise NotImplementedError("ContextGateway contract requires an application implementation")
