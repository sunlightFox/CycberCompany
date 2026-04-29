from __future__ import annotations

from core_types import (
    ApiModel,
    EntityId,
    MemoryBlock,
    MemoryCandidate,
    MemoryItem,
    MemorySearchResponse,
)


class MemorySearchRequest(ApiModel):
    member_id: EntityId
    query: str
    conversation_id: EntityId | None = None
    intent: str | None = None
    limit: int = 10


class MemoryExtractRequest(ApiModel):
    text: str
    member_id: EntityId
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    message_id: EntityId | None = None
    trace_id: EntityId | None = None


class MemoryExtractResult(ApiModel):
    candidates: list[MemoryCandidate]
    memories: list[MemoryItem]
    blocked: bool = False
    reason: str | None = None


class MemoryService:
    async def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        raise NotImplementedError("MemoryService contract requires an application implementation")

    async def extract(self, request: MemoryExtractRequest) -> MemoryExtractResult:
        raise NotImplementedError("MemoryService contract requires an application implementation")

    async def compress(self, response: MemorySearchResponse) -> list[MemoryBlock]:
        raise NotImplementedError("MemoryService contract requires an application implementation")
