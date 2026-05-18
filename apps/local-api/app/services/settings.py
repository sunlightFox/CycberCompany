from __future__ import annotations

from typing import Any

from core_types import ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import utc_now_iso
from app.db.repositories.settings_repo import SettingsRepository
from app.schemas.settings import RuntimeSettings, RuntimeSettingsPatch, RuntimeSettingsResponse
from app.services.audit import AuditEventService


class SettingsService:
    def __init__(
        self,
        *,
        repo: SettingsRepository,
        model_routing_config: dict[str, Any],
        safety_config: dict[str, Any],
        mcp_config: dict[str, Any],
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._model_routing_config = model_routing_config
        self._safety_config = safety_config
        self._mcp_config = mcp_config
        self._trace = trace_service
        self._audit = audit_service

    async def get_settings(
        self,
        *,
        organization_id: str = "org_default",
        trace_id: str | None = None,
    ) -> RuntimeSettingsResponse:
        span_id = await self._start_span(trace_id, "read runtime settings")
        row = await self._repo.get_runtime_settings(organization_id)
        if row is None:
            response = self._default_response(organization_id, trace_id=trace_id)
        else:
            response = RuntimeSettingsResponse(
                setting_id=row["setting_id"],
                organization_id=row["organization_id"],
                settings=RuntimeSettings(**row["settings"]),
                version=int(row["version"]),
                trace_id=row.get("trace_id"),
                updated_by_member_id=row.get("updated_by_member_id"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={"version": response.version, "source": response.source},
            )
        return response

    async def update_settings(
        self,
        patch: RuntimeSettingsPatch,
        *,
        organization_id: str = "org_default",
        trace_id: str | None = None,
    ) -> RuntimeSettingsResponse:
        span_id = await self._start_span(
            trace_id,
            "update runtime settings",
            input_data=patch.model_dump(exclude_none=True, mode="json"),
        )
        try:
            current = await self.get_settings(organization_id=organization_id)
            current_settings = current.settings.model_dump(mode="json")
            patch_data = patch.model_dump(exclude_unset=True, exclude_none=True, mode="json")
            updated_by_member_id = patch_data.pop("updated_by_member_id", None)
            if not patch_data:
                if span_id:
                    await self._trace.end_span(span_id, output_data={"changed": False})
                return current
            if _contains_inline_secret(patch_data):
                raise AppError(
                    ErrorCode.CONFIG_ERROR,
                    "设置中不能包含明文 secret/token/password/cookie/private_key/mnemonic",
                    status_code=422,
                )
            for section, value in patch_data.items():
                if section not in current_settings:
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR,
                        "不支持的设置域",
                        status_code=422,
                        details={"section": section},
                    )
                current_settings[section] = {**current_settings[section], **value}
            settings = RuntimeSettings(**current_settings)
            now = utc_now_iso()
            saved = await self._repo.upsert_runtime_settings(
                setting_id=current.setting_id,
                organization_id=organization_id,
                settings=settings.model_dump(mode="json"),
                updated_by_member_id=updated_by_member_id,
                trace_id=trace_id,
                now=now,
            )
            await self._sync_runtime_backing_settings(settings, now)
            await self._audit.write_event(
                actor_type="member" if updated_by_member_id else "system",
                actor_id=updated_by_member_id,
                action="settings.update",
                object_type="runtime_settings",
                object_id=saved["setting_id"],
                summary="运行时设置已更新",
                risk_level=RiskLevel.R3,
                payload={"changed_sections": sorted(patch_data), "settings": settings.model_dump()},
                trace_id=trace_id,
            )
            if span_id:
                await self._trace.end_span(
                    span_id,
                    output_data={
                        "changed_sections": sorted(patch_data),
                        "version": saved["version"],
                    },
                )
            return RuntimeSettingsResponse(
                setting_id=saved["setting_id"],
                organization_id=saved["organization_id"],
                settings=RuntimeSettings(**saved["settings"]),
                version=int(saved["version"]),
                trace_id=saved.get("trace_id"),
                updated_by_member_id=saved.get("updated_by_member_id"),
                created_at=saved["created_at"],
                updated_at=saved["updated_at"],
            )
        except AppError as exc:
            if span_id:
                await self._trace.end_span(
                    span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"error_code": exc.code},
                    error_code=exc.code,
                )
            raise

    async def _sync_runtime_backing_settings(
        self,
        settings: RuntimeSettings,
        now: str,
    ) -> None:
        await self._repo.upsert_app_setting(
            "model_routing",
            self._model_routing_backing_config(settings),
            now,
        )
        await self._repo.upsert_app_setting("safety", settings.safety.model_dump(mode="json"), now)
        await self._repo.upsert_app_setting("mcp", settings.mcp.model_dump(mode="json"), now)
        await self._repo.upsert_app_setting("memory", settings.memory.model_dump(mode="json"), now)
        await self._repo.upsert_app_setting("vector", settings.vector.model_dump(mode="json"), now)

    def _default_response(
        self,
        organization_id: str,
        *,
        trace_id: str | None,
    ) -> RuntimeSettingsResponse:
        now = utc_now_iso()
        return RuntimeSettingsResponse(
            setting_id=f"settings_{organization_id}",
            organization_id=organization_id,
            settings=self._default_settings(),
            version=0,
            source="config_defaults",
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )

    def _default_settings(self) -> RuntimeSettings:
        routing = _mapping(self._model_routing_config.get("routing"))
        privacy = _mapping(routing.get("privacy"))
        safety_risk = _mapping(self._safety_config.get("risk"))
        mcp = _mapping(self._mcp_config.get("mcp") or self._mcp_config)
        return RuntimeSettings(
            model_routing={
                "default_route": str(routing.get("default") or "local_main"),
                "allow_cloud_fallback": bool(
                    _mapping(privacy.get("medium")).get("allow_cloud", True)
                ),
                "high_privacy_allow_cloud": bool(
                    _mapping(privacy.get("high")).get("allow_cloud", False)
                ),
                "medium_privacy_allow_cloud": bool(
                    _mapping(privacy.get("medium")).get("allow_cloud", True)
                ),
                "reserved_output_tokens": int(routing.get("reserved_output_tokens") or 1024),
                "context_budget_tokens": int(routing.get("context_budget_tokens") or 8192),
            },
            safety={
                "require_confirmation": list(safety_risk.get("require_confirmation") or []),
                "deny_paths": list(safety_risk.get("deny_paths") or []),
                "terminal_policy_profile": str(
                    _mapping(safety_risk.get("sandbox")).get(
                        "terminal_policy_profile",
                        "task_artifact_sandbox",
                    )
                ),
                "approval_profile": str(
                    safety_risk.get("approval_profile") or "balanced_personal"
                ),
                "governance_mode": str(safety_risk.get("governance_mode") or "smooth"),
                "chat_visible_redaction": str(
                    safety_risk.get("chat_visible_redaction")
                    or ("relaxed" if safety_risk.get("governance_mode") == "smooth" else "strict")
                ),
                "approval_policy": _mapping(safety_risk.get("approval_policy")),
            },
            vector={
                "provider": "chroma",
                "enabled": True,
                "degraded_fallback": "fts",
            },
            mcp={
                "enabled": bool(mcp.get("enabled", False)),
                "allowed_stdio_commands": list(mcp.get("allowed_stdio_commands") or []),
                "blocked_stdio_markers": list(mcp.get("blocked_stdio_markers") or []),
                "default_unknown_tool_status": str(
                    mcp.get("default_unknown_tool_status") or "disabled"
                ),
            },
            memory={
                "implicit_extraction_enabled": True,
                "candidate_review_threshold": 0.55,
            },
        )

    def _model_routing_backing_config(self, settings: RuntimeSettings) -> dict[str, Any]:
        backing = dict(self._model_routing_config)
        routing = dict(_mapping(backing.get("routing")))
        privacy = dict(_mapping(routing.get("privacy")))
        high = dict(_mapping(privacy.get("high")))
        medium = dict(_mapping(privacy.get("medium")))
        low = dict(_mapping(privacy.get("low")))
        model = settings.model_routing
        routing["default"] = model.default_route
        routing["reserved_output_tokens"] = model.reserved_output_tokens
        routing["context_budget_tokens"] = model.context_budget_tokens
        high["allow_cloud"] = model.high_privacy_allow_cloud
        medium["allow_cloud"] = model.medium_privacy_allow_cloud
        medium.setdefault("fallback", "cloud_strong" if model.allow_cloud_fallback else None)
        if not model.allow_cloud_fallback:
            medium.pop("fallback", None)
        low.setdefault("allow_cloud", True)
        privacy["high"] = high
        privacy["medium"] = medium
        privacy["low"] = low
        routing["privacy"] = privacy
        backing["routing"] = routing
        return redact(backing)

    async def _start_span(
        self,
        trace_id: str | None,
        name: str,
        input_data: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.CONFIG_LOAD,
            name=name,
            input_data=redact(input_data or {}),
        )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _contains_inline_secret(value: Any) -> bool:
    markers = ("api_key=", "token=", "password=", "cookie=", "private_key", "mnemonic")
    if isinstance(value, dict):
        return any(
            any(marker.rstrip("=").lower() == str(key).lower() for marker in markers)
            or _contains_inline_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_inline_secret(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in markers)
    return False
