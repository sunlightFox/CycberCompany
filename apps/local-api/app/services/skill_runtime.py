from __future__ import annotations

from typing import Any


class SkillRuntime:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def match(self, *args: Any, **kwargs: Any) -> Any:
        return await self._service._match_skills_impl(*args, **kwargs)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        return await self._service._run_skill_impl(*args, **kwargs)

