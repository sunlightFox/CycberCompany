from __future__ import annotations

from time import perf_counter

from brain.adapters import CancelToken, ModelAdapterError, ModelChatRequest, OpenAICompatibleClient
from core_types import ErrorCode, RiskLevel

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.brain_repo import BrainRepository
from app.schemas.brain import (
    BrainCreateRequest,
    BrainResponse,
    BrainUpdateRequest,
    BrainVerifyResponse,
)
from app.services.audit import AuditEventService
from app.services.secrets import SecretStore


class BrainService:
    def __init__(
        self,
        repo: BrainRepository,
        secret_store: SecretStore,
        audit: AuditEventService,
    ) -> None:
        self._repo = repo
        self._secrets = secret_store
        self._audit = audit

    async def list_brains(self) -> list[BrainResponse]:
        return [BrainResponse(**row) for row in await self._repo.list_brains()]

    async def get_brain(self, brain_id: str) -> BrainResponse | None:
        row = await self._repo.get_brain(brain_id)
        return BrainResponse(**row) if row else None

    async def create_brain(
        self,
        request: BrainCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrainResponse:
        self._validate_brain_payload(
            is_local=request.is_local,
            endpoint=request.endpoint,
            model_name=request.model_name,
            api_key=request.api_key,
            api_key_ref=request.api_key_ref,
        )
        now = utc_now_iso()
        api_key_ref = request.api_key_ref
        if request.api_key:
            api_key_ref, storage_uri = self._secrets.put_secret(request.api_key)
            await self._repo.insert_secret_ref(
                secret_ref=api_key_ref,
                kind="model_api_key",
                label=request.display_name,
                storage_uri=storage_uri,
                created_at=now,
            )
            await self._audit.write_event(
                actor_type="system",
                action="secret_ref.created",
                object_type="secret_ref",
                object_id=api_key_ref,
                summary="模型密钥引用已创建",
                risk_level=RiskLevel.R2,
                payload={"secret_ref": api_key_ref},
                trace_id=trace_id,
            )
        brain_id = new_id("brn")
        await self._repo.insert_brain(
            {
                **request.model_dump(exclude={"api_key", "api_key_ref"}),
                "brain_id": brain_id,
                "api_key_ref": api_key_ref,
                "status": "configured" if request.enabled else "disabled",
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._audit.write_event(
            actor_type="system",
            action="brain.created",
            object_type="brain",
            object_id=brain_id,
            summary="大脑配置已创建",
            risk_level=RiskLevel.R1,
            payload={
                "brain_id": brain_id,
                "provider": request.provider,
                "has_api_key": bool(api_key_ref),
            },
            trace_id=trace_id,
        )
        created = await self.get_brain(brain_id)
        if created is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "大脑创建后无法读取", status_code=500)
        return created

    async def update_brain(
        self,
        brain_id: str,
        request: BrainUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrainResponse:
        existing = await self._repo.get_brain(brain_id)
        if existing is None:
            raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
        fields_set = request.model_fields_set
        next_is_local = (
            request.is_local
            if "is_local" in fields_set and request.is_local is not None
            else bool(existing["is_local"])
        )
        next_endpoint = request.endpoint if "endpoint" in fields_set else existing["endpoint"]
        next_model_name = (
            request.model_name if "model_name" in fields_set else existing["model_name"]
        )
        next_api_key_ref = (
            request.api_key_ref if "api_key_ref" in fields_set else existing.get("api_key_ref")
        )
        self._validate_brain_payload(
            is_local=next_is_local,
            endpoint=next_endpoint,
            model_name=next_model_name,
            api_key=request.api_key,
            api_key_ref=next_api_key_ref,
        )
        fields = request.model_dump(exclude_unset=True, exclude={"api_key"})
        if request.api_key:
            api_key_ref = fields.get("api_key_ref") or existing.get("api_key_ref")
            now_for_secret = utc_now_iso()
            if api_key_ref:
                storage_uri = self._secrets.rotate_secret(str(api_key_ref), request.api_key)
            else:
                api_key_ref, storage_uri = self._secrets.put_secret(request.api_key)
            fields["api_key_ref"] = api_key_ref
            await self._repo.insert_secret_ref(
                secret_ref=str(api_key_ref),
                kind="model_api_key",
                label=fields.get("display_name") or existing["display_name"],
                storage_uri=storage_uri,
                created_at=now_for_secret,
            )
            await self._audit.write_event(
                actor_type="system",
                action="secret_ref.rotated",
                object_type="secret_ref",
                object_id=str(api_key_ref),
                summary="模型密钥引用已更新",
                risk_level=RiskLevel.R2,
                payload={"secret_ref": api_key_ref},
                trace_id=trace_id,
            )
        if "enabled" in fields:
            fields["status"] = "configured" if fields.pop("enabled") else "disabled"
        fields["updated_at"] = utc_now_iso()
        await self._repo.update_brain(brain_id, fields)
        await self._audit.write_event(
            actor_type="system",
            action="brain.updated",
            object_type="brain",
            object_id=brain_id,
            summary="大脑配置已更新",
            risk_level=RiskLevel.R1,
            payload={"brain_id": brain_id, "changed_fields": sorted(fields)},
            trace_id=trace_id,
        )
        updated = await self.get_brain(brain_id)
        if updated is None:
            raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
        return updated

    async def set_enabled(
        self,
        brain_id: str,
        enabled: bool,
        *,
        trace_id: str | None = None,
    ) -> BrainResponse:
        existing = await self._repo.get_brain(brain_id)
        if existing is None:
            raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
        await self._repo.update_brain(
            brain_id,
            {"status": "configured" if enabled else "disabled", "updated_at": utc_now_iso()},
        )
        await self._audit.write_event(
            actor_type="system",
            action="brain.enabled" if enabled else "brain.disabled",
            object_type="brain",
            object_id=brain_id,
            summary="大脑已启用" if enabled else "大脑已禁用",
            risk_level=RiskLevel.R1,
            payload={"brain_id": brain_id},
            trace_id=trace_id,
        )
        updated = await self.get_brain(brain_id)
        if updated is None:
            raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
        return updated

    async def verify_brain(
        self,
        brain_id: str,
        *,
        trace_id: str | None = None,
    ) -> BrainVerifyResponse:
        brain = await self._repo.get_brain(brain_id)
        if brain is None:
            raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
        if not brain.get("endpoint"):
            return await self._write_verify_result(
                brain_id,
                status="unhealthy",
                error_code=ErrorCode.MODEL_NOT_CONFIGURED.value,
                message="endpoint 未配置",
                latency_ms=None,
                trace_id=trace_id,
            )

        started = perf_counter()
        client = OpenAICompatibleClient(
            str(brain["endpoint"]),
            self._secrets.get_secret(brain.get("api_key_ref")),
        )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_output_tokens=8,
            top_p=1,
            timeout_seconds=min(int(brain.get("timeout_seconds") or 180), 30),
            stream=False,
            trace_id=trace_id or "trc_verify",
            turn_id="turn_verify",
            route_id="route_verify",
            privacy_level="low",
            retry_count=0,
        )
        try:
            await client.complete_chat(request, CancelToken())
        except ModelAdapterError as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            return await self._write_verify_result(
                brain_id,
                status="unhealthy",
                error_code=exc.code.value,
                message=exc.message,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )
        latency_ms = int((perf_counter() - started) * 1000)
        return await self._write_verify_result(
            brain_id,
            status="healthy",
            error_code=None,
            message="模型连接验证成功",
            latency_ms=latency_ms,
            trace_id=trace_id,
        )

    async def _write_verify_result(
        self,
        brain_id: str,
        *,
        status: str,
        error_code: str | None,
        message: str,
        latency_ms: int | None,
        trace_id: str | None,
    ) -> BrainVerifyResponse:
        now = utc_now_iso()
        await self._repo.update_brain(
            brain_id,
            {
                "status": status,
                "last_verified_at": now,
                "last_error_code": error_code,
                "last_error_message": None if status == "healthy" else message,
                "latency_ms": latency_ms,
                "updated_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="system",
            action="brain.verify",
            object_type="brain",
            object_id=brain_id,
            summary=message,
            risk_level=RiskLevel.R1,
            payload={"brain_id": brain_id, "status": status, "error_code": error_code},
            trace_id=trace_id,
        )
        return BrainVerifyResponse(
            brain_id=brain_id,
            status=status,
            latency_ms=latency_ms,
            error_code=error_code,
            message=message,
        )

    def _validate_brain_payload(
        self,
        *,
        is_local: bool,
        endpoint: str | None,
        model_name: str | None,
        api_key: str | None,
        api_key_ref: str | None,
    ) -> None:
        if not endpoint:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "endpoint 必须配置",
                status_code=422,
            )
        if not model_name:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "model_name 必须配置",
                status_code=422,
            )
        if not is_local and not (api_key or api_key_ref):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "云端或远程大脑必须提供 api_key 或 api_key_ref",
                status_code=422,
            )
