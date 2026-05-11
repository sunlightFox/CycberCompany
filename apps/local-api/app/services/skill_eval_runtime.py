from __future__ import annotations

from typing import Any


class SkillEvalRuntime:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def run_eval(self, *args: Any, **kwargs: Any) -> Any:
        return await self._service._run_eval_impl(*args, **kwargs)

