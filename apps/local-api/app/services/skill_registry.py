from __future__ import annotations

from typing import Any


class SkillRegistry:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def list_skills(self, *args: Any, **kwargs: Any) -> Any:
        return await self._service._list_skills_impl(*args, **kwargs)

    async def list_index(self, status: str | None = None) -> list[dict[str, Any]]:
        skills = await self.list_skills(status=status)
        items: list[dict[str, Any]] = []
        for skill in skills:
            data = skill.model_dump(mode="json") if hasattr(skill, "model_dump") else dict(skill)
            items.append(
                {
                    "skill_id": data.get("skill_id"),
                    "bundle_id": data.get("bundle_id"),
                    "display_name": data.get("display_name"),
                    "status": data.get("status"),
                    "required_tools": list(data.get("required_tools") or []),
                    "required_assets": list(data.get("required_assets") or []),
                    "tags": list(data.get("tags") or []),
                }
            )
        return items

    async def replay_skill_runs(self, task_id: str) -> list[dict[str, Any]]:
        return await self._service._replay_skill_runs_impl(task_id)

    async def replay_plugin_events(self, task_id: str) -> list[dict[str, Any]]:
        return await self._service._replay_plugin_events_impl(task_id)

