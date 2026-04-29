from __future__ import annotations

from typing import Any

from core_types import (
    AssetCategory,
    AssetDetail,
    AssetSummary,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.schemas.assets import AssetCreateRequest, AssetUpdateRequest, AssetVerifyResponse
from app.services.audit import AuditEventService
from app.services.secrets import SecretStore

SECRET_CONFIG_KEYS = {
    "api_key",
    "token",
    "password",
    "cookie",
    "private_key",
    "mnemonic",
    "credential",
    "secret",
}

REQUIRED_CONFIG_KEYS = {
    AssetCategory.BRAIN: {"model_name"},
    AssetCategory.ACCOUNT: {"platform", "username", "auth_type"},
    AssetCategory.WALLET: {"network", "address"},
    AssetCategory.HARDWARE: {"provider", "device_type"},
    AssetCategory.KNOWLEDGE_BASE: {"source_type", "root_uri"},
}


class AssetService:
    def __init__(
        self,
        *,
        repo: AssetRepository,
        secret_store: SecretStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._secrets = secret_store
        self._trace = trace_service
        self._audit = audit_service

    async def create_asset(
        self,
        request: AssetCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> AssetDetail:
        config = _validated_config(request.asset_type, request.config)
        now = utc_now_iso()
        secret_ref = None
        if request.secret_value:
            secret_ref, storage_uri = self._secrets.put_secret(request.secret_value)
            await self._repo.upsert_secret_ref(
                secret_ref=secret_ref,
                organization_id="org_default",
                kind=f"{request.asset_type.value}_secret",
                label=request.display_name,
                storage_uri=storage_uri,
                secret_type=f"{request.asset_type.value}_credential",
                provider=request.provider or "local",
                metadata={"asset_type": request.asset_type.value},
                now=now,
            )
            await self._audit.write_event(
                actor_type="system",
                action="secret_ref.created",
                object_type="secret_ref",
                object_id=secret_ref,
                summary="资产密钥引用已创建",
                risk_level=RiskLevel.R2,
                payload={"secret_ref": secret_ref, "asset_type": request.asset_type.value},
                trace_id=trace_id,
            )
        asset_id = new_id("ast")
        await self._repo.insert_asset(
            {
                **request.model_dump(exclude={"secret_value"}, mode="json"),
                "asset_id": asset_id,
                "organization_id": "org_default",
                "config": config,
                "secret_ref": secret_ref,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._create_default_policies(
            asset_id=asset_id,
            asset_type=request.asset_type,
            organization_id="org_default",
            now=now,
        )
        await self._audit.write_event(
            actor_type="system",
            action="asset.created",
            object_type="asset",
            object_id=asset_id,
            summary="资产已创建",
            risk_level=request.risk_level,
            payload={
                "asset_id": asset_id,
                "asset_type": request.asset_type.value,
                "has_secret": bool(secret_ref),
            },
            trace_id=trace_id,
        )
        return await self.get_asset(asset_id)

    async def list_assets(
        self,
        *,
        asset_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AssetSummary]:
        rows = await self._repo.list_assets(
            organization_id="org_default",
            asset_type=asset_type,
            status=status,
            limit=limit,
        )
        return [AssetSummary(**_asset_summary(row)) for row in rows]

    async def get_asset(self, asset_id: str) -> AssetDetail:
        row = await self._repo.get_asset(asset_id)
        if row is None or row["status"] == "deleted":
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "资产不存在", status_code=404)
        return AssetDetail(**_asset_detail(row))

    async def update_asset(
        self,
        asset_id: str,
        request: AssetUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> AssetDetail:
        existing = await self._repo.get_asset(asset_id)
        if existing is None or existing["status"] == "deleted":
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "资产不存在", status_code=404)
        fields = request.model_dump(exclude_unset=True, exclude={"secret_value"}, mode="json")
        if "config" in fields and fields["config"] is not None:
            fields["config"] = _validated_config(
                AssetCategory(existing["asset_type"]),
                fields["config"],
            )
        if request.secret_value:
            secret_ref = existing.get("secret_ref")
            now_for_secret = utc_now_iso()
            if secret_ref:
                storage_uri = self._secrets.rotate_secret(str(secret_ref), request.secret_value)
            else:
                secret_ref, storage_uri = self._secrets.put_secret(request.secret_value)
            fields["secret_ref"] = secret_ref
            await self._repo.upsert_secret_ref(
                secret_ref=str(secret_ref),
                organization_id=existing["organization_id"],
                kind=f"{existing['asset_type']}_secret",
                label=fields.get("display_name") or existing["display_name"],
                storage_uri=storage_uri,
                secret_type=f"{existing['asset_type']}_credential",
                provider=fields.get("provider") or existing.get("provider") or "local",
                metadata={"asset_type": existing["asset_type"]},
                now=now_for_secret,
            )
            await self._audit.write_event(
                actor_type="system",
                action="secret_ref.rotated",
                object_type="secret_ref",
                object_id=str(secret_ref),
                summary="资产密钥引用已更新",
                risk_level=RiskLevel.R2,
                payload={"secret_ref": secret_ref, "asset_id": asset_id},
                trace_id=trace_id,
            )
        fields["updated_at"] = utc_now_iso()
        await self._repo.update_asset(asset_id, fields)
        await self._audit.write_event(
            actor_type="system",
            action="asset.updated",
            object_type="asset",
            object_id=asset_id,
            summary="资产已更新",
            risk_level=RiskLevel.R1,
            payload={"asset_id": asset_id, "changed_fields": sorted(fields)},
            trace_id=trace_id,
        )
        return await self.get_asset(asset_id)

    async def verify_asset(
        self,
        asset_id: str,
        *,
        trace_id: str | None = None,
    ) -> AssetVerifyResponse:
        asset = await self._repo.get_asset(asset_id)
        if asset is None or asset["status"] == "deleted":
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "资产不存在", status_code=404)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_VERIFY,
            "verify asset",
            metadata={"asset_id": asset_id, "asset_type": asset["asset_type"]},
        )
        try:
            checked = _verify_lightweight(asset)
            now = utc_now_iso()
            await self._repo.update_asset(
                asset_id,
                {"last_verified_at": now, "updated_at": now},
            )
            await self._end_span(span_id, output_data={"checked_actions": checked})
            await self._audit.write_event(
                actor_type="system",
                action="asset.verify",
                object_type="asset",
                object_id=asset_id,
                summary="资产轻量验证完成",
                risk_level=RiskLevel.R1,
                payload={"asset_id": asset_id, "checked_actions": checked},
                trace_id=trace_id,
            )
            return AssetVerifyResponse(
                asset_id=asset_id,
                status="verified",
                message="资产轻量验证完成，未执行高风险动作",
                checked_actions=checked,
            )
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(redact(str(exc)))},
            )
            if isinstance(exc, AppError):
                raise
            raise

    async def set_status(
        self,
        asset_id: str,
        status: str,
        *,
        trace_id: str | None = None,
    ) -> AssetDetail:
        existing = await self._repo.get_asset(asset_id)
        if existing is None or existing["status"] == "deleted":
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "资产不存在", status_code=404)
        now = utc_now_iso()
        fields = {"status": status, "updated_at": now}
        if status == "archived":
            fields["archived_at"] = now
        if status in {"disabled", "archived", "deleted"}:
            revoked = await self._repo.revoke_handles_for_asset(asset_id, revoked_at=now)
            for handle_id in revoked:
                await self._repo.insert_handle_event(
                    {
                        "event_id": new_id("ahe"),
                        "organization_id": existing["organization_id"],
                        "handle_id": handle_id,
                        "event_type": "revoked",
                        "reason": f"asset_{status}",
                        "actor_type": "system",
                        "actor_id": None,
                        "trace_id": trace_id,
                        "metadata": {"asset_id": asset_id},
                        "created_at": now,
                    }
                )
        await self._repo.update_asset(asset_id, fields)
        await self._audit.write_event(
            actor_type="system",
            action=f"asset.{status}",
            object_type="asset",
            object_id=asset_id,
            summary=f"资产状态已更新为 {status}",
            risk_level=RiskLevel.R1,
            payload={"asset_id": asset_id, "status": status},
            trace_id=trace_id,
        )
        return await self.get_asset(asset_id) if status != "deleted" else AssetDetail(
            **_asset_detail({**existing, "status": "deleted"})
        )

    async def _create_default_policies(
        self,
        *,
        asset_id: str,
        asset_type: AssetCategory,
        organization_id: str,
        now: str,
    ) -> None:
        for action, risk in _default_approval_actions(asset_type).items():
            await self._repo.insert_policy(
                {
                    "policy_id": new_id("apol"),
                    "organization_id": organization_id,
                    "asset_id": asset_id,
                    "policy_type": "asset_default",
                    "action": action,
                    "effect": "approval_required",
                    "risk_level": risk.value,
                    "approval_policy": {"required": True},
                    "condition": {},
                    "priority": 100,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            metadata=metadata,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(span_id, status=status, output_data=output_data)


def _validated_config(asset_type: AssetCategory, config: dict[str, Any]) -> dict[str, Any]:
    lowered = {key.lower() for key in config}
    if lowered & SECRET_CONFIG_KEYS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "资产 config 不能包含明文 secret，请使用 secret_value",
            status_code=422,
        )
    required = REQUIRED_CONFIG_KEYS[asset_type]
    missing = sorted(required - set(config))
    if missing:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "资产配置缺少必填字段",
            status_code=422,
            details={"missing": missing, "asset_type": asset_type.value},
        )
    return config


def _verify_lightweight(asset: dict[str, Any]) -> list[str]:
    asset_type = AssetCategory(asset["asset_type"])
    config = asset["config"]
    if asset_type == AssetCategory.WALLET:
        address = str(config.get("address") or "")
        if len(address) < 8:
            raise AppError(ErrorCode.VALIDATION_ERROR, "钱包地址格式不合法", status_code=422)
        return ["address_format", "network_config"]
    if asset_type == AssetCategory.ACCOUNT:
        return ["credential_reference", "profile_read_skipped"]
    if asset_type == AssetCategory.HARDWARE:
        return ["connection_config", "status_query_skipped"]
    if asset_type == AssetCategory.KNOWLEDGE_BASE:
        return ["source_root", "index_permission"]
    if asset_type == AssetCategory.BRAIN:
        return ["model_config"]
    return ["config"]


def _asset_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": row["asset_id"],
        "organization_id": row["organization_id"],
        "asset_type": row["asset_type"],
        "display_name": row["display_name"],
        "provider": row.get("provider"),
        "status": row["status"],
        "sensitivity": row["sensitivity"],
        "visibility": row.get("visibility", "private"),
        "risk_level": row.get("risk_level", "R1"),
        "summary_text": row.get("summary_text"),
        "capabilities": row.get("capabilities", []),
        "has_secret": bool(row.get("secret_ref")),
        "expires_at": row.get("expires_at"),
        "last_verified_at": row.get("last_verified_at"),
        "archived_at": row.get("archived_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _asset_detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_asset_summary(row),
        "owner_scope_type": row.get("owner_scope_type", "member"),
        "owner_scope_id": row.get("owner_scope_id"),
        "config": redact(row.get("config", {})),
        "policy": redact(row.get("policy", {})),
        "metadata": redact(row.get("metadata", {})),
        "secret_ref": None,
    }


def _default_approval_actions(asset_type: AssetCategory) -> dict[str, RiskLevel]:
    if asset_type == AssetCategory.ACCOUNT:
        return {"publish_post": RiskLevel.R4, "delete_content": RiskLevel.R4}
    if asset_type == AssetCategory.WALLET:
        return {"draft_transfer": RiskLevel.R5, "sign_transaction": RiskLevel.R6}
    if asset_type == AssetCategory.HARDWARE:
        return {"control_device": RiskLevel.R4}
    return {}
