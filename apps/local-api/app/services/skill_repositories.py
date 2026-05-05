from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core_types import (
    ErrorCode,
    RiskLevel,
    SkillDependencyEdge,
    SkillGrowthCandidate,
    SkillMarketplaceHealthRecord,
    SkillMarketplaceInstallRecord,
    SkillMarketplacePackageDetail,
    SkillRepositoryEntry,
    SkillRepositoryRecord,
    SkillRepositorySyncRun,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.skill_repository_repo import SkillRepositoryRepository
from app.schemas.skills import (
    SkillGrowthCandidateConsolidateRequest,
    SkillRepositoryPatchRequest,
    SkillRepositoryUpsertRequest,
)
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
        skill_repo: SkillMcpRepository | None = None,
    ) -> None:
        self._repo = repo
        self._skill_repo = skill_repo
        self._config = config
        self._root_dir = root_dir
        self._trace = trace_service
        self._audit = audit_service

    def set_skill_repo(self, skill_repo: SkillMcpRepository) -> None:
        self._skill_repo = skill_repo

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
        return [SkillRepositoryEntry(**_public_entry(row)) for row in rows]

    async def package_detail(
        self,
        *,
        repository_id: str,
        package_ref: str,
    ) -> SkillMarketplacePackageDetail:
        entry = await self._repo.get_entry(repository_id, package_ref)
        if entry is None:
            raise AppError(ErrorCode.NOT_FOUND, "Skill 市场条目不存在", status_code=404)
        versions = await self._repo.list_package_versions(
            repository_id=repository_id,
            package_ref=package_ref,
        )
        health = await self._repo.latest_health_record(
            repository_id=repository_id,
            package_ref=package_ref,
        )
        installs = await self._repo.list_install_records(
            repository_id=repository_id,
            package_ref=package_ref,
            limit=20,
        )
        dependencies = await self._repo.list_dependency_edges(
            source_type="marketplace_package",
            source_id=f"{repository_id}:{package_ref}",
            limit=50,
        )
        return SkillMarketplacePackageDetail(
            entry=SkillRepositoryEntry(**_public_entry(entry)),
            versions=[redact(item) for item in versions],
            latest_health=SkillMarketplaceHealthRecord(**health) if health else None,
            install_records=[SkillMarketplaceInstallRecord(**item) for item in installs],
            dependency_edges=[SkillDependencyEdge(**item) for item in dependencies],
        )

    async def refresh_health(
        self,
        repository_id: str,
        *,
        trace_id: str | None = None,
    ) -> list[SkillMarketplaceHealthRecord]:
        repository = await self.require_repository(repository_id)
        entries = await self._repo.search_entries(
            query=None,
            repository_id=repository.repository_id,
            tag=None,
            limit=500,
        )
        now = utc_now_iso()
        records: list[SkillMarketplaceHealthRecord] = []
        for entry in entries:
            health = _health_for_entry(entry, repository_status=repository.status)
            data = {
                "health_record_id": new_id("skhlth"),
                "organization_id": "org_default",
                "repository_id": repository.repository_id,
                "package_ref": entry["package_ref"],
                "bundle_id": entry["bundle_id"],
                "health_status": health["health_status"],
                "provider_status": health["provider_status"],
                "quality_score": health["quality_score"],
                "reason_codes": health["reason_codes"],
                "evidence": health["evidence"],
                "trace_id": trace_id,
                "checked_at": now,
                "created_at": now,
            }
            await self._repo.insert_health_record(data)
            await self._repo.update_entry_marketplace(
                repository.repository_id,
                entry["package_ref"],
                {
                    "health_status": data["health_status"],
                    "quality_score": data["quality_score"],
                    "last_health_check_at": now,
                    "health_reason": ",".join(data["reason_codes"]) or None,
                    "updated_at": now,
                },
            )
            records.append(SkillMarketplaceHealthRecord(**data))
        await self._audit.write_event(
            actor_type="system",
            action="skill_marketplace.health_refreshed",
            object_type="skill_repository",
            object_id=repository.repository_id,
            summary="Skill 市场健康状态已刷新",
            risk_level=RiskLevel.R1,
            payload={"repository_id": repository.repository_id, "count": len(records)},
            trace_id=trace_id,
        )
        return records

    async def record_install(
        self,
        *,
        repository_id: str | None,
        package_ref: str | None,
        installed_bundle_id: str | None,
        skill_ids: list[str],
        status: str,
        gate_status: str,
        eval_status: str | None,
        blocked_reason: str | None,
        requested_by_member_id: str | None,
        trace_id: str | None = None,
    ) -> SkillMarketplaceInstallRecord:
        now = utc_now_iso()
        entry = None
        if repository_id and package_ref:
            entry = await self._repo.get_entry(repository_id, package_ref)
        data = {
            "install_record_id": new_id("skinst"),
            "organization_id": "org_default",
            "repository_id": repository_id,
            "package_ref": package_ref,
            "bundle_id": entry.get("bundle_id") if entry else installed_bundle_id,
            "installed_bundle_id": installed_bundle_id,
            "skill_id": skill_ids[0] if skill_ids else None,
            "version": entry.get("version") if entry else None,
            "status": status,
            "gate_status": gate_status,
            "eval_status": eval_status,
            "blocked_reason": blocked_reason,
            "source_uri_hash": _source_uri_hash(entry.get("source", {})) if entry else None,
            "requested_by_member_id": requested_by_member_id,
            "trace_id": trace_id,
            "created_at": now,
        }
        await self._repo.insert_install_record(data)
        return SkillMarketplaceInstallRecord(**data)

    async def list_install_records(
        self,
        *,
        repository_id: str | None = None,
        package_ref: str | None = None,
        limit: int = 50,
    ) -> list[SkillMarketplaceInstallRecord]:
        rows = await self._repo.list_install_records(
            repository_id=repository_id,
            package_ref=package_ref,
            limit=max(1, min(limit, 200)),
        )
        return [SkillMarketplaceInstallRecord(**row) for row in rows]

    async def refresh_dependency_edges_for_skill(
        self,
        skill: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> list[SkillDependencyEdge]:
        now = utc_now_iso()
        edges: list[SkillDependencyEdge] = []
        for tool_name in skill.get("required_tools") or []:
            edge = _dependency_edge(
                source_type="skill",
                source_id=skill["skill_id"],
                target_type="tool",
                target_id=str(tool_name),
                dependency_kind="requires_tool",
                required_action=str(tool_name),
                risk_level=_risk_for_tool(str(tool_name)),
                status="active" if not _tool_fail_reason(str(tool_name)) else "blocked",
                fail_closed_reason=_tool_fail_reason(str(tool_name)),
                evidence={"bundle_id": skill.get("bundle_id")},
                trace_id=trace_id,
                now=now,
            )
            await self._repo.upsert_dependency_edge(edge)
            edges.append(SkillDependencyEdge(**edge))
        for asset in skill.get("required_assets") or []:
            target_id = str(asset.get("asset_type") or asset.get("category") or "asset")
            action = str(asset.get("action") or asset.get("capability") or "use")
            edge = _dependency_edge(
                source_type="skill",
                source_id=skill["skill_id"],
                target_type="asset_scope",
                target_id=target_id,
                dependency_kind="requires_asset",
                required_action=action,
                risk_level=str(asset.get("risk_level") or "R2"),
                status="requires_grant",
                fail_closed_reason="asset_broker_handle_required",
                evidence={"bundle_id": skill.get("bundle_id")},
                trace_id=trace_id,
                now=now,
            )
            await self._repo.upsert_dependency_edge(edge)
            edges.append(SkillDependencyEdge(**edge))
        return edges

    async def list_dependency_edges(
        self,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[SkillDependencyEdge]:
        rows = await self._repo.list_dependency_edges(
            source_type=source_type,
            source_id=source_id,
            status=status,
            limit=max(1, min(limit, 500)),
        )
        return [SkillDependencyEdge(**row) for row in rows]

    async def consolidate_growth_candidates(
        self,
        request: SkillGrowthCandidateConsolidateRequest,
        *,
        trace_id: str | None = None,
    ) -> list[SkillGrowthCandidate]:
        sources = await self._repo.list_growth_experience_sources(
            task_id=request.task_id,
            experience_id=request.experience_id,
            limit=request.limit,
        )
        now = utc_now_iso()
        records: list[SkillGrowthCandidate] = []
        for source in sources:
            decision = _growth_decision(source)
            candidate_id: str | None = None
            if decision in {"candidate_created", "governance_hint"}:
                if self._skill_repo is None:
                    raise AppError(
                        ErrorCode.INTERNAL_ERROR,
                        "Skill candidate repository unavailable",
                        status_code=500,
                    )
                candidate_id = f"skcand_{_hash_text(source['experience_id'])[:24]}"
                await self._skill_repo.insert_candidate(
                    _candidate_from_experience(
                        source,
                        candidate_id=candidate_id,
                        now=now,
                        trace_id=trace_id,
                    )
                )
            evidence = {
                "evidence_id": new_id("skgrow"),
                "organization_id": "org_default",
                "candidate_id": candidate_id,
                "source_type": "memory_experience",
                "source_id": source["experience_id"],
                "experience_id": source["experience_id"],
                "task_id": source.get("task_id"),
                "memory_id": source.get("memory_id"),
                "outcome": source.get("outcome"),
                "reuse_score": float(source.get("reuse_score") or 0),
                "decision": decision,
                "evidence": {
                    "kind": source.get("kind"),
                    "confidence_score": source.get("confidence_score"),
                    "summary": str(redact(source.get("summary_text") or ""))[:240],
                },
                "trace_id": trace_id,
                "created_at": now,
            }
            await self._repo.insert_growth_evidence(evidence)
            records.append(SkillGrowthCandidate(**evidence))
        return records

    async def list_growth_candidates(
        self,
        *,
        candidate_id: str | None = None,
        limit: int = 50,
    ) -> list[SkillGrowthCandidate]:
        rows = await self._repo.list_growth_evidence(
            candidate_id=candidate_id,
            limit=max(1, min(limit, 200)),
        )
        return [SkillGrowthCandidate(**row) for row in rows]

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
    dependency_summary = _dependency_summary_from_package(package)
    compatibility = (
        dict(package.get("compatibility"))
        if isinstance(package.get("compatibility"), dict)
        else {"runtime": "local-api", "schema": INDEX_SCHEMA_VERSION}
    )
    quality_score = _package_quality_score(package, source)
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
        "health_status": "unknown",
        "quality_score": quality_score,
        "install_count": 0,
        "compatibility": compatibility,
        "dependency_summary": dependency_summary,
        "latest_eval_status": package.get("latest_eval_status"),
        "last_health_check_at": None,
        "health_reason": None,
        "package_metadata": {
            "source_type": source_type,
            "source_uri_hash": _source_uri_hash(source),
            "has_checksum": bool(package.get("checksum")),
        },
        "search_text": search_text,
        "status": "active",
        "indexed_at": indexed_at,
        "updated_at": indexed_at,
    }


def _public_entry(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    return {
        **row,
        "source": {
            "type": source.get("type"),
            "uri_hash": _source_uri_hash(source),
            "checksum_required": bool(row.get("checksum")),
        },
        "package_metadata": redact(row.get("package_metadata", {})),
    }


def _dependency_summary_from_package(package: dict[str, Any]) -> dict[str, Any]:
    manifest = package.get("manifest") if isinstance(package.get("manifest"), dict) else {}
    required_tools = package.get("required_tools") or manifest.get("required_tools") or []
    required_assets = package.get("required_assets") or manifest.get("required_assets") or []
    mcp_tools = package.get("mcp_tools") or manifest.get("mcp_tools") or []
    return {
        "required_tools": [str(item) for item in required_tools],
        "required_assets": redact(required_assets if isinstance(required_assets, list) else []),
        "mcp_tools": [str(item) for item in mcp_tools],
        "dependency_count": len(required_tools) + len(required_assets) + len(mcp_tools),
    }


def _package_quality_score(package: dict[str, Any], source: dict[str, Any]) -> float:
    score = 0.45
    if package.get("description"):
        score += 0.1
    if package.get("checksum"):
        score += 0.15
    if package.get("version"):
        score += 0.08
    if package.get("trust_level") in {"trusted", "local", "verified"}:
        score += 0.12
    if source.get("type") in SUPPORTED_SOURCE_TYPES:
        score += 0.08
    return round(max(0.05, min(0.98, score)), 4)


def _health_for_entry(entry: dict[str, Any], *, repository_status: str) -> dict[str, Any]:
    reasons: list[str] = []
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    source_type = str(source.get("type") or "")
    source_uri = str(source.get("uri") or source.get("url") or "")
    provider_status = "available" if repository_status == "enabled" else "disabled"
    health_status = "healthy" if repository_status == "enabled" else "degraded"
    if source_type not in SUPPORTED_SOURCE_TYPES:
        health_status = "unavailable"
        reasons.append("unsupported_source_type")
    if source_type in {"local_directory", "local_archive"} and source_uri:
        path = Path(source_uri)
        if not path.exists():
            health_status = "unavailable"
            provider_status = "unreachable"
            reasons.append("local_source_missing")
    if not entry.get("checksum"):
        reasons.append("checksum_missing")
    quality = float(entry.get("quality_score", 0.5) or 0.5)
    if "checksum_missing" in reasons:
        quality = min(quality, 0.72)
    return {
        "health_status": health_status,
        "provider_status": provider_status,
        "quality_score": round(max(0.05, min(0.98, quality)), 4),
        "reason_codes": reasons,
        "evidence": {
            "source_type": source_type,
            "source_uri_hash": _source_uri_hash(source),
            "repository_status": repository_status,
        },
    }


def _dependency_edge(
    *,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    dependency_kind: str,
    required_action: str | None,
    risk_level: str,
    status: str,
    fail_closed_reason: str | None,
    evidence: dict[str, Any],
    trace_id: str | None,
    now: str,
) -> dict[str, Any]:
    key = "|".join(
        [
            source_type,
            source_id,
            target_type,
            target_id,
            dependency_kind,
            required_action or "",
        ]
    )
    return {
        "edge_id": f"skdep_{_hash_text(key)[:24]}",
        "organization_id": "org_default",
        "source_type": source_type,
        "source_id": source_id,
        "target_type": target_type,
        "target_id": target_id,
        "dependency_kind": dependency_kind,
        "required_action": required_action,
        "risk_level": risk_level,
        "status": status,
        "fail_closed_reason": fail_closed_reason,
        "evidence": redact(evidence),
        "trace_id": trace_id,
        "created_at": now,
        "updated_at": now,
    }


def _risk_for_tool(tool_name: str) -> str:
    if tool_name.startswith("terminal") or tool_name in {"browser.submit", "file.delete"}:
        return "R4"
    if tool_name.startswith("browser.") or tool_name.startswith("file."):
        return "R2"
    return "R1"


def _tool_fail_reason(tool_name: str) -> str | None:
    if "*" in tool_name or tool_name.endswith(":*"):
        return "wildcard_tool_requires_review"
    if tool_name.startswith("asset.secret"):
        return "asset_broker_handle_required"
    return None


def _growth_decision(source: dict[str, Any]) -> str:
    outcome = str(source.get("outcome") or "")
    kind = str(source.get("kind") or "")
    confidence = float(source.get("confidence_score") or 0.0)
    reuse = float(source.get("reuse_score") or 0.0)
    if outcome == "failed":
        return "governance_hint"
    if kind in {"procedural_experience", "task_experience"} and max(confidence, reuse) >= 0.55:
        return "candidate_created"
    return "discarded_low_value"


def _candidate_from_experience(
    source: dict[str, Any],
    *,
    candidate_id: str,
    now: str,
    trace_id: str | None,
) -> dict[str, Any]:
    title = _candidate_title(source)
    outcome = str(source.get("outcome") or "")
    required_tools = ["file.write"] if outcome != "failed" else []
    return {
        "candidate_id": candidate_id,
        "organization_id": "org_default",
        "source_type": "memory_experience",
        "source_id": source["experience_id"],
        "title": title,
        "description": str(redact(source.get("summary_text") or ""))[:400],
        "draft_manifest": {
            "id": _safe_id(title),
            "display_name": title,
            "bundle_revision": "experience-draft",
            "required_tools": required_tools,
            "risk_policy": {"candidate_only": True, "source": "phase57_growth"},
        },
        "draft_skill_md": _candidate_skill_md(source, title=title),
        "proposed_permissions": {"tools": required_tools, "candidate_only": True},
        "proposed_eval_cases": [
            {
                "id": "experience_replay",
                "input": {"experience_id": source["experience_id"]},
                "expected": {"candidate_only": True},
            }
        ],
        "status": "pending_review",
        "reviewed_by_member_id": None,
        "review_reason": "failed_experience_governance_hint" if outcome == "failed" else None,
        "promoted_bundle_id": None,
        "trace_id": trace_id,
        "created_at": now,
        "updated_at": now,
    }


def _candidate_title(source: dict[str, Any]) -> str:
    raw = str(source.get("summary_text") or source.get("kind") or "Skill Experience")
    words = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", raw).strip()
    return (words[:48] or "Skill Experience").strip()


def _candidate_skill_md(source: dict[str, Any], *, title: str) -> str:
    summary = str(redact(source.get("summary_text") or "")).strip()
    if str(source.get("outcome") or "") == "failed":
        summary = f"失败经验治理提示：{summary}"
    return (
        f"# {title}\n\n"
        "## 用途\n"
        f"{summary or '从任务经验沉淀出的候选 Skill。'}\n\n"
        "## 何时使用\n相似任务需要复用流程，但必须先人工审核。\n\n"
        "## 输入\n任务目标和必要上下文。\n\n"
        "## 输出\n候选执行步骤或治理提示。\n\n"
        "## 步骤\n1. 复核来源经验。\n2. 确认权限和评测。\n3. 生成候选输出。\n\n"
        "## 禁止\n不得绕过 Safety、Approval、Capability Graph 或 Asset Broker。\n"
    )


def _source_uri_hash(source: dict[str, Any]) -> str | None:
    uri = source.get("uri") or source.get("url")
    if not uri:
        return None
    return "sha256:" + hashlib.sha256(str(uri).encode("utf-8")).hexdigest()


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
