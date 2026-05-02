from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core_types import (
    ErrorCode,
    RiskLevel,
    SkillRepositoryEntry,
    SkillRepositoryRecord,
    SkillRepositorySyncRun,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_repository_repo import SkillRepositoryRepository
from app.schemas.skills import SkillRepositoryPatchRequest, SkillRepositoryUpsertRequest
from app.services.audit import AuditEventService

INDEX_SCHEMA_VERSION = "skill_repository.index.v1"
SUPPORTED_SOURCE_TYPES = {
    "archive_url",
    "skill_md_url",
    "github_path",
    "local_directory",
    "local_archive",
}


class SkillRepositoryService:
    def __init__(
        self,
        *,
        repo: SkillRepositoryRepository,
        config: dict[str, Any],
        root_dir: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._config = config
        self._root_dir = root_dir
        self._trace = trace_service
        self._audit = audit_service

    async def ensure_configured(self, *, trace_id: str | None = None) -> None:
        settings = _skill_config(self._config)
        default_repository_id = str(settings.get("default_repository_id") or "clawhub")
        repositories = settings.get("repositories") or []
        now = utc_now_iso()
        for index, item in enumerate(repositories):
            if not isinstance(item, dict):
                continue
            repository_id = _safe_id(str(item.get("repository_id") or item.get("id") or ""))
            if not repository_id:
                continue
            await self.upsert_repository(
                repository_id,
                SkillRepositoryUpsertRequest(
                    display_name=str(item.get("display_name") or repository_id),
                    provider=str(item.get("provider") or "index_json"),
                    index_uri=item.get("index_uri"),
                    base_uri=item.get("base_uri"),
                    auth=_redacted_auth(item.get("auth") or {}),
                    priority=int(item.get("priority") or (100 + index)),
                    is_default=repository_id == default_repository_id,
                    trust_level=str(item.get("trust_level") or "restricted"),
                    status="enabled" if bool(item.get("enabled", True)) else "disabled",
                    config=dict(item.get("config") or {}),
                ),
                trace_id=trace_id,
                audit=False,
                now=now,
            )

    async def upsert_repository(
        self,
        repository_id: str,
        request: SkillRepositoryUpsertRequest,
        *,
        trace_id: str | None = None,
        audit: bool = True,
        now: str | None = None,
    ) -> SkillRepositoryRecord:
        repository_id = _safe_id(repository_id)
        if not repository_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "repository_id 不能为空", status_code=422)
        _validate_auth(request.auth)
        current = await self._repo.get_repository(repository_id)
        timestamp = now or utc_now_iso()
        if request.is_default:
            await self._repo.clear_default()
        await self._repo.upsert_repository(
            {
                "repository_id": repository_id,
                "organization_id": "org_default",
                "display_name": request.display_name,
                "provider": request.provider,
                "index_uri": request.index_uri,
                "base_uri": request.base_uri,
                "auth": request.auth,
                "priority": request.priority,
                "is_default": request.is_default,
                "trust_level": request.trust_level,
                "status": request.status,
                "config": request.config,
                "trace_id": trace_id,
                "created_at": current["created_at"] if current else timestamp,
                "updated_at": timestamp,
            }
        )
        if audit:
            await self._audit.write_event(
                actor_type="system",
                action="skill_repository.upserted",
                object_type="skill_repository",
                object_id=repository_id,
                summary="Skill 仓库源已配置",
                risk_level=RiskLevel.R2,
                payload={"repository_id": repository_id, "provider": request.provider},
                trace_id=trace_id,
            )
        return await self.require_repository(repository_id)

    async def patch_repository(
        self,
        repository_id: str,
        request: SkillRepositoryPatchRequest,
        *,
        trace_id: str | None = None,
    ) -> SkillRepositoryRecord:
        row = await self.require_repository(repository_id)
        data = request.model_dump(exclude_unset=True, exclude_none=True, mode="json")
        if not data:
            return row
        if "auth" in data:
            _validate_auth(data["auth"])
        if data.get("is_default") is True:
            await self._repo.clear_default()
        data["updated_at"] = utc_now_iso()
        data["trace_id"] = trace_id
        await self._repo.update_repository(repository_id, data)
        await self._audit.write_event(
            actor_type="system",
            action="skill_repository.updated",
            object_type="skill_repository",
            object_id=repository_id,
            summary="Skill 仓库源已更新",
            risk_level=RiskLevel.R2,
            payload={"fields": sorted(data.keys()), "repository_id": repository_id},
            trace_id=trace_id,
        )
        return await self.require_repository(repository_id)

    async def disable_repository(
        self,
        repository_id: str,
        *,
        trace_id: str | None = None,
    ) -> SkillRepositoryRecord:
        return await self.patch_repository(
            repository_id,
            SkillRepositoryPatchRequest(status="disabled"),
            trace_id=trace_id,
        )

    async def require_repository(self, repository_id: str) -> SkillRepositoryRecord:
        row = await self._repo.get_repository(repository_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "Skill 仓库源不存在", status_code=404)
        return SkillRepositoryRecord(**row)

    async def list_repositories(self) -> list[SkillRepositoryRecord]:
        return [SkillRepositoryRecord(**row) for row in await self._repo.list_repositories()]

    async def refresh_repository(
        self,
        repository_id: str,
        *,
        trace_id: str | None = None,
    ) -> tuple[SkillRepositoryRecord, SkillRepositorySyncRun, int]:
        repository = await self.require_repository(repository_id)
        started_at = utc_now_iso()
        sync_run_id = new_id("skrsync")
        try:
            packages = self._load_index(repository)
            entries = [
                _entry_from_package(
                    repository_id=repository.repository_id,
                    package=package,
                    root_dir=self._root_dir,
                    indexed_at=started_at,
                )
                for package in packages
            ]
            await self._repo.replace_entries(repository.repository_id, entries)
            completed_at = utc_now_iso()
            await self._repo.update_repository(
                repository.repository_id,
                {
                    "last_refresh_at": completed_at,
                    "last_error_code": None,
                    "last_error_summary": None,
                    "updated_at": completed_at,
                    "trace_id": trace_id,
                },
            )
            await self._repo.insert_sync_run(
                {
                    "sync_run_id": sync_run_id,
                    "organization_id": "org_default",
                    "repository_id": repository.repository_id,
                    "status": "completed",
                    "indexed_count": len(entries),
                    "trace_id": trace_id,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "created_at": started_at,
                }
            )
            sync_run = await self._repo.get_sync_run(sync_run_id)
            return (
                await self.require_repository(repository.repository_id),
                SkillRepositorySyncRun(**(sync_run or {})),
                len(entries),
            )
        except Exception as exc:
            completed_at = utc_now_iso()
            error_code = getattr(exc, "code", ErrorCode.PLUGIN_VALIDATE_FAILED.value)
            await self._repo.update_repository(
                repository.repository_id,
                {
                    "last_error_code": error_code,
                    "last_error_summary": str(redact(str(exc))),
                    "updated_at": completed_at,
                    "trace_id": trace_id,
                },
            )
            await self._repo.insert_sync_run(
                {
                    "sync_run_id": sync_run_id,
                    "organization_id": "org_default",
                    "repository_id": repository.repository_id,
                    "status": "failed",
                    "indexed_count": 0,
                    "error_code": error_code,
                    "error_summary": str(redact(str(exc))),
                    "trace_id": trace_id,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "created_at": started_at,
                }
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 仓库索引刷新失败",
                status_code=422,
            ) from exc

    async def refresh_all(self, *, trace_id: str | None = None) -> None:
        for repository in await self.list_repositories():
            if repository.status == "enabled":
                await self.refresh_repository(repository.repository_id, trace_id=trace_id)

    async def search(
        self,
        *,
        query: str | None = None,
        repository_id: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[SkillRepositoryEntry]:
        rows = await self._repo.search_entries(
            query=query.strip() if query else None,
            repository_id=repository_id,
            tag=tag.strip() if tag else None,
            limit=max(1, min(int(limit), 100)),
        )
        return [SkillRepositoryEntry(**row) for row in rows]

    async def resolve_repository_ref(
        self,
        source_uri: str,
        *,
        repository_id: str | None = None,
    ) -> SkillRepositoryEntry:
        repo_id, package_ref = _split_repository_ref(source_uri, repository_id=repository_id)
        if not repo_id:
            default = await self._repo.get_default_repository()
            if default is None:
                raise AppError(ErrorCode.NOT_FOUND, "没有可用默认 Skill 仓库源", status_code=404)
            repo_id = str(default["repository_id"])
        entry = await self._repo.get_entry(repo_id, package_ref)
        if entry is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "Skill 仓库条目不存在",
                status_code=404,
                details={"repository_id": repo_id, "package_ref": package_ref},
            )
        return SkillRepositoryEntry(**entry)

    def _load_index(self, repository: SkillRepositoryRecord) -> list[dict[str, Any]]:
        if repository.provider != "index_json":
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "当前阶段仅支持 index_json Skill 仓库源",
                status_code=422,
            )
        if not repository.index_uri:
            raise AppError(ErrorCode.VALIDATION_ERROR, "index_uri 必填", status_code=422)
        path = _resolve_local_uri(self._root_dir, repository.index_uri)
        if not path.exists() or not path.is_file():
            raise AppError(ErrorCode.NOT_FOUND, "Skill 仓库索引不存在", status_code=404)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 仓库索引必须是对象",
                status_code=422,
            )
        if data.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 仓库索引 schema_version 不支持",
                status_code=422,
            )
        packages = data.get("packages")
        if not isinstance(packages, list):
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 仓库索引 packages 必须是数组",
                status_code=422,
            )
        return [package for package in packages if isinstance(package, dict)]


def _skill_config(config: dict[str, Any]) -> dict[str, Any]:
    if "skills" in config and isinstance(config["skills"], dict):
        return dict(config["skills"])
    return dict(config)


def _entry_from_package(
    *,
    repository_id: str,
    package: dict[str, Any],
    root_dir: Path,
    indexed_at: str,
) -> dict[str, Any]:
    package_ref = str(package.get("package_ref") or "").strip()
    bundle_id = _safe_id(str(package.get("bundle_id") or package_ref))
    display_name = str(package.get("display_name") or bundle_id)
    raw_source = package.get("source")
    source: dict[str, Any] = dict(raw_source) if isinstance(raw_source, dict) else {}
    source_type = str(source.get("type") or "")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            "Skill 仓库条目 source.type 不支持",
            status_code=422,
            details={"source_type": source_type, "package_ref": package_ref},
        )
    source = _normalize_source(source, root_dir=root_dir)
    tags = [str(item) for item in package.get("tags") or []]
    keywords = [str(item) for item in package.get("keywords") or []]
    if not package_ref or not bundle_id:
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            "Skill 仓库条目缺少 package_ref/bundle_id",
            status_code=422,
        )
    search_text = " ".join(
        [
            repository_id,
            package_ref,
            bundle_id,
            display_name,
            str(package.get("description") or ""),
            str(package.get("author") or ""),
            " ".join(tags),
            " ".join(keywords),
        ]
    ).lower()
    return {
        "entry_id": f"skrent_{_hash_text(repository_id + ':' + package_ref)[:24]}",
        "organization_id": "org_default",
        "repository_id": repository_id,
        "package_ref": package_ref,
        "bundle_id": bundle_id,
        "display_name": display_name,
        "description": package.get("description"),
        "version": package.get("version"),
        "author": package.get("author"),
        "tags": tags,
        "keywords": keywords,
        "source": source,
        "checksum": package.get("checksum"),
        "trust_level": str(package.get("trust_level") or "restricted"),
        "search_text": search_text,
        "status": "active",
        "indexed_at": indexed_at,
        "updated_at": indexed_at,
    }


def _normalize_source(source: dict[str, Any], *, root_dir: Path) -> dict[str, Any]:
    normalized = dict(source)
    source_type = str(normalized.get("type") or "")
    uri = normalized.get("uri")
    if source_type in {"local_directory", "local_archive"} and isinstance(uri, str):
        normalized["uri"] = str(_resolve_local_uri(root_dir, uri))
    return normalized


def _resolve_local_uri(root_dir: Path, uri: str) -> Path:
    path = Path(uri).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root_dir / path).resolve()


def _split_repository_ref(source_uri: str, *, repository_id: str | None) -> tuple[str | None, str]:
    value = source_uri.removeprefix("repository://").strip()
    if ":" in value:
        repo_id, package_ref = value.split(":", 1)
        return _safe_id(repo_id), package_ref.strip()
    if "/" in value and repository_id:
        return _safe_id(repository_id), value
    return repository_id, value


def _redacted_auth(value: Any) -> dict[str, Any]:
    auth = dict(value) if isinstance(value, dict) else {}
    _validate_auth(auth)
    return auth


def _validate_auth(auth: dict[str, Any]) -> None:
    forbidden = {"api_key", "token", "password", "secret", "private_key", "cookie"}
    if forbidden.intersection({key.lower() for key in auth}):
        raise AppError(
            ErrorCode.CONFIG_ERROR,
            "Skill 仓库认证只能使用 env_ref 或 secret_ref",
            status_code=422,
        )
    for key in auth:
        if key not in {"env_ref", "secret_ref"}:
            raise AppError(
                ErrorCode.CONFIG_ERROR,
                "Skill 仓库认证字段不支持",
                status_code=422,
                details={"field": key},
            )


def _safe_id(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9.-]+", "-", lowered).strip("-")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
