from __future__ import annotations

from core_types import ApiModel, TaskSummary


class TaskCreateRequest(ApiModel):
    title: str
    goal: str
    owner_member_id: str | None = None


class TaskEngine:
    async def create_task(self, request: TaskCreateRequest) -> TaskSummary:
        raise NotImplementedError("TaskEngine contract requires an application implementation")
