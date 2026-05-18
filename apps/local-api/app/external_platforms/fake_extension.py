from __future__ import annotations

import hashlib
import time
from typing import Any

from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.services.external_platform_extensions import (
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

FAKE_PROVIDER_TARGET: dict[str, Any] = {
    "target_id": "ept_fake_platform",
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


class FakeExternalPlatformProvider(ExternalPlatformProvider):
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
            "action_status": "published" if plan.action_type == "publish_content" else "completed",
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


def _build_providers(_context: ExternalPlatformRuntimeContext) -> list[ExternalPlatformProvider]:
    return [FakeExternalPlatformProvider()]


FAKE_EXTERNAL_PLATFORM_EXTENSION = ExternalPlatformExtensionDefinition(
    manifest=ExternalPlatformExtensionManifest(
        id="fake",
        platform_keys=("fake_platform",),
        execution_modes=("fake_provider",),
        seeded_targets=(FAKE_PROVIDER_TARGET,),
        display_aliases=("模拟外部平台", "测试平台"),
        canonical_aliases=("fake platform", "fake_platform"),
        action_markers={
            "publish_content": ("发布", "publish", "post"),
            "send_message": ("发消息", "发送", "message"),
            "read_status": ("查看", "read", "status"),
        },
        content_markers=("内容：", "内容:", "正文：", "正文:"),
        generic_platform_markers=("平台", "测试平台", "fake platform"),
    ),
    provider_factory=_build_providers,
)


def _stable_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
