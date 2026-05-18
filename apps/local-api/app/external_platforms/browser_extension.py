from __future__ import annotations

from typing import Any

from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.services.external_platform_adapter_browser_legacy import (
    ExternalPlatformBrowserAdapterLegacyRunner,
)
from app.services.external_platform_extensions import (
    ExternalPlatformAdapterHandlerProtocol,
    ExternalPlatformExtensionDefinition,
    ExternalPlatformExtensionManifest,
    ExternalPlatformRuntimeContext,
)
from app.services.external_platform_providers import (
    ExternalPlatformProvider,
    ProviderExecutionRequest,
    ProviderExecutionResult,
    ProviderInfo,
)


class BrowserExternalPlatformProvider(ExternalPlatformProvider):
    def __init__(self, *, provider_key: str = "browser", display_name: str = "Browser provider seam") -> None:
        self._provider_key = provider_key
        self._display_name = display_name

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_key=self._provider_key,
            display_name=self._display_name,
            execution_modes=[self._provider_key],
            status="degraded",
            real_external_platform_integration=False,
            capabilities=["browser_execution_mode_boundary"],
            metadata={
                "reason": "browser provider requires platform-specific Skill/MCP adapter",
                "secret_material_visible": False,
            },
        )

    async def execute(self, request: ProviderExecutionRequest) -> ProviderExecutionResult:
        plan = request.plan
        now = utc_now_iso()
        evidence = {
            "executor": self._provider_key,
            "provider_module": "BrowserExternalPlatformProvider",
            "action_status": "degraded",
            "failure_reason": "browser_provider_not_configured",
            "platform_key": plan.platform_key,
            "plan_id": plan.plan_id,
            "secret_material_visible": False,
            "recovery": "register a platform-specific provider or use fake_provider for tests",
        }
        await request.repo.insert_execution(
            {
                "execution_id": new_id("epexec"),
                "plan_id": plan.plan_id,
                "organization_id": plan.organization_id,
                "member_id": plan.member_id,
                "executor": self._provider_key,
                "step_type": "provider_boundary",
                "status": "degraded",
                "request_summary": {"execution_mode": self._provider_key},
                "response_summary": redact(evidence),
                "evidence": redact(evidence),
                "error_code": "EXTERNAL_PLATFORM_PROVIDER_UNAVAILABLE",
                "error_summary": "browser provider is not configured",
                "latency_ms": 0,
                "trace_id": request.trace_id,
                "started_at": now,
                "completed_at": now,
                "created_at": now,
            }
        )
        return ProviderExecutionResult(
            status="failed",
            failure_reason="browser_provider_not_configured",
            evidence=redact(evidence),
            message="浏览器 provider 尚未配置具体平台适配器，未执行外部提交。",
            next_step="register_provider_or_choose_fake_provider",
        )


class BrowserAdapterHandler(ExternalPlatformAdapterHandlerProtocol):
    def __init__(self, service: Any) -> None:
        self._legacy = ExternalPlatformBrowserAdapterLegacyRunner(service)

    async def compile_plan(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._legacy.compile_plan(plan_id, request, trace_id=trace_id)

    async def execute_adapter(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._legacy.execute_adapter(plan_id, request, trace_id=trace_id)

    async def discover_adapter(
        self,
        plan_id: str,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._legacy.discover_adapter(plan_id, trace_id=trace_id)

    async def resume_after_human(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._legacy.resume_after_human(plan_id, request, trace_id=trace_id)


def _build_providers(_context: ExternalPlatformRuntimeContext) -> list[ExternalPlatformProvider]:
    return [
        BrowserExternalPlatformProvider(provider_key="browser"),
        BrowserExternalPlatformProvider(provider_key="mcp_adapter", display_name="MCP adapter provider seam"),
    ]


BROWSER_EXTERNAL_PLATFORM_EXTENSION = ExternalPlatformExtensionDefinition(
    manifest=ExternalPlatformExtensionManifest(
        id="browser",
        execution_modes=("browser", "mcp_adapter", "mcp"),
    ),
    provider_factory=_build_providers,
    adapter_handler_factory=lambda service: BrowserAdapterHandler(service),
)
