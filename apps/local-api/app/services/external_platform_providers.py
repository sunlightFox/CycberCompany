from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from core_types import ExternalPlatformActionPlan
from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.external_platform_repo import ExternalPlatformRepository

FAKE_PROVIDER_TARGET: dict[str, Any] = {
    "platform_key": "fake_platform",
    "display_name": "模拟外部平台",
    "aliases": ["某平台", "模拟平台", "测试平台", "fake platform", "fake_platform"],
    "supported_actions": ["publish_content", "send_message", "read_status"],
    "required_asset_types": ["account"],
    "execution_modes": ["fake_provider", "browser"],
    "risk_defaults": {
        "publish_content": "R4",
        "send_message": "R3",
        "read_status": "R1",
    },
    "metadata": {
        "seeded_for": "phase47_provider_fixture",
        "real_provider": False,
        "provider_registry_owned": True,
    },
}

XIAOHONGSHU_BROWSER_TARGET: dict[str, Any] = {
    "platform_key": "social_xiaohongshu",
    "display_name": "小红书",
    "aliases": ["小红书", "xhs", "rednote"],
    "supported_actions": ["publish_content", "comment_content", "read_status"],
    "required_asset_types": ["account"],
    "execution_modes": ["browser"],
    "risk_defaults": {
        "publish_content": "R4",
        "comment_content": "R3",
        "read_status": "R1",
    },
    "metadata": {
        "seeded_for": "phase_xiaohongshu_browser_flow",
        "real_provider": True,
        "provider_registry_owned": True,
        "real_external_platform_integration": True,
    },
}


@dataclass(frozen=True)
class ProviderInfo:
    provider_key: str
    display_name: str
    execution_modes: list[str]
    status: str = "available"
    real_external_platform_integration: bool = False
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderExecutionRequest:
    plan: ExternalPlatformActionPlan
    repo: ExternalPlatformRepository
    trace_id: str | None = None


@dataclass(frozen=True)
class ProviderExecutionResult:
    status: str
    evidence: dict[str, Any]
    message: str
    next_step: str | None = None
    failure_reason: str | None = None


class ExternalPlatformProvider(Protocol):
    @property
    def info(self) -> ProviderInfo:
        ...

    async def execute(self, request: ProviderExecutionRequest) -> ProviderExecutionResult:
        ...


class ExternalPlatformProviderRegistry:
    def __init__(self, providers: list[ExternalPlatformProvider] | None = None) -> None:
        self._providers: dict[str, ExternalPlatformProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ExternalPlatformProvider) -> None:
        self._providers[provider.info.provider_key] = provider
        for mode in provider.info.execution_modes:
            self._providers.setdefault(mode, provider)

    def get(self, key: str | None) -> ExternalPlatformProvider:
        provider_key = key or "fake_provider"
        provider = self._providers.get(provider_key)
        if provider is None:
            return UnknownExternalPlatformProvider(provider_key=provider_key)
        return provider

    def list(self) -> list[ProviderInfo]:
        seen: set[str] = set()
        providers: list[ProviderInfo] = []
        for provider in self._providers.values():
            key = provider.info.provider_key
            if key in seen:
                continue
            seen.add(key)
            providers.append(provider.info)
        providers.sort(key=lambda item: item.provider_key)
        return providers


class FakeExternalPlatformProvider:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_key="fake_provider",
            display_name="Local fake provider",
            execution_modes=["fake_provider"],
            status="available",
            real_external_platform_integration=False,
            capabilities=["publish_content", "send_message", "read_status"],
            metadata={"fixture_provider": True, "secret_material_visible": False},
        )

    async def execute(self, request: ProviderExecutionRequest) -> ProviderExecutionResult:
        plan = request.plan
        started = time.perf_counter()
        for step in plan.steps:
            step_type = str(step.get("step_type") or "unknown")
            now = utc_now_iso()
            response_summary = {
                "action_status": "ok",
                "provider": "fake_provider",
                "untrusted_external_content": True,
            }
            evidence = {
                "plan_id": plan.plan_id,
                "step_type": step_type,
                "platform_key": plan.platform_key,
                "action_type": plan.action_type,
                "asset_ref": plan.selected_asset_id,
                "handle_ref": plan.selected_handle_id,
                "content_hash": _stable_hash(plan.content_summary or ""),
                "secret_material_visible": False,
                "provider_module": "FakeExternalPlatformProvider",
            }
            if step_type == "submit_publish":
                evidence.update(
                    {
                        "provider_request_id": f"fake:{plan.plan_id}",
                        "status": "published",
                        "url": f"fake-platform://posts/{plan.plan_id}",
                    }
                )
                response_summary.update(
                    {
                        "provider_request_id": f"fake:{plan.plan_id}",
                        "published": True,
                    }
                )
            await request.repo.insert_execution(
                {
                    "execution_id": new_id("epexec"),
                    "plan_id": plan.plan_id,
                    "organization_id": plan.organization_id,
                    "member_id": plan.member_id,
                    "executor": "fake_provider",
                    "step_type": step_type,
                    "status": "completed",
                    "request_summary": {
                        "step_type": step_type,
                        "content_hash": _stable_hash(plan.content_summary or ""),
                    },
                    "response_summary": response_summary,
                    "evidence": redact(evidence),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "trace_id": request.trace_id,
                    "started_at": now,
                    "completed_at": utc_now_iso(),
                    "created_at": now,
                }
            )
        final_evidence: dict[str, Any] = {
            "executor": "fake_provider",
            "provider_module": "FakeExternalPlatformProvider",
            "provider_request_id": f"fake:{plan.plan_id}",
            "action_status": "published"
            if plan.action_type == "publish_content"
            else "completed",
            "url": f"fake-platform://posts/{plan.plan_id}",
            "redaction_policy": "trace_service.redact",
            "secret_material_visible": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        return ProviderExecutionResult(
            status="completed",
            evidence=redact(final_evidence),
            message="模拟平台 provider 已完成受控发布。",
        )


class BrowserExternalPlatformProvider:
    def __init__(self, *, provider_key: str = "browser") -> None:
        self._provider_key = provider_key

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_key=self._provider_key,
            display_name="Browser provider seam",
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


class UnknownExternalPlatformProvider:
    def __init__(self, *, provider_key: str) -> None:
        self._provider_key = provider_key

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_key=self._provider_key,
            display_name="Unknown provider",
            execution_modes=[self._provider_key],
            status="unavailable",
            real_external_platform_integration=False,
            capabilities=[],
            metadata={
                "reason": "provider_not_registered",
                "secret_material_visible": False,
            },
        )

    async def execute(self, request: ProviderExecutionRequest) -> ProviderExecutionResult:
        plan = request.plan
        now = utc_now_iso()
        evidence = {
            "executor": self._provider_key,
            "provider_module": "UnknownExternalPlatformProvider",
            "action_status": "denied",
            "failure_reason": "provider_not_registered",
            "platform_key": plan.platform_key,
            "plan_id": plan.plan_id,
            "secret_material_visible": False,
            "recovery": "register provider before executing this external platform plan",
        }
        await request.repo.insert_execution(
            {
                "execution_id": new_id("epexec"),
                "plan_id": plan.plan_id,
                "organization_id": plan.organization_id,
                "member_id": plan.member_id,
                "executor": self._provider_key,
                "step_type": "provider_boundary",
                "status": "failed",
                "request_summary": {"execution_mode": self._provider_key},
                "response_summary": redact(evidence),
                "evidence": redact(evidence),
                "error_code": "EXTERNAL_PLATFORM_PROVIDER_NOT_REGISTERED",
                "error_summary": "external platform provider is not registered",
                "latency_ms": 0,
                "trace_id": request.trace_id,
                "started_at": now,
                "completed_at": now,
                "created_at": now,
            }
        )
        return ProviderExecutionResult(
            status="failed",
            failure_reason="provider_not_registered",
            evidence=redact(evidence),
            message="指定的外部平台 provider 未注册，未执行外部动作。",
            next_step="register_provider_or_choose_available_provider",
        )


def default_external_platform_provider_registry() -> ExternalPlatformProviderRegistry:
    registry = ExternalPlatformProviderRegistry()
    registry.register(FakeExternalPlatformProvider())
    registry.register(BrowserExternalPlatformProvider(provider_key="browser"))
    return registry


def _stable_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
