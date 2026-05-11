from __future__ import annotations

from typing import Any


class SkillInstaller:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def install_bundle(self, *args: Any, **kwargs: Any) -> Any:
        return await self._service._install_bundle_impl(*args, **kwargs)

