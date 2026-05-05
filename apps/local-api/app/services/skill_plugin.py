from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from core_types import (
    ErrorCode,
    PermissionPreview,
    PluginBundle,
    PluginEvent,
    RiskLevel,
    SkillCandidateRecord,
    SkillEvalRun,
    SkillMatch,
    SkillRecord,
    SkillRunRecord,
    TraceSpanStatus,
    TraceSpanType,
)
from safety_service import SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.skills import BundleInstallRequest, SkillMatchRequest
from app.schemas.tasks import ToolExecuteRequest
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService
from app.services.skill_governance import SkillGovernanceService
from app.services.skill_repositories import SkillRepositoryService
from app.services.skill_source_resolver import SkillSourceResolver
from app.services.tools import ToolRuntime

REQUIRED_SKILL_SECTIONS = ["用途", "何时使用", "输入", "输出", "步骤", "禁止"]


class SkillPluginService:
    def __init__(
        self,
        *,
        repo: SkillMcpRepository,
        task_repo: TaskRepository,
        tool_runtime: ToolRuntime,
        artifact_store: ArtifactStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
        governance_service: SkillGovernanceService | None = None,
        repository_service: SkillRepositoryService | None = None,
        source_resolver: SkillSourceResolver | None = None,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo
        self._tools = tool_runtime
        self._artifacts = artifact_store
        self._source_resolver = source_resolver
        self._trace = trace_service
        self._audit = audit_service
        self._governance = governance_service
        self._repository_service = repository_service
        self._safety = SafetyService()

    def set_governance_service(self, governance_service: SkillGovernanceService) -> None:
        self._governance = governance_service

    def set_source_resolver(self, source_resolver: SkillSourceResolver) -> None:
        self._source_resolver = source_resolver

    def set_repository_service(self, repository_service: SkillRepositoryService) -> None:
        self._repository_service = repository_service

    async def install_bundle(
        self,
        request: BundleInstallRequest,
        *,
        trace_id: str | None = None,
    ) -> tuple[PluginBundle, list[SkillRecord], PermissionPreview]:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.PLUGIN_INSTALL,
            "install plugin bundle",
            input_data={"source_type": request.source_type, "source_uri": request.source_uri},
        )
        now = utc_now_iso()
        job_key = request.idempotency_key or f"plugin.install:{request.source_uri}"
        resolved = None
        await self._repo.insert_install_job(
            {
                "job_id": new_id("pjob"),
                "organization_id": "org_default",
                "idempotency_key": job_key,
                "job_type": "install_bundle",
                "status": "running",
                "payload": redact(request.model_dump(mode="json")),
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            resolved = await self._resolve_source(request)
            root = resolved.root
            manifest, skill_md, manifest_hash = await self._load_and_validate(root, trace_id)
            preview = await self._build_permission_preview(None, manifest, trace_id=trace_id)
            analysis = None
            if self._governance is not None:
                analysis = await self._governance.analyze_manifest(
                    bundle_id=_safe_id(str(manifest["id"])),
                    manifest=manifest,
                    skill_md=skill_md,
                    manifest_hash=manifest_hash,
                    trace_id=trace_id,
                )
            bundle_id = _safe_id(str(manifest["id"]))
            bundle = {
                "bundle_id": bundle_id,
                "organization_id": "org_default",
                "display_name": str(manifest.get("display_name") or bundle_id),
                "description": manifest.get("description"),
                "author": manifest.get("author"),
                "bundle_revision": str(
                    manifest.get("bundle_revision") or manifest.get("version") or "1.0.0"
                ),
                "source_type": resolved.source_type,
                "source_uri": resolved.source_uri,
                "package_uri": f"bundle://{bundle_id}",
                "manifest_hash": manifest_hash,
                "signature_status": "unsigned",
                "trust_level": (
                    analysis.trust_level if analysis is not None else _trust_for_manifest(manifest)
                ),
                "status": "installed_disabled",
                "permission_summary": preview.model_dump(mode="json"),
                "risk_summary": {
                    "high_risk_actions": preview.high_risk_actions,
                    "blocked_actions": preview.blocked_actions,
                },
                "manifest": manifest,
                "installed_by_member_id": request.requested_by_member_id,
                "installed_at": now,
                "created_at": now,
                "updated_at": now,
            }
            await self._repo.insert_bundle(bundle)
            await self._repo.delete_files_for_bundle(bundle_id)
            for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
                relative = file_path.resolve().relative_to(root).as_posix()
                await self._repo.insert_plugin_file(
                    {
                        "file_id": new_id("pfile"),
                        "bundle_id": bundle_id,
                        "relative_path": relative,
                        "file_type": _file_type(relative),
                        "size_bytes": file_path.stat().st_size,
                        "checksum": _hash_bytes(file_path.read_bytes()),
                        "sensitivity": "low",
                        "created_at": now,
                    }
                )
            skills = []
            for skill_data in _skills_from_manifest(bundle_id, manifest, skill_md):
                skill_data.update(
                    {
                        "organization_id": "org_default",
                        "bundle_id": bundle_id,
                        "status": "installed_disabled",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                await self._repo.insert_skill(skill_data)
                skill = await self._repo.get_skill(skill_data["skill_id"])
                if skill is not None:
                    skills.append(SkillRecord(**skill))
                await self._register_skill_tool(skill_data, "disabled", now)
            for case in _eval_cases_from_manifest(manifest):
                await self._repo.insert_eval_case(
                    {
                        "eval_case_id": new_id("sevalcase"),
                        "organization_id": "org_default",
                        "skill_id": skills[0].skill_id if skills else None,
                        "bundle_id": bundle_id,
                        "case_key": str(case.get("id") or case.get("case_key") or new_id("case")),
                        "input": case.get("input", {}),
                        "expected": case.get("expected", {}),
                        "forbidden": case.get("forbidden", {}),
                        "risk_assertions": case.get("risk_assertions", {}),
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            await self._event(
                "plugin.installed",
                bundle_id=bundle_id,
                payload={"bundle_id": bundle_id, "skill_count": len(skills)},
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="member",
                actor_id=request.requested_by_member_id,
                action="plugin.installed",
                object_type="plugin_bundle",
                object_id=bundle_id,
                summary="插件包已安装",
                risk_level=RiskLevel.R2,
                payload={"bundle_id": bundle_id, "skill_count": len(skills)},
                trace_id=trace_id,
            )
            await self._repo.insert_install_job(
                {
                    "job_id": new_id("pjob"),
                    "organization_id": "org_default",
                    "bundle_id": bundle_id,
                    "idempotency_key": job_key,
                    "job_type": "install_bundle",
                    "status": "completed",
                    "payload": redact(request.model_dump(mode="json")),
                    "result": {"bundle_id": bundle_id, "skill_count": len(skills)},
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            await self._end_span(span_id, output_data={"bundle_id": bundle_id})
            bundle_row = await self._repo.get_bundle(bundle_id)
            if bundle_row is None:
                raise AppError(
                    ErrorCode.PLUGIN_INSTALL_FAILED,
                    "插件安装后无法读取",
                    status_code=500,
                )
            if self._governance is not None:
                await self._governance.persist_install_governance(
                    request=request,
                    bundle=bundle_row,
                    skills=skills,
                    manifest=manifest,
                    skill_md=skill_md,
                    manifest_hash=manifest_hash,
                    preview=preview.model_copy(update={"bundle_id": bundle_id}),
                    analysis=analysis,
                    trace_id=trace_id,
                )
            if self._repository_service is not None:
                await self._repository_service.record_install(
                    repository_id=resolved.repository_id,
                    package_ref=resolved.package_ref,
                    installed_bundle_id=bundle_id,
                    skill_ids=[skill.skill_id for skill in skills],
                    status="installed_disabled",
                    gate_status="preview_passed",
                    eval_status=analysis.status if analysis is not None else None,
                    blocked_reason=None if analysis is None or analysis.status != "blocked" else "analysis_blocked",
                    requested_by_member_id=request.requested_by_member_id,
                    trace_id=trace_id,
                )
                for skill in skills:
                    await self._repository_service.refresh_dependency_edges_for_skill(
                        skill.model_dump(mode="json"),
                        trace_id=trace_id,
                    )
            return (
                PluginBundle(**bundle_row),
                skills,
                preview.model_copy(update={"bundle_id": bundle_id}),
            )
        except Exception as exc:
            await self._repo.insert_install_job(
                {
                    "job_id": new_id("pjob"),
                    "organization_id": "org_default",
                    "idempotency_key": job_key,
                    "job_type": "install_bundle",
                    "status": "failed",
                    "payload": redact(request.model_dump(mode="json")),
                    "error_code": getattr(exc, "code", ErrorCode.PLUGIN_INSTALL_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            if self._repository_service is not None:
                try:
                    await self._repository_service.record_install(
                        repository_id=getattr(resolved, "repository_id", None),
                        package_ref=getattr(resolved, "package_ref", None),
                        installed_bundle_id=None,
                        skill_ids=[],
                        status="failed",
                        gate_status="blocked",
                        eval_status=None,
                        blocked_reason=str(getattr(exc, "code", ErrorCode.PLUGIN_INSTALL_FAILED.value)),
                        requested_by_member_id=request.requested_by_member_id,
                        trace_id=trace_id,
                    )
                except Exception:
                    pass
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.PLUGIN_INSTALL_FAILED,
                "插件安装失败",
                status_code=500,
            ) from exc

    async def list_bundles(self) -> list[PluginBundle]:
        return [PluginBundle(**row) for row in await self._repo.list_bundles()]

    async def get_bundle(self, bundle_id: str) -> PluginBundle:
        row = await self._repo.get_bundle(bundle_id)
        if row is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "插件包不存在", status_code=404)
        return PluginBundle(**row)

    async def preview_permissions(
        self,
        bundle_id: str,
        *,
        trace_id: str | None = None,
    ) -> PermissionPreview:
        bundle = await self.get_bundle(bundle_id)
        return await self._build_permission_preview(
            bundle.bundle_id,
            bundle.manifest,
            trace_id=trace_id,
        )

    async def enable_bundle(
        self,
        bundle_id: str,
        *,
        actor_member_id: str,
        trace_id: str | None = None,
    ) -> PluginBundle:
        bundle = await self.get_bundle(bundle_id)
        if bundle.status == "revoked":
            raise AppError(ErrorCode.PLUGIN_REVOKED, "插件包已撤销", status_code=409)
        if bundle.trust_level == "blocked" or bundle.signature_status == "invalid":
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "插件包不可信，不能启用",
                status_code=409,
            )
        skills = await self._repo.list_skills(bundle_id=bundle_id)
        for skill in skills:
            result = await self.run_eval(skill["skill_id"], trace_id=trace_id)
            if result.security_failures:
                raise AppError(
                    ErrorCode.EVAL_SECURITY_FAILED,
                    "Skill 安全评测失败",
                    status_code=409,
                )
        now = utc_now_iso()
        await self._repo.update_bundle(
            bundle_id,
            {"status": "enabled", "enabled_at": now, "updated_at": now},
        )
        for skill in skills:
            await self._repo.update_skill(
                skill["skill_id"],
                {"status": "enabled", "updated_at": now},
            )
            await self._register_skill_tool(skill, "active", now)
        await self._event(
            "plugin.enabled",
            bundle_id=bundle_id,
            payload={"bundle_id": bundle_id},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=actor_member_id,
            action="plugin.enabled",
            object_type="plugin_bundle",
            object_id=bundle_id,
            summary="插件包已启用",
            risk_level=RiskLevel.R2,
            payload={"bundle_id": bundle_id},
            trace_id=trace_id,
        )
        return await self.get_bundle(bundle_id)

    async def disable_bundle(
        self,
        bundle_id: str,
        *,
        actor_member_id: str,
        reason: str | None,
        trace_id: str | None = None,
    ) -> PluginBundle:
        bundle = await self.get_bundle(bundle_id)
        if bundle.status == "revoked":
            raise AppError(ErrorCode.PLUGIN_REVOKED, "插件包已撤销", status_code=409)
        now = utc_now_iso()
        await self._repo.update_bundle(
            bundle_id,
            {"status": "disabled", "disabled_at": now, "updated_at": now},
        )
        for skill in await self._repo.list_skills(bundle_id=bundle_id):
            await self._repo.update_skill(
                skill["skill_id"],
                {"status": "disabled", "updated_at": now},
            )
            await self._register_skill_tool(skill, "disabled", now)
        await self._event(
            "plugin.disabled",
            bundle_id=bundle_id,
            payload={"bundle_id": bundle_id, "reason": reason},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=actor_member_id,
            action="plugin.disabled",
            object_type="plugin_bundle",
            object_id=bundle_id,
            summary="插件包已禁用",
            risk_level=RiskLevel.R2,
            payload={"bundle_id": bundle_id, "reason": reason},
            trace_id=trace_id,
        )
        return await self.get_bundle(bundle_id)

    async def revoke_bundle(
        self,
        bundle_id: str,
        *,
        actor_member_id: str,
        reason: str | None,
        trace_id: str | None = None,
    ) -> PluginBundle:
        await self.disable_bundle(
            bundle_id,
            actor_member_id=actor_member_id,
            reason=reason,
            trace_id=trace_id,
        )
        now = utc_now_iso()
        await self._repo.update_bundle(
            bundle_id,
            {"status": "revoked", "revoked_at": now, "updated_at": now},
        )
        for skill in await self._repo.list_skills(bundle_id=bundle_id):
            await self._repo.update_skill(
                skill["skill_id"],
                {"status": "revoked", "updated_at": now},
            )
            await self._register_skill_tool(skill, "disabled", now)
        await self._event(
            "plugin.revoked",
            bundle_id=bundle_id,
            payload={"bundle_id": bundle_id, "reason": reason},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=actor_member_id,
            action="plugin.revoked",
            object_type="plugin_bundle",
            object_id=bundle_id,
            summary="插件包已撤销",
            risk_level=RiskLevel.R3,
            payload={"bundle_id": bundle_id, "reason": reason},
            trace_id=trace_id,
        )
        return await self.get_bundle(bundle_id)

    async def list_events(self, bundle_id: str) -> list[PluginEvent]:
        await self.get_bundle(bundle_id)
        return [PluginEvent(**row) for row in await self._repo.list_events(bundle_id)]

    async def list_skills(self, status: str | None = None) -> list[SkillRecord]:
        return [SkillRecord(**row) for row in await self._repo.list_skills(status=status)]

    async def get_skill(self, skill_id: str) -> SkillRecord:
        row = await self._repo.get_skill(skill_id)
        if row is None:
            raise AppError(ErrorCode.SKILL_NOT_FOUND, "Skill 不存在", status_code=404)
        return SkillRecord(**row)

    async def enable_skill(
        self,
        skill_id: str,
        *,
        actor_member_id: str,
        trace_id: str | None = None,
    ) -> SkillRecord:
        skill = await self.get_skill(skill_id)
        bundle = await self.get_bundle(skill.bundle_id)
        if skill.status == "revoked" or bundle.status == "revoked":
            raise AppError(ErrorCode.SKILL_REVOKED, "Skill 已撤销", status_code=409)
        if bundle.status != "enabled":
            raise AppError(ErrorCode.PLUGIN_DISABLED, "插件包未启用", status_code=409)
        result = await self.run_eval(skill_id, trace_id=trace_id)
        if result.security_failures:
            raise AppError(ErrorCode.EVAL_SECURITY_FAILED, "Skill 安全评测失败", status_code=409)
        now = utc_now_iso()
        await self._repo.update_skill(skill_id, {"status": "enabled", "updated_at": now})
        await self._register_skill_tool(skill.model_dump(mode="json"), "active", now)
        await self._event(
            "skill.enabled",
            bundle_id=skill.bundle_id,
            skill_id=skill_id,
            payload={"skill_id": skill_id},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=actor_member_id,
            action="skill.enabled",
            object_type="skill",
            object_id=skill_id,
            summary="Skill 已启用",
            risk_level=RiskLevel.R2,
            payload={"skill_id": skill_id},
            trace_id=trace_id,
        )
        return await self.get_skill(skill_id)

    async def disable_skill(
        self,
        skill_id: str,
        *,
        actor_member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> SkillRecord:
        skill = await self.get_skill(skill_id)
        if skill.status == "revoked":
            raise AppError(ErrorCode.SKILL_REVOKED, "Skill 已撤销", status_code=409)
        now = utc_now_iso()
        await self._repo.update_skill(skill_id, {"status": "disabled", "updated_at": now})
        await self._register_skill_tool(skill.model_dump(mode="json"), "disabled", now)
        await self._event(
            "skill.disabled",
            bundle_id=skill.bundle_id,
            skill_id=skill_id,
            payload={"skill_id": skill_id, "reason": reason},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=actor_member_id,
            action="skill.disabled",
            object_type="skill",
            object_id=skill_id,
            summary="Skill 已禁用",
            risk_level=RiskLevel.R2,
            payload={"skill_id": skill_id, "reason": reason},
            trace_id=trace_id,
        )
        return await self.get_skill(skill_id)

    async def match_skills(
        self,
        request: SkillMatchRequest,
        *,
        trace_id: str | None = None,
    ) -> list[SkillMatch]:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SKILL_MATCH,
            "match skills",
            input_data={"goal": request.goal, "intent": request.intent},
        )
        try:
            text = f"{request.intent or ''} {request.goal}".lower()
            matches: list[SkillMatch] = []
            for row in await self._repo.list_skills(status="enabled"):
                score, reason = _score_skill(row, text)
                if score <= 0:
                    continue
                matches.append(
                    SkillMatch(
                        skill_id=row["skill_id"],
                        bundle_id=row["bundle_id"],
                        display_name=row["display_name"],
                        confidence=min(score, 0.99),
                        reason=reason,
                        required_tools=row["required_tools"],
                        required_assets=row["required_assets"],
                    )
                )
            matches.sort(key=lambda item: item.confidence, reverse=True)
            await self._end_span(span_id, output_data={"match_count": len(matches)})
            return matches
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def run_skill(
        self,
        skill_id: str,
        *,
        task_id: str | None,
        step_id: str | None,
        owner_member_id: str,
        input_data: dict[str, Any],
        matched_reason: str | None = None,
        confidence: float | None = None,
        approval_id: str | None = None,
        trace_id: str | None = None,
    ) -> SkillRunRecord:
        skill = await self.get_skill(skill_id)
        if skill.status != "enabled":
            raise AppError(ErrorCode.SKILL_DISABLED, "Skill 未启用", status_code=409)
        bundle = await self.get_bundle(skill.bundle_id)
        if bundle.status != "enabled":
            raise AppError(ErrorCode.PLUGIN_DISABLED, "插件包未启用", status_code=409)
        steps = skill.steps or _default_skill_steps(skill)
        policy_snapshot: dict[str, Any] = {}
        if self._governance is not None:
            policy_snapshot = await self._governance.ensure_skill_run_allowed(
                skill=skill,
                bundle=bundle.model_dump(mode="json"),
                owner_member_id=owner_member_id,
                steps=steps,
                input_data=input_data,
                task_id=task_id,
                trace_id=trace_id,
            )
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SKILL_RUN,
            "run skill",
            input_data={"skill_id": skill_id, "task_id": task_id},
        )
        now = utc_now_iso()
        existing_run = None
        if approval_id and task_id and step_id:
            existing_run = await self._repo.get_waiting_skill_run(
                task_id=task_id,
                step_id=step_id,
                skill_id=skill_id,
                approval_id=approval_id,
            )
        if existing_run is not None:
            skill_run_id = str(existing_run["skill_run_id"])
            await self._repo.update_skill_run(
                skill_run_id,
                {
                    "status": "running",
                    "trace_id": trace_id,
                    "started_at": now,
                    "error_code": None,
                    "error_summary": None,
                    "policy_snapshot": policy_snapshot,
                },
            )
        else:
            skill_run_id = new_id("skrun")
            await self._repo.insert_skill_run(
                {
                    "skill_run_id": skill_run_id,
                    "organization_id": "org_default",
                    "skill_id": skill_id,
                    "bundle_id": skill.bundle_id,
                    "task_id": task_id,
                    "step_id": step_id,
                    "owner_member_id": owner_member_id,
                    "status": "running",
                    "input_redacted": redact(input_data),
                    "matched_reason": matched_reason,
                    "confidence": confidence,
                    "policy_snapshot": policy_snapshot,
                    "trace_id": trace_id,
                    "started_at": now,
                    "created_at": now,
                }
            )
        await self._event(
            "skill.started",
            bundle_id=skill.bundle_id,
            skill_id=skill_id,
            payload={
                "skill_id": skill_id,
                "skill_run_id": skill_run_id,
                "resumed_after_approval": existing_run is not None,
            },
            trace_id=trace_id,
        )
        artifact_ids: list[str] = []
        output: dict[str, Any] = {"steps": []}
        try:
            for index, step in enumerate(steps, start=1):
                tool_name = str(step.get("tool_name") or step.get("tool") or "")
                if not tool_name:
                    continue
                args = dict(step.get("args", {}))
                args = _format_args(args, input_data, skill)
                result = await self._tools.execute(
                    ToolExecuteRequest(
                        task_id=task_id,
                        step_id=step_id,
                        member_id=owner_member_id,
                        tool_name=tool_name,
                        args=args,
                        idempotency_key=_skill_tool_idempotency_key(
                            skill_run_id=skill_run_id,
                            skill_id=skill_id,
                            task_id=task_id,
                            step_id=step_id,
                            index=index,
                            tool_name=tool_name,
                            approval_id=approval_id,
                        ),
                        approval_id=approval_id,
                    ),
                    trace_id=trace_id,
                )
                artifact_ids.extend([artifact.artifact_id for artifact in result.artifacts])
                output["steps"].append(
                    {
                        "tool_name": tool_name,
                        "status": result.tool_call.status,
                        "artifact_ids": [artifact.artifact_id for artifact in result.artifacts],
                    }
                )
                if result.approval:
                    await self._repo.update_skill_run(
                        skill_run_id,
                        {
                            "status": "waiting_approval",
                            "approval_id": result.approval.approval_id,
                            "artifact_ids": artifact_ids,
                            "output_redacted": output,
                            "policy_snapshot": policy_snapshot,
                        },
                    )
                    await self._end_span(
                        span_id,
                        output_data={"status": "waiting_approval"},
                    )
                    return SkillRunRecord(
                        **(await self._skill_run_row_or_error(skill_run_id))
                    )
            await self._repo.update_skill_run(
                skill_run_id,
                {
                    "status": "completed",
                    "output_redacted": output,
                    "artifact_ids": artifact_ids,
                    "policy_snapshot": policy_snapshot,
                    "completed_at": utc_now_iso(),
                },
            )
            if self._governance is not None:
                await self._governance.record_skill_output_taint(
                    skill=skill,
                    skill_run_id=skill_run_id,
                    task_id=task_id,
                    output=output,
                    policy_snapshot=policy_snapshot,
                    trace_id=trace_id,
                )
            await self._event(
                "skill.completed",
                bundle_id=skill.bundle_id,
                skill_id=skill_id,
                payload={
                    "skill_id": skill_id,
                    "skill_run_id": skill_run_id,
                    "artifact_ids": artifact_ids,
                },
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="member",
                actor_id=owner_member_id,
                action="skill.run_completed",
                object_type="skill",
                object_id=skill_id,
                summary="Skill 执行完成",
                risk_level=RiskLevel.R2,
                payload={"skill_id": skill_id, "skill_run_id": skill_run_id},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"status": "completed"})
            return SkillRunRecord(**(await self._skill_run_row_or_error(skill_run_id)))
        except Exception as exc:
            await self._repo.update_skill_run(
                skill_run_id,
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.SKILL_RUN_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "output_redacted": output,
                    "artifact_ids": artifact_ids,
                    "completed_at": utc_now_iso(),
                },
            )
            await self._event(
                "skill.failed",
                bundle_id=skill.bundle_id,
                skill_id=skill_id,
                payload={"skill_id": skill_id, "error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                raise
            raise AppError(ErrorCode.SKILL_RUN_FAILED, "Skill 执行失败", status_code=500) from exc

    async def run_eval(self, skill_id: str, *, trace_id: str | None = None) -> SkillEvalRun:
        skill = await self.get_skill(skill_id)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SKILL_EVAL,
            "run skill eval",
            input_data={"skill_id": skill_id},
        )
        now = utc_now_iso()
        cases = await self._repo.list_eval_cases(skill_id)
        failed = 0
        security = 0
        results = []
        forbidden_actions = _forbidden_actions(skill.risk_policy)
        for case in cases:
            case_failed = False
            forbidden = case.get("forbidden", {})
            text_forbidden = set(forbidden.get("text", []))
            if any(str(item) in skill.instructions for item in text_forbidden):
                case_failed = True
                security += 1
            actions = set(forbidden.get("actions", []))
            if actions.intersection(forbidden_actions):
                case_failed = True
                security += 1
            failed += 1 if case_failed else 0
            results.append({"case_key": case["case_key"], "passed": not case_failed})
        status = "failed" if failed else "passed"
        eval_run_id = new_id("sevalrun")
        completed_at = utc_now_iso()
        await self._repo.insert_eval_run(
            {
                "eval_run_id": eval_run_id,
                "organization_id": "org_default",
                "skill_id": skill_id,
                "bundle_id": skill.bundle_id,
                "status": status,
                "total_cases": len(cases),
                "passed_cases": len(cases) - failed,
                "failed_cases": failed,
                "security_failures": security,
                "result": {"cases": results},
                "trace_id": trace_id,
                "started_at": now,
                "completed_at": completed_at,
                "created_at": now,
            }
        )
        if self._governance is not None:
            await self._governance.record_eval_binding(
                skill=skill,
                eval_run_id=eval_run_id,
                status=status,
                trace_id=trace_id,
            )
        await self._repo.update_skill(
            skill_id,
            {
                "eval_summary": {
                    "last_eval_run_id": eval_run_id,
                    "status": status,
                    "security_failures": security,
                },
                "updated_at": completed_at,
            },
        )
        await self._audit.write_event(
            actor_type="system",
            action="skill.eval_completed",
            object_type="skill",
            object_id=skill_id,
            summary="Skill 评测完成",
            risk_level=RiskLevel.R1,
            payload={"eval_run_id": eval_run_id, "status": status},
            trace_id=trace_id,
        )
        await self._end_span(
            span_id,
            output_data={"eval_run_id": eval_run_id, "status": status},
        )
        return SkillEvalRun(
            eval_run_id=eval_run_id,
            organization_id="org_default",
            skill_id=skill_id,
            bundle_id=skill.bundle_id,
            status=status,
            total_cases=len(cases),
            passed_cases=len(cases) - failed,
            failed_cases=failed,
            security_failures=security,
            result={"cases": results},
            trace_id=trace_id,
            started_at=now,
            completed_at=completed_at,
            created_at=now,
        )

    async def list_candidates(self, status: str | None = None) -> list[SkillCandidateRecord]:
        return [SkillCandidateRecord(**row) for row in await self._repo.list_candidates(status)]

    async def promote_candidate(
        self,
        candidate_id: str,
        *,
        reviewed_by_member_id: str,
        trace_id: str | None = None,
    ) -> tuple[PluginBundle, list[SkillRecord]]:
        row = await self._repo.get_candidate(candidate_id)
        if row is None:
            raise AppError(ErrorCode.SKILL_CANDIDATE_NOT_FOUND, "Skill 候选不存在", status_code=404)
        bundle_id = _safe_id(str(row["draft_manifest"].get("id") or row["candidate_id"]))
        now = utc_now_iso()
        manifest = {
            **row["draft_manifest"],
            "id": bundle_id,
            "display_name": row["title"],
            "entry_skills": [bundle_id],
            "steps": _default_candidate_steps(row),
        }
        preview = await self._build_permission_preview(bundle_id, manifest, trace_id=trace_id)
        bundle = {
            "bundle_id": bundle_id,
            "organization_id": "org_default",
            "display_name": row["title"],
            "description": row.get("description"),
            "author": "local",
            "bundle_revision": "draft",
            "source_type": "candidate",
            "source_uri": f"skill_candidate://{candidate_id}",
            "package_uri": f"bundle://{bundle_id}",
            "manifest_hash": _hash_text(_json(manifest)),
            "signature_status": "unsigned",
            "trust_level": "local",
            "status": "installed_disabled",
            "permission_summary": preview.model_dump(mode="json"),
            "risk_summary": {"high_risk_actions": preview.high_risk_actions},
            "manifest": manifest,
            "installed_by_member_id": reviewed_by_member_id,
            "installed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_bundle(bundle)
        skill_data = {
            "skill_id": f"skill.{bundle_id}",
            "organization_id": "org_default",
            "bundle_id": bundle_id,
            "name": bundle_id,
            "display_name": row["title"],
            "description": row.get("description"),
            "entrypoint_path": "SKILL.md",
            "instructions": row["draft_skill_md"],
            "trigger": manifest.get("triggers", {}),
            "input_schema": {},
            "output_schema": {},
            "required_tools": manifest.get("required_tools", ["file.write"]),
            "required_assets": manifest.get("required_assets", []),
            "permission": row["proposed_permissions"],
            "risk_policy": manifest.get("risk_policy", {}),
            "eval_summary": {},
            "steps": manifest["steps"],
            "status": "installed_disabled",
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_skill(skill_data)
        await self._repo.insert_candidate(
            {
                **row,
                "status": "promoted",
                "reviewed_by_member_id": reviewed_by_member_id,
                "promoted_bundle_id": bundle_id,
                "updated_at": now,
            }
        )
        await self._event(
            "skill.candidate_promoted",
            bundle_id=bundle_id,
            skill_id=skill_data["skill_id"],
            payload={"candidate_id": candidate_id, "bundle_id": bundle_id},
            trace_id=trace_id,
        )
        bundle_row = await self._repo.get_bundle(bundle_id)
        skill_row = await self._repo.get_skill(skill_data["skill_id"])
        if bundle_row is None or skill_row is None:
            raise AppError(ErrorCode.PLUGIN_INSTALL_FAILED, "候选转正后无法读取", status_code=500)
        return PluginBundle(**bundle_row), [SkillRecord(**skill_row)]

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        reviewed_by_member_id: str,
        reason: str | None,
        trace_id: str | None = None,
    ) -> SkillCandidateRecord:
        row = await self._repo.get_candidate(candidate_id)
        if row is None:
            raise AppError(ErrorCode.SKILL_CANDIDATE_NOT_FOUND, "Skill 候选不存在", status_code=404)
        await self._repo.insert_candidate(
            {
                **row,
                "status": "rejected",
                "reviewed_by_member_id": reviewed_by_member_id,
                "review_reason": reason,
                "updated_at": utc_now_iso(),
            }
        )
        updated = await self._repo.get_candidate(candidate_id)
        if updated is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "候选无法读取", status_code=500)
        return SkillCandidateRecord(**updated)

    async def replay_skill_runs(self, task_id: str) -> list[dict[str, Any]]:
        return [redact(row) for row in await self._repo.list_skill_runs(task_id)]

    async def replay_plugin_events(self, task_id: str) -> list[dict[str, Any]]:
        return [redact(row) for row in await self._repo.list_events_for_task_replay(task_id)]

    async def _load_and_validate(
        self,
        root: Path,
        trace_id: str | None,
    ) -> tuple[dict[str, Any], str, str]:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.PLUGIN_VALIDATE,
            "validate plugin manifest",
            input_data={"root": str(root)},
        )
        try:
            manifest_path = root / "bundle.yaml"
            skill_path = root / "SKILL.md"
            if not manifest_path.exists():
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "缺少 bundle.yaml",
                    status_code=422,
                )
            if not skill_path.exists():
                raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "缺少 SKILL.md", status_code=422)
            manifest_text = manifest_path.read_text(encoding="utf-8")
            skill_md = skill_path.read_text(encoding="utf-8")
            if self._safety.classify_chat_input(manifest_text).sensitivity_hits:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "bundle manifest 包含敏感信息",
                    status_code=422,
                )
            if self._safety.classify_chat_input(skill_md).sensitivity_hits:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "SKILL.md 包含敏感信息",
                    status_code=422,
                )
            manifest = yaml.safe_load(manifest_text)
            if not isinstance(manifest, dict):
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "bundle.yaml 必须是对象",
                    status_code=422,
                )
            if not manifest.get("id"):
                raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "bundle id 必填", status_code=422)
            if _safe_id(str(manifest["id"])) in {"company", "employees", "boss"}:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "bundle id 使用了保留词",
                    status_code=422,
                )
            missing_sections = [
                section for section in REQUIRED_SKILL_SECTIONS if section not in skill_md
            ]
            if missing_sections:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "SKILL.md 缺少必填段落",
                    status_code=422,
                    details={"missing_sections": missing_sections},
                )
            _validate_manifest_tool_contract(manifest)
            for file_path in root.rglob("*"):
                resolved = file_path.resolve()
                if root not in [resolved, *resolved.parents]:
                    raise AppError(
                        ErrorCode.PLUGIN_VALIDATE_FAILED,
                        "bundle 路径逃逸",
                        status_code=422,
                    )
            for tool_name in _manifest_tool_names(manifest):
                if str(tool_name).startswith("mcp."):
                    continue
                if await self._task_repo.get_tool(str(tool_name)) is None:
                    raise AppError(
                        ErrorCode.PLUGIN_VALIDATE_FAILED,
                        "required_tools 不存在",
                        status_code=422,
                        details={"tool_name": tool_name},
                    )
            manifest_hash = _hash_text(manifest_text + "\n" + skill_md)
            await self._end_span(span_id, output_data={"manifest_hash": manifest_hash})
            return manifest, skill_md, manifest_hash
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _build_permission_preview(
        self,
        bundle_id: str | None,
        manifest: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> PermissionPreview:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.PLUGIN_PERMISSION_PREVIEW,
            "preview plugin permissions",
            input_data={"bundle_id": bundle_id or manifest.get("id")},
        )
        required_tools = []
        manifest_risks = _manifest_tool_risks(manifest)
        for tool_name in _manifest_tool_names(manifest):
            tool = await self._task_repo.get_tool(str(tool_name))
            risk_policy = (tool or {}).get("risk_policy", {"default": "R2"})
            required_tools.append(
                {
                    "tool_name": str(tool_name),
                    "risk_level": manifest_risks.get(
                        str(tool_name),
                        risk_policy.get("default", "R2"),
                    ),
                }
            )
        high_risk = [
            {"action": action, "risk_level": "R4", "approval_required": True}
            for action in manifest.get("risk_policy", {}).get("confirmation_required_for", [])
        ]
        permissions = manifest.get("permissions", {})
        network = (
            manifest.get("network")
            or permissions.get("network")
            or permissions.get("net")
            or {}
        )
        filesystem = (
            manifest.get("filesystem")
            or permissions.get("filesystem")
            or permissions.get("fs")
            or {}
        )
        assets = permissions.get("assets") or manifest.get("required_assets", [])
        preview = PermissionPreview(
            bundle_id=bundle_id,
            summary=f"{manifest.get('display_name') or manifest.get('id')} 需要 "
            f"{len(required_tools)} 个工具和 "
            f"{len(manifest.get('required_assets', []))} 类资产声明。",
            required_tools=required_tools,
            required_assets=assets if isinstance(assets, list) else [],
            network=network if isinstance(network, dict) else {},
            filesystem=filesystem if isinstance(filesystem, dict) else {},
            high_risk_actions=high_risk,
            blocked_actions=["wallet.sign_transaction", "hardware.control_device"],
            trust={"signature_status": "unsigned", "trust_level": _trust_for_manifest(manifest)},
            preview_hash=_hash_text(_json(manifest) + _json(required_tools)),
        )
        await self._end_span(span_id, output_data={"preview_hash": preview.preview_hash})
        return preview

    async def _register_skill_tool(
        self,
        skill: dict[str, Any],
        status: str,
        now: str,
    ) -> None:
        await self._task_repo.upsert_tool(
            {
                "tool_name": f"skill.{skill['name']}.run",
                "display_name": skill["display_name"],
                "description": skill.get("description") or f"Run skill {skill['name']}",
                "source": "skill",
                "input_schema": skill.get("input_schema", {}),
                "output_schema": skill.get("output_schema", {}),
                "risk_policy": skill.get("risk_policy", {"default": "R2"}),
                "required_handle_types": [],
                "status": status,
                "bundle_id": skill["bundle_id"],
                "skill_id": skill["skill_id"],
                "adapter_config": {"kind": "skill_runner"},
                "trust_level": "local",
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _skill_run_row_or_error(self, skill_run_id: str) -> dict[str, Any]:
        row = await self._repo.get_skill_run(skill_run_id)
        if row is not None:
            return row
        raise AppError(ErrorCode.SKILL_RUN_FAILED, "Skill run 无法读取", status_code=500)

    async def _event(
        self,
        event_type: str,
        *,
        bundle_id: str | None = None,
        skill_id: str | None = None,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("pevt"),
                "organization_id": "org_default",
                "bundle_id": bundle_id,
                "skill_id": skill_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    def _resolve_bundle_root(self, request: BundleInstallRequest) -> Path:
        if request.source_type != "local_directory":
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "当前阶段仅支持 local_directory 安装源",
                status_code=422,
            )
        root = Path(request.source_uri).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "安装目录不存在", status_code=404)
        return root

    async def _resolve_source(self, request: BundleInstallRequest):
        if self._source_resolver is not None:
            return await self._source_resolver.resolve(request)
        root = self._resolve_bundle_root(request)
        from app.services.skill_source_resolver import ResolvedSkillSource

        return ResolvedSkillSource(
            root=root,
            source_type=request.source_type,
            source_uri=request.source_uri,
        )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=redact(input_data or {}),
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


def _skills_from_manifest(
    bundle_id: str,
    manifest: dict[str, Any],
    skill_md: str,
) -> list[dict[str, Any]]:
    explicit = manifest.get("skills")
    if isinstance(explicit, list) and explicit:
        entries = explicit
    else:
        entries = [{"name": name} for name in manifest.get("entry_skills", [manifest["id"]])]
    skills = []
    for entry in entries:
        name = _safe_id(str(entry.get("name") or entry.get("id") or manifest["id"]))
        skills.append(
            {
                "skill_id": f"skill.{bundle_id}.{name}",
                "name": name,
                "display_name": str(
                    entry.get("display_name") or manifest.get("display_name") or name
                ),
                "description": entry.get("description") or manifest.get("description"),
                "entrypoint_path": str(entry.get("entrypoint_path") or "SKILL.md"),
                "instructions": skill_md,
                "trigger": entry.get("triggers") or manifest.get("triggers", {}),
                "input_schema": entry.get("input_schema") or manifest.get("input_schema", {}),
                "output_schema": entry.get("output_schema") or manifest.get("output_schema", {}),
                "required_tools": entry.get("required_tools") or _manifest_tool_names(manifest),
                "required_assets": entry.get("required_assets")
                or manifest.get("required_assets", []),
                "permission": entry.get("permissions") or manifest.get("permissions", {}),
                "risk_policy": entry.get("risk_policy") or manifest.get("risk_policy", {}),
                "eval_summary": {},
                "steps": entry.get("steps") or manifest.get("steps", []),
            }
        )
    return skills


def _validate_manifest_tool_contract(manifest: dict[str, Any]) -> None:
    manifest_tools = set(_manifest_tool_names(manifest))
    root_steps = _declared_steps(manifest.get("steps"), "steps")
    for tool_name in _step_tool_names(root_steps, "steps"):
        if tool_name not in manifest_tools:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "steps 使用了未声明的 required_tools",
                status_code=422,
                details={"tool_name": tool_name},
            )

    explicit = manifest.get("skills")
    if not isinstance(explicit, list):
        return
    for index, entry in enumerate(explicit, start=1):
        if not isinstance(entry, dict):
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "skills 条目必须是对象",
                status_code=422,
                details={"index": index},
            )
        declared = manifest_tools | _declared_tool_set(
            entry.get("required_tools"),
            f"skills[{index}].required_tools",
        )
        steps = _declared_steps(entry.get("steps", manifest.get("steps")), f"skills[{index}].steps")
        for tool_name in _step_tool_names(steps, f"skills[{index}].steps"):
            if tool_name not in declared:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "Skill steps 使用了未声明的 required_tools",
                    status_code=422,
                    details={"skill_index": index, "tool_name": tool_name},
                )


def _declared_tool_set(raw: Any, field_name: str) -> set[str]:
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            f"{field_name} 必须是列表",
            status_code=422,
        )
    result: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                f"{field_name} 只能包含非空字符串",
                status_code=422,
            )
        result.add(item.strip())
    return result


def _declared_steps(raw: Any, field_name: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            f"{field_name} 必须是列表",
            status_code=422,
        )
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(raw, start=1):
        if not isinstance(step, dict):
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                f"{field_name} 条目必须是对象",
                status_code=422,
                details={"index": index},
            )
        steps.append(step)
    return steps


def _step_tool_names(steps: list[dict[str, Any]], field_name: str) -> list[str]:
    names: list[str] = []
    for index, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name") or step.get("tool") or "").strip()
        if not tool_name:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                f"{field_name} 条目缺少 tool_name",
                status_code=422,
                details={"index": index},
            )
        names.append(tool_name)
    return names


def _manifest_tool_names(manifest: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in manifest.get("required_tools") or []:
        if isinstance(item, str):
            names.append(item)
    permissions = manifest.get("permissions") or {}
    for item in permissions.get("tools") or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str):
            names.append(item)
    return sorted(set(names))


def _manifest_tool_risks(manifest: dict[str, Any]) -> dict[str, str]:
    risks: dict[str, str] = {}
    permissions = manifest.get("permissions") or {}
    for item in permissions.get("tools") or []:
        if isinstance(item, dict) and item.get("name"):
            risks[str(item["name"])] = str(item.get("risk") or "R2")
    return risks


def _eval_cases_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    cases = manifest.get("eval_cases")
    return cases if isinstance(cases, list) else []


def _score_skill(skill: dict[str, Any], text: str) -> tuple[float, str]:
    trigger = skill.get("trigger", {})
    keywords = [str(item).lower() for item in trigger.get("keywords", [])]
    intents = [str(item).lower() for item in trigger.get("intents", [])]
    score = 0.0
    hits = []
    for keyword in keywords:
        if keyword and keyword in text:
            score += 0.35
            hits.append(keyword)
    for intent in intents:
        if intent and intent in text:
            score += 0.45
            hits.append(intent)
    if skill["name"].replace("_", " ") in text:
        score += 0.2
        hits.append(skill["name"])
    if not hits and any(word in text for word in skill["display_name"].lower().split()):
        score += 0.15
        hits.append("display_name")
    return score, f"匹配 {', '.join(hits)}" if hits else ""


def _default_skill_steps(skill: SkillRecord) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": "file.write",
            "args": {
                "path": "outputs/skill-result.md",
                "content": f"# {skill.display_name}\n\nSkill 已按声明式流程完成。",
            },
        }
    ]


def _default_candidate_steps(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": "file.write",
            "args": {
                "path": "outputs/skill-candidate-result.md",
                "content": f"# {candidate['title']}\n\n候选 Skill 已生成草稿输出。",
            },
        }
    ]


def _format_args(
    args: dict[str, Any],
    input_data: dict[str, Any],
    skill: SkillRecord,
) -> dict[str, Any]:
    def replace(value: Any) -> Any:
        if isinstance(value, str):
            placeholder = re.fullmatch(r"\{([A-Za-z0-9_]+)\}", value.strip())
            if placeholder:
                key = placeholder.group(1)
                if key == "skill_display_name":
                    return skill.display_name
                if key in input_data:
                    return input_data[key]
            result = value
            for key, item in input_data.items():
                result = result.replace("{" + key + "}", str(item))
            return result.replace("{skill_display_name}", skill.display_name)
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    return {key: replace(value) for key, value in args.items()}


def _skill_tool_idempotency_key(
    *,
    skill_run_id: str,
    skill_id: str,
    task_id: str | None,
    step_id: str | None,
    index: int,
    tool_name: str,
    approval_id: str | None,
) -> str | None:
    if task_id and step_id:
        suffix = f":approved:{approval_id}" if approval_id else ""
        return f"{task_id}:{step_id}:skill:{skill_id}:{index}:{tool_name}{suffix}"
    return f"{skill_run_id}:{index}:{tool_name}" if task_id else None


def _forbidden_actions(risk_policy: dict[str, Any]) -> set[str]:
    return set(risk_policy.get("forbidden_actions", []))


def _trust_for_manifest(manifest: dict[str, Any]) -> str:
    risk_policy = manifest.get("risk_policy", {})
    high_risk = risk_policy.get("confirmation_required_for", [])
    permissions = manifest.get("permissions", {})
    network = manifest.get("network") or permissions.get("network") or permissions.get("net")
    manifest_risks = _manifest_tool_risks(manifest)
    if network or high_risk or any(_risk_value(risk) >= 3 for risk in manifest_risks.values()):
        return "restricted"
    return "local"


def _risk_value(risk: str) -> int:
    try:
        return int(str(risk).upper().removeprefix("R"))
    except ValueError:
        return 1


def _safe_id(value: str) -> str:
    return re_sub(value.strip().lower())


def re_sub(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)


def _file_type(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().removeprefix(".")
    return suffix or "file"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
