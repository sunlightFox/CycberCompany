from __future__ import annotations

from core_types import ApiModel, EntityId


class SkillMatchRequest(ApiModel):
    member_id: EntityId
    intent: str


class SkillMatch(ApiModel):
    skill_id: EntityId
    confidence: float
    reason: str


class SkillEngine:
    async def match(self, request: SkillMatchRequest) -> list[SkillMatch]:
        raise NotImplementedError("SkillEngine contract requires an application implementation")
