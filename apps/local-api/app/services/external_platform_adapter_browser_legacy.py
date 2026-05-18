from __future__ import annotations

from typing import Any


class ExternalPlatformBrowserAdapterLegacyRunner:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def compile_plan(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._service._compile_plan_legacy(plan_id, request, trace_id=trace_id)

    async def execute_adapter(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._service._execute_adapter_legacy(plan_id, request, trace_id=trace_id)

    async def discover_adapter(
        self,
        plan_id: str,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._service._discover_adapter_legacy(plan_id, trace_id=trace_id)

    async def resume_after_human(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._service._resume_after_human_legacy(plan_id, request, trace_id=trace_id)
