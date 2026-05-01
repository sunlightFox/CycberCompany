from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from core_types import (
    CapabilityDecision,
    ErrorCode,
    PermissionPreview,
    RiskLevel,
    SkillBundleSource,
    SkillBundleVersion,
    SkillEvalBinding,
    SkillGrantRecord,
    SkillOutputTaintRecord,
    SkillPermissionPreviewRecord,
    SkillRecord,
    SkillRollbackPoint,
    SkillStaticAnalysisReport,
)
from safety_service import SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_governance_repo import SkillGovernanceRepository
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.assets import CapabilityGrantCreateRequest
from app.schemas.skill_governance import (
    SkillGrantCreateRequest,
    SkillInstallPreviewRequest,
    SkillInstallPreviewResponse,
    SkillRollbackRequest,
    SkillRollbackResponse,
    SkillUpgradeRequest,
    SkillUpgradeResponse,
)
from app.schemas.skills import BundleInstallRequest
from app.services.audit import AuditEventService
from app.services.capability import CapabilityGraphService, capability_request
from app.services.checkpoints import rollback_availability_for_tool

BLOCKED_TOOL_PATTERNS = {
    "terminal.run:*": "wildcard_terminal",
    "file:**": "wildcard_filesystem",
    "asset.secret:*": "asset_secret_access",
}
SENSITIVE_TEXT_PATTERNS = {
    "api_key": "hardcoded_api_key",
    "password": "hardcoded_password",
    "private_key": "hardcoded_private_key",
    "mnemonic": "hardcoded_mnemonic",
    "cookie": "hardcoded_cookie",
    "token": "hardcoded_token",
    "curl | sh": "pipe_to_shell",
    "rm -rf": "destructive_shell",
    "c:\\users\\": "local_user_path",
    "/users/": "local_user_path",
}
HIGH_RISK_TOOLS = {"terminal.run", "browser.download", "browser.submit", "file.delete"}


class SkillGovernanceService:
    def __init__(
        self,
        *,
        repo: SkillGovernanceRepository,
        skill_repo: SkillMcpRepository,
        task_repo: TaskRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
        capability_service: CapabilityGraphService | None = None,
    ) -> None:
        self._repo = repo
        self._skills = skill_repo
        self._tasks = task_repo
        self._trace = trace_service
        self._audit = audit_service
        self._capability = capability_service
        self._safety = SafetyService()

    async def preview_install(
        self,
        request: SkillInstallPreviewRequest | BundleInstallRequest,
        *,
        trace_id: str | None = None,
    ) -> SkillInstallPreviewResponse:
        root = self._resolve_bundle_root(request)
        manifest, skill_md, manifest_hash = self._load_manifest(root)
        bundle_id = _safe_id(str(manifest.get("id") or root.name))
        preview = await self.build_permission_preview(
            bundle_id=bundle_id,
            manifest=manifest,
            manifest_hash=manifest_hash,
            trace_id=trace_id,
        )
        analysis = await self.analyze_manifest(
            bundle_id=bundle_id,
            manifest=manifest,
            skill_md=skill_md,
            manifest_hash=manifest_hash,
            trace_id=trace_id,
        )
        source = await self.persist_preview_source(
            request=request,
            bundle_id=bundle_id,
            trust_level=analysis.trust_level,
            manifest_hash=manifest_hash,
            trace_id=trace_id,
        )
        governance_preview = await self.persist_permission_preview(
            bundle_id=bundle_id,
            manifest=manifest,
            manifest_hash=manifest_hash,
            preview=preview,
            analysis=analysis,
            trace_id=trace_id,
        )
        return SkillInstallPreviewResponse(
            preview=preview,
            governance_preview=governance_preview,
            static_analysis=analysis,
            source=source,
            version=None,
            blocked=analysis.status == "blocked",
        )

    async def persist_install_governance(
        self,
        *,
        request: BundleInstallRequest,
        bundle: dict[str, Any],
        skills: list[SkillRecord],
        manifest: dict[str, Any],
        skill_md: str,
        manifest_hash: str,
        preview: PermissionPreview,
        trace_id: str | None,
    ) -> None:
        analysis = await self.analyze_manifest(
            bundle_id=str(bundle["bundle_id"]),
            manifest=manifest,
            skill_md=skill_md,
            manifest_hash=manifest_hash,
            trace_id=trace_id,
        )
        await self.persist_preview_source(
            request=request,
            bundle_id=str(bundle["bundle_id"]),
            trust_level=analysis.trust_level,
            manifest_hash=manifest_hash,
            trace_id=trace_id,
        )
        await self.persist_bundle_version(
            bundle=bundle,
            manifest=manifest,
            manifest_hash=manifest_hash,
            analysis=analysis,
            preview=preview,
            trace_id=trace_id,
        )
        await self.persist_permission_preview(
            bundle_id=str(bundle["bundle_id"]),
            manifest=manifest,
            manifest_hash=manifest_hash,
            preview=preview,
            analysis=analysis,
            trace_id=trace_id,
        )
        for skill in skills:
            await self._skills.insert_event(
                {
                    "event_id": new_id("pevt"),
                    "organization_id": "org_default",
                    "bundle_id": skill.bundle_id,
                    "skill_id": skill.skill_id,
                    "event_type": "skill.governance_recorded",
                    "payload": {
                        "skill_id": skill.skill_id,
                        "requires_user_grant": self.requires_runtime_grant(bundle, skill),
                        "analysis_status": analysis.status,
                    },
                    "payload_redacted": redact(
                        {
                            "skill_id": skill.skill_id,
                            "requires_user_grant": self.requires_runtime_grant(bundle, skill),
                            "analysis_status": analysis.status,
                        }
                    ),
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )

    async def build_permission_preview(
        self,
        *,
        bundle_id: str | None,
        manifest: dict[str, Any],
        manifest_hash: str,
        trace_id: str | None,
    ) -> PermissionPreview:
        del trace_id
        declared_tools = await self._declared_tools_with_risk(manifest)
        permission_summary = _permission_summary(manifest, declared_tools)
        risk_level = _max_risk([item["risk_level"] for item in declared_tools] or ["R1"])
        high_risk_actions = [
            {
                "action": item["tool_name"],
                "risk_level": item["risk_level"],
                "approval_required": _risk_order(item["risk_level"]) >= 3,
            }
            for item in declared_tools
            if _risk_order(item["risk_level"]) >= 3
        ]
        blocked_actions = _static_blocked_actions(manifest)
        return PermissionPreview(
            bundle_id=bundle_id,
            summary=(
                f"{manifest.get('display_name') or manifest.get('id')} 需要 "
                f"{len(declared_tools)} 个工具和 "
                f"{len(permission_summary['assets'])} 类资产声明。"
            ),
            required_tools=declared_tools,
            required_assets=permission_summary["assets"],
            network=permission_summary["network"],
            filesystem=permission_summary["filesystem"],
            high_risk_actions=high_risk_actions,
            blocked_actions=blocked_actions,
            trust={
                "signature_status": "unsigned",
                "trust_level": _trust_from_manifest(manifest, risk_level, blocked_actions),
            },
            preview_hash=_hash_text(
                _json(redact(manifest)) + _json(declared_tools) + manifest_hash
            ),
        )

    async def analyze_manifest(
        self,
        *,
        bundle_id: str | None,
        manifest: dict[str, Any],
        skill_md: str,
        manifest_hash: str,
        trace_id: str | None,
    ) -> SkillStaticAnalysisReport:
        text = (_json(manifest) + "\n" + skill_md).lower()
        reason_codes: list[str] = []
        blocked_reasons: list[str] = []
        warnings: list[str] = []
        remediation: list[str] = []
        sensitive_findings: list[dict[str, Any]] = []

        for pattern, code in BLOCKED_TOOL_PATTERNS.items():
            if pattern in text:
                reason_codes.append(code)
                blocked_reasons.append(code)
                remediation.append(f"remove_{code}")

        for pattern, code in SENSITIVE_TEXT_PATTERNS.items():
            if pattern in text:
                reason_codes.append(code)
                sensitive_findings.append({"kind": code, "count": text.count(pattern)})
                sensitive_path_codes = {"sensitive_local_path", "local_user_path"}
                if code.startswith("hardcoded") or code in sensitive_path_codes:
                    blocked_reasons.append(code)
                else:
                    warnings.append(code)

        permissions = manifest.get("permissions", {})
        network = (
            manifest.get("network")
            or permissions.get("network")
            or permissions.get("net")
            or {}
        )
        allowed_domains = (
            [str(item) for item in network.get("allowed_domains", [])]
            if isinstance(network, dict)
            else []
        )
        if "*" in allowed_domains:
            reason_codes.append("network_wildcard_requires_review")
            warnings.append("network_wildcard_requires_review")

        declared_tools = await self._declared_tools_with_risk(manifest)
        risk_level = _max_risk([item["risk_level"] for item in declared_tools] or ["R1"])
        unattended_allowed = _manifest_unattended_allowed(manifest)
        high_risk_tool = any(
            item["tool_name"] in HIGH_RISK_TOOLS or _risk_order(item["risk_level"]) >= 3
            for item in declared_tools
        )
        if unattended_allowed and high_risk_tool:
            reason_codes.append("unattended_high_risk_conflict")
            blocked_reasons.append("unattended_high_risk_conflict")
            remediation.append("set_unattended_allowed_false")

        sensitivity_hits = self._safety.classify_chat_input(text).sensitivity_hits
        if sensitivity_hits:
            reason_codes.append("sensitivity_hits_detected")
            sensitive_findings.append(
                {"kind": "sensitivity_hits", "count": len(sensitivity_hits)}
            )

        blocked = bool(blocked_reasons)
        trust_level = "blocked" if blocked else _trust_from_manifest(manifest, risk_level, [])
        report = {
            "analysis_report_id": new_id("skana"),
            "organization_id": "org_default",
            "bundle_id": bundle_id,
            "bundle_revision": str(
                manifest.get("bundle_revision") or manifest.get("version") or "1.0.0"
            ),
            "manifest_hash": manifest_hash,
            "status": "blocked" if blocked else "passed_with_warnings" if warnings else "passed",
            "risk_level": risk_level,
            "trust_level": trust_level,
            "reason_codes": sorted(set(reason_codes)),
            "blocked_reasons": sorted(set(blocked_reasons)),
            "warnings": sorted(set(warnings)),
            "remediation_hints": sorted(set(remediation)),
            "sensitive_findings": sensitive_findings,
            "manifest_summary": {
                "bundle_id": bundle_id,
                "tools": [item["tool_name"] for item in declared_tools],
                "permission_model": "manifest_v2" if _is_manifest_v2(manifest) else "legacy",
                "unattended_allowed": unattended_allowed and not blocked,
            },
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_static_analysis(report)
        return SkillStaticAnalysisReport(**report)

    async def persist_preview_source(
        self,
        *,
        request: SkillInstallPreviewRequest | BundleInstallRequest,
        bundle_id: str,
        trust_level: str,
        manifest_hash: str,
        trace_id: str | None,
    ) -> SkillBundleSource:
        now = utc_now_iso()
        source_uri = str(request.source_uri)
        data = {
            "source_id": f"sksrc_{_hash_text(bundle_id + source_uri)[:18]}",
            "organization_id": "org_default",
            "bundle_id": bundle_id,
            "source_type": request.source_type,
            "source_uri_redacted": f"{request.source_type}:{Path(source_uri).name}",
            "source_uri_hash": _hash_text(source_uri),
            "signature_status": "unsigned",
            "checksum": manifest_hash,
            "trust_level": trust_level,
            "metadata": {"source_policy": "local_only", "raw_uri_stored": False},
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_source(data)
        return SkillBundleSource(**data)

    async def persist_bundle_version(
        self,
        *,
        bundle: dict[str, Any],
        manifest: dict[str, Any],
        manifest_hash: str,
        analysis: SkillStaticAnalysisReport,
        preview: PermissionPreview,
        trace_id: str | None,
    ) -> SkillBundleVersion:
        now = utc_now_iso()
        data = {
            "version_id": new_id("skver"),
            "organization_id": "org_default",
            "bundle_id": bundle["bundle_id"],
            "bundle_revision": str(bundle["bundle_revision"]),
            "manifest_hash": manifest_hash,
            "signature_status": bundle.get("signature_status", "unsigned"),
            "trust_level": analysis.trust_level,
            "permission_summary": preview.model_dump(mode="json"),
            "risk_summary": {
                "risk_level": analysis.risk_level,
                "blocked_reasons": analysis.blocked_reasons,
                "reason_codes": analysis.reason_codes,
            },
            "manifest_redacted": redact(manifest),
            "status": "active",
            "installed_by_member_id": bundle.get("installed_by_member_id"),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_version(data)
        return SkillBundleVersion(**data)

    async def persist_permission_preview(
        self,
        *,
        bundle_id: str,
        manifest: dict[str, Any],
        manifest_hash: str,
        preview: PermissionPreview,
        analysis: SkillStaticAnalysisReport,
        trace_id: str | None,
    ) -> SkillPermissionPreviewRecord:
        data = {
            "preview_id": new_id("skprev"),
            "organization_id": "org_default",
            "bundle_id": bundle_id,
            "bundle_revision": str(
                manifest.get("bundle_revision") or manifest.get("version") or "1.0.0"
            ),
            "manifest_hash": manifest_hash,
            "trust_level": analysis.trust_level,
            "risk_level": analysis.risk_level,
            "permission_summary": {
                "tools": [item["tool_name"] for item in preview.required_tools],
                "assets": preview.required_assets,
                "network": preview.network,
                "filesystem": preview.filesystem,
                "high_risk_actions": preview.high_risk_actions,
            },
            "blocked_reasons": analysis.blocked_reasons,
            "requires_user_grant": True,
            "unattended_allowed": _manifest_unattended_allowed(manifest)
            and analysis.status != "blocked"
            and _risk_order(analysis.risk_level) <= 2,
            "preview_hash": preview.preview_hash,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_permission_preview(data)
        return SkillPermissionPreviewRecord(**data)

    async def create_grant(
        self,
        skill_id: str,
        request: SkillGrantCreateRequest,
        *,
        trace_id: str | None,
    ) -> SkillGrantRecord:
        skill = await self._get_skill(skill_id)
        allowed_tools = request.allowed_tools or list(skill.required_tools)
        now = utc_now_iso()
        data = {
            "skill_grant_id": new_id("skgrant"),
            "organization_id": "org_default",
            "skill_id": skill.skill_id,
            "bundle_id": skill.bundle_id,
            "subject_type": request.subject_type,
            "subject_id": request.subject_id,
            "allowed_tools": sorted(set(allowed_tools)),
            "allowed_asset_actions": sorted(set(request.allowed_asset_actions)),
            "allowed_mcp_tools": sorted(set(request.allowed_mcp_tools)),
            "denied_actions": sorted(set(request.denied_actions)),
            "approval_policy": request.approval_policy,
            "status": "active",
            "grant_scope": request.grant_scope,
            "created_by_member_id": request.created_by_member_id,
            "expires_at": request.expires_at,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_grant(data)
        capability_edge_id = None
        if self._capability is not None:
            edge = await self._capability.create_grant(
                CapabilityGrantCreateRequest(
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                    object_type="skill",
                    object_id=skill.skill_id,
                    action="skill.run",
                    effect="allow",
                    risk_level=_risk_level_enum(_skill_risk_level(skill)),
                    condition={
                        "skill_grant_id": data["skill_grant_id"],
                        "allowed_tools": data["allowed_tools"],
                        "allowed_asset_actions": data["allowed_asset_actions"],
                        "grant_scope": data["grant_scope"],
                    },
                    source_type="skill_grant",
                    source_id=data["skill_grant_id"],
                    valid_to=request.expires_at,
                ),
                trace_id=trace_id,
            )
            capability_edge_id = edge.edge_id
        await self._audit.write_event(
            actor_type="member",
            actor_id=request.created_by_member_id,
            action="skill.grant_created",
            object_type="skill",
            object_id=skill_id,
            summary="Skill 授权已创建",
            payload=redact(
                {
                    "skill_id": skill_id,
                    "allowed_tools": allowed_tools,
                    "capability_edge_id": capability_edge_id,
                    "capability_fact_source": "capability_graph",
                }
            ),
            trace_id=trace_id,
        )
        return SkillGrantRecord(**data)

    async def list_grants(self, skill_id: str) -> list[SkillGrantRecord]:
        await self._get_skill(skill_id)
        return [SkillGrantRecord(**row) for row in await self._repo.list_grants(skill_id)]

    async def revoke_skill(
        self,
        skill_id: str,
        *,
        actor_member_id: str,
        reason: str | None,
        trace_id: str | None,
    ) -> dict[str, Any]:
        skill = await self._get_skill(skill_id)
        now = utc_now_iso()
        revoked_grants = await self._repo.revoke_grants(
            skill_id,
            actor_member_id=actor_member_id,
            reason=reason,
            revoked_at=now,
        )
        revoked_capability_edges = await self._revoke_skill_capability_edges(
            skill_id,
            trace_id=trace_id,
        )
        await self._skills.update_skill(skill_id, {"status": "revoked", "updated_at": now})
        await self._skills.insert_event(
            {
                "event_id": new_id("pevt"),
                "organization_id": "org_default",
                "bundle_id": skill.bundle_id,
                "skill_id": skill.skill_id,
                "event_type": "skill.revoked",
                "payload": {
                    "reason": reason,
                    "revoked_grants": revoked_grants,
                    "revoked_capability_edges": revoked_capability_edges,
                    "capability_fact_source": "capability_graph",
                },
                "payload_redacted": redact(
                    {
                        "reason": reason,
                        "revoked_grants": revoked_grants,
                        "revoked_capability_edges": revoked_capability_edges,
                        "capability_fact_source": "capability_graph",
                    }
                ),
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        return {
            "skill_id": skill_id,
            "status": "revoked",
            "revoked_grants": revoked_grants,
            "revoked_capability_edges": revoked_capability_edges,
        }

    async def upgrade_skill(
        self,
        skill_id: str,
        request: SkillUpgradeRequest,
        *,
        trace_id: str | None,
    ) -> SkillUpgradeResponse:
        skill_row = await self._get_skill_row(skill_id)
        bundle_row = await self._get_bundle_row(skill_row["bundle_id"])
        rollback = await self._create_rollback_point(
            skill_row=skill_row,
            bundle_row=bundle_row,
            reason=request.reason or "before_upgrade",
            actor_member_id=request.actor_member_id,
            trace_id=trace_id,
        )
        now = utc_now_iso()
        manifest = {**bundle_row["manifest"], **request.manifest_patch}
        if request.bundle_revision:
            manifest["bundle_revision"] = request.bundle_revision
        if request.display_name:
            skill_row["display_name"] = request.display_name
        if request.description is not None:
            skill_row["description"] = request.description
        if request.required_tools is not None:
            skill_row["required_tools"] = request.required_tools
            manifest["required_tools"] = request.required_tools
        if request.steps is not None:
            skill_row["steps"] = request.steps
            manifest["steps"] = request.steps
        bundle_revision = str(
            request.bundle_revision
            or manifest.get("bundle_revision")
            or bundle_row["bundle_revision"]
        )
        manifest_hash = _hash_text(_json(redact(manifest)))
        await self._skills.update_skill(
            skill_id,
            {
                "display_name": skill_row["display_name"],
                "description": skill_row.get("description"),
                "required_tools": skill_row["required_tools"],
                "steps": skill_row["steps"],
                "updated_at": now,
            },
        )
        await self._skills.update_bundle(
            bundle_row["bundle_id"],
            {
                "bundle_revision": bundle_revision,
                "manifest_hash": manifest_hash,
                "manifest": manifest,
                "updated_at": now,
            },
        )
        updated_skill = await self._get_skill_row(skill_id)
        updated_bundle = await self._get_bundle_row(bundle_row["bundle_id"])
        return SkillUpgradeResponse(
            rollback_point=rollback,
            skill=redact(updated_skill),
            bundle=redact(updated_bundle),
        )

    async def rollback_skill(
        self,
        skill_id: str,
        request: SkillRollbackRequest,
        *,
        trace_id: str | None,
    ) -> SkillRollbackResponse:
        point = (
            await self._repo.get_rollback_point(request.rollback_point_id)
            if request.rollback_point_id
            else await self._repo.latest_rollback_point(skill_id)
        )
        if point is None:
            raise AppError(ErrorCode.NOT_FOUND, "Skill rollback point 不存在", status_code=404)
        skill_snapshot = point["skill_snapshot"]
        bundle_snapshot = point["bundle_snapshot"]
        now = utc_now_iso()
        await self._skills.update_skill(
            skill_id,
            {
                "display_name": skill_snapshot.get("display_name"),
                "description": skill_snapshot.get("description"),
                "required_tools": skill_snapshot.get("required_tools", []),
                "steps": skill_snapshot.get("steps", []),
                "updated_at": now,
            },
        )
        await self._skills.update_bundle(
            point["bundle_id"],
            {
                "bundle_revision": bundle_snapshot.get("bundle_revision", point["from_revision"]),
                "manifest_hash": bundle_snapshot.get("manifest_hash", point["manifest_hash"]),
                "manifest": bundle_snapshot.get("manifest", {}),
                "updated_at": now,
            },
        )
        return SkillRollbackResponse(
            rollback_point=SkillRollbackPoint(**point),
            skill=redact(await self._get_skill_row(skill_id)),
            bundle=redact(await self._get_bundle_row(point["bundle_id"])),
        )

    async def list_analysis(self, skill_id: str) -> list[SkillStaticAnalysisReport]:
        skill = await self._get_skill(skill_id)
        rows = await self._repo.list_static_analysis(skill.bundle_id)
        return [SkillStaticAnalysisReport(**row) for row in rows]

    async def list_eval_bindings(self, skill_id: str) -> list[SkillEvalBinding]:
        await self._get_skill(skill_id)
        return [SkillEvalBinding(**row) for row in await self._repo.list_eval_bindings(skill_id)]

    async def list_output_taints(self, skill_id: str) -> list[SkillOutputTaintRecord]:
        await self._get_skill(skill_id)
        return [
            SkillOutputTaintRecord(**row)
            for row in await self._repo.list_output_taints(skill_id)
        ]

    async def record_eval_binding(
        self,
        *,
        skill: SkillRecord,
        eval_run_id: str,
        status: str,
        trace_id: str | None,
    ) -> None:
        bundle = await self._get_bundle_row(skill.bundle_id)
        risk_level = _skill_risk_level(skill)
        await self._repo.insert_eval_binding(
            {
                "binding_id": new_id("skbind"),
                "organization_id": "org_default",
                "skill_id": skill.skill_id,
                "bundle_id": skill.bundle_id,
                "bundle_revision": str(bundle["bundle_revision"]),
                "manifest_hash": str(bundle["manifest_hash"]),
                "eval_run_id": eval_run_id,
                "capability_scope": {
                    "tools": skill.required_tools,
                    "assets": skill.required_assets,
                    "manifest_version": "v2" if _is_manifest_v2(bundle["manifest"]) else "legacy",
                },
                "risk_level": risk_level,
                "status": status,
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def ensure_skill_run_allowed(
        self,
        *,
        skill: SkillRecord,
        bundle: dict[str, Any],
        owner_member_id: str,
        steps: list[dict[str, Any]],
        input_data: dict[str, Any],
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        manifest = bundle.get("manifest", {})
        grant_required = self.requires_runtime_grant(bundle, skill)
        tool_names = [
            str(step.get("tool_name") or step.get("tool") or "")
            for step in steps
            if str(step.get("tool_name") or step.get("tool") or "")
        ]
        snapshot: dict[str, Any] = {
            "governance": "phase48",
            "grant_required": grant_required,
            "skill_id": skill.skill_id,
            "tool_names": tool_names,
            "manifest_version": "v2" if _is_manifest_v2(manifest) else "legacy",
            "trust_level": str(bundle.get("trust_level") or "unknown"),
            "signature_status": str(bundle.get("signature_status") or "unknown"),
            "capability_entrypoint": "capability_graph",
            "safety_entrypoint": "skill_governance_preflight",
            "tool_runtime_boundary": "required",
            "checkpoint_requirements": _checkpoint_requirements_for_steps(steps),
            "trace_id": trace_id,
        }
        if any(
            item.get("checkpoint_required") is True
            for item in snapshot["checkpoint_requirements"]
        ) and not task_id:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "Skill 文件变更需要绑定任务以创建 checkpoint",
                status_code=409,
                details={"reason_code": "skill_checkpoint_requires_task_binding"},
            )
        if not grant_required:
            snapshot["decision"] = "allow_legacy_compatibility"
            return redact(snapshot)

        grant = await self._repo.active_grant(skill.skill_id, "member", owner_member_id)
        if grant is None:
            raise AppError(
                ErrorCode.CAPABILITY_DENIED,
                "Skill 未获得当前成员授权",
                status_code=403,
                details={"skill_id": skill.skill_id, "reason_code": "skill_grant_missing"},
            )
        denied_actions = set(grant["denied_actions"])
        allowed_tools = set(grant["allowed_tools"])
        capability_decision = await self._decide_skill_run_capability(
            skill=skill,
            owner_member_id=owner_member_id,
            grant=grant,
            tool_names=tool_names,
            trace_id=trace_id,
        )
        if capability_decision is not None and not capability_decision.allowed:
            raise AppError(
                ErrorCode.CAPABILITY_DENIED,
                "Capability Graph 拒绝 Skill 执行",
                status_code=403,
                details={
                    "skill_id": skill.skill_id,
                    "reason_code": "skill_capability_graph_denied",
                    "capability_decision_id": capability_decision.decision_id,
                    "capability_reason": capability_decision.reason,
                },
            )
        for tool_name in tool_names:
            if tool_name in denied_actions:
                raise AppError(
                    ErrorCode.CAPABILITY_DENIED,
                    "Skill grant 显式拒绝该工具",
                    status_code=403,
                    details={"tool_name": tool_name, "reason_code": "skill_tool_denied"},
                )
            if tool_name not in allowed_tools:
                raise AppError(
                    ErrorCode.CAPABILITY_DENIED,
                    "Skill grant 未授权该工具",
                    status_code=403,
                    details={"tool_name": tool_name, "reason_code": "skill_tool_not_granted"},
                )

        attendance = _attendance(input_data)
        unattended_policy = await self._unattended_policy(
            skill=skill,
            manifest=manifest,
            tool_names=tool_names,
            attendance=attendance,
            trust_level=str(bundle.get("trust_level") or "unknown"),
            signature_status=str(bundle.get("signature_status") or "unknown"),
        )
        if not unattended_policy["allowed"]:
            raise AppError(
                ErrorCode.SAFETY_BLOCKED,
                "Skill 不允许无人值守执行",
                status_code=403,
                details={"reason_code": unattended_policy["reason_code"]},
            )

        snapshot["decision"] = "allow_with_grant"
        snapshot["skill_grant_id"] = grant["skill_grant_id"]
        snapshot["allowed_tools"] = sorted(allowed_tools)
        snapshot["denied_actions"] = sorted(denied_actions)
        snapshot["capability_graph"] = _capability_snapshot(capability_decision, grant)
        snapshot["unattended_policy"] = unattended_policy
        return redact(snapshot)

    async def _decide_skill_run_capability(
        self,
        *,
        skill: SkillRecord,
        owner_member_id: str,
        grant: dict[str, Any],
        tool_names: list[str],
        trace_id: str | None,
    ) -> CapabilityDecision | None:
        if self._capability is None:
            return None
        decision = await self._capability.decide(
            capability_request(
                subject_type="member",
                subject_id=owner_member_id,
                object_type="skill",
                object_id=skill.skill_id,
                action="skill.run",
                context={
                    "skill_grant_id": grant["skill_grant_id"],
                    "required_tools": sorted(set(tool_names)),
                    "fact_source": "capability_graph",
                    "governance": "phase48",
                },
            ),
            trace_id=trace_id,
        )
        if not decision.allowed and decision.reason == "no_matching_grant":
            await self._capability.create_grant(
                CapabilityGrantCreateRequest(
                    subject_type="member",
                    subject_id=owner_member_id,
                    object_type="skill",
                    object_id=skill.skill_id,
                    action="skill.run",
                    effect="allow",
                    risk_level=_risk_level_enum(_skill_risk_level(skill)),
                    condition={
                        "skill_grant_id": grant["skill_grant_id"],
                        "allowed_tools": grant.get("allowed_tools", []),
                        "allowed_asset_actions": grant.get("allowed_asset_actions", []),
                        "grant_scope": grant.get("grant_scope", "explicit"),
                        "synced_from_existing_skill_grant": True,
                    },
                    source_type="skill_grant_sync",
                    source_id=grant["skill_grant_id"],
                    valid_to=grant.get("expires_at"),
                ),
                trace_id=trace_id,
            )
            decision = await self._capability.decide(
                capability_request(
                    subject_type="member",
                    subject_id=owner_member_id,
                    object_type="skill",
                    object_id=skill.skill_id,
                    action="skill.run",
                    context={
                        "skill_grant_id": grant["skill_grant_id"],
                        "required_tools": sorted(set(tool_names)),
                        "fact_source": "capability_graph",
                        "governance": "phase48",
                        "synced_from_existing_skill_grant": True,
                    },
                ),
                trace_id=trace_id,
            )
        return decision

    async def _revoke_skill_capability_edges(
        self,
        skill_id: str,
        *,
        trace_id: str | None,
    ) -> int:
        if self._capability is None:
            return 0
        revoked = 0
        for edge in await self._capability.list_grants(
            object_type="skill",
            object_id=skill_id,
            limit=200,
        ):
            if edge.status != "active":
                continue
            if edge.source_type not in {"skill_grant", "skill_grant_sync"}:
                continue
            await self._capability.delete_grant(edge.edge_id, trace_id=trace_id)
            revoked += 1
        return revoked

    async def _unattended_policy(
        self,
        *,
        skill: SkillRecord,
        manifest: dict[str, Any],
        tool_names: list[str],
        attendance: str,
        trust_level: str,
        signature_status: str,
    ) -> dict[str, Any]:
        policy = {
            "attendance": attendance,
            "required": attendance == "unattended",
            "manifest_unattended_allowed": _manifest_unattended_allowed(manifest),
            "trust_level": trust_level,
            "signature_status": signature_status,
            "eval_binding_required": False,
            "eval_binding_status": "not_required",
            "allowed": True,
            "reason_code": "attended_skill_run",
        }
        if attendance != "unattended":
            return policy
        policy["eval_binding_required"] = True
        risk_level = _skill_risk_level(skill)
        if trust_level in {"blocked", "unknown"} or signature_status == "invalid":
            policy.update(
                {
                    "allowed": False,
                    "reason_code": "unattended_skill_trust_blocked",
                    "risk_level": risk_level,
                }
            )
            return policy
        if (
            not _manifest_unattended_allowed(manifest)
            or any(tool in HIGH_RISK_TOOLS for tool in tool_names)
            or _risk_order(risk_level) >= 3
        ):
            policy.update(
                {
                    "allowed": False,
                    "reason_code": "unattended_skill_blocked",
                    "risk_level": risk_level,
                }
            )
            return policy
        bindings = await self._repo.list_eval_bindings(skill.skill_id)
        passed = [
            binding
            for binding in bindings
            if str(binding.get("status") or "").lower() in {"passed", "completed", "pass"}
        ]
        policy["eval_binding_status"] = "passed" if passed else "missing_or_failed"
        policy["latest_eval_run_id"] = passed[0]["eval_run_id"] if passed else None
        if not passed:
            policy.update(
                {
                    "allowed": False,
                    "reason_code": "unattended_skill_eval_binding_missing",
                    "risk_level": risk_level,
                }
            )
            return policy
        policy.update(
            {
                "allowed": True,
                "reason_code": "unattended_skill_eval_binding_passed",
                "risk_level": risk_level,
            }
        )
        return policy

    async def record_skill_output_taint(
        self,
        *,
        skill: SkillRecord,
        skill_run_id: str,
        task_id: str | None,
        output: dict[str, Any],
        policy_snapshot: dict[str, Any],
        trace_id: str | None,
    ) -> SkillOutputTaintRecord:
        output_text = _json(output)
        redacted_output = redact(output)
        redacted_text = _json(redacted_output)
        changed = redacted_text != output_text
        findings = []
        if changed:
            findings.append({"kind": "redaction_applied", "count": 1})
        hits = self._safety.classify_chat_input(output_text).sensitivity_hits
        if hits:
            findings.append({"kind": "sensitivity_hits", "count": len(hits)})
        data = {
            "taint_record_id": new_id("sktaint"),
            "organization_id": "org_default",
            "skill_id": skill.skill_id,
            "bundle_id": skill.bundle_id,
            "skill_run_id": skill_run_id,
            "task_id": task_id,
            "taint_source": "skill_output",
            "output_hash": _hash_text(redacted_text),
            "output_preview": redacted_text[:512],
            "untrusted_external_content": True,
            "dlp_findings": findings,
            "redaction_summary": {"redaction_applied": changed, "finding_count": len(findings)},
            "guard_decision": "allow_as_untrusted_redacted",
            "policy_snapshot": policy_snapshot,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_output_taint(data)
        return SkillOutputTaintRecord(**data)

    def requires_runtime_grant(self, bundle: dict[str, Any], skill: SkillRecord) -> bool:
        manifest = bundle.get("manifest", {})
        return _is_manifest_v2(manifest) or bool(skill.permission.get("tools"))

    async def _declared_tools_with_risk(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        names = _manifest_tool_names(manifest)
        result: list[dict[str, Any]] = []
        risk_by_tool = _manifest_tool_risks(manifest)
        for tool_name in names:
            tool = await self._tasks.get_tool(tool_name)
            fallback_risk = "R4" if tool_name in HIGH_RISK_TOOLS else "R2"
            policy = (tool or {}).get("risk_policy", {"default": fallback_risk})
            result.append(
                {
                    "tool_name": tool_name,
                    "risk_level": str(
                        risk_by_tool.get(tool_name) or policy.get("default", fallback_risk)
                    ),
                }
            )
        return result

    async def _get_skill(self, skill_id: str) -> SkillRecord:
        row = await self._skills.get_skill(skill_id)
        if row is None:
            raise AppError(ErrorCode.SKILL_NOT_FOUND, "Skill 不存在", status_code=404)
        return SkillRecord(**row)

    async def _get_skill_row(self, skill_id: str) -> dict[str, Any]:
        row = await self._skills.get_skill(skill_id)
        if row is None:
            raise AppError(ErrorCode.SKILL_NOT_FOUND, "Skill 不存在", status_code=404)
        return row

    async def _get_bundle_row(self, bundle_id: str) -> dict[str, Any]:
        row = await self._skills.get_bundle(bundle_id)
        if row is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "插件包不存在", status_code=404)
        return row

    async def _create_rollback_point(
        self,
        *,
        skill_row: dict[str, Any],
        bundle_row: dict[str, Any],
        reason: str,
        actor_member_id: str,
        trace_id: str | None,
    ) -> SkillRollbackPoint:
        data = {
            "rollback_point_id": new_id("skrb"),
            "organization_id": "org_default",
            "skill_id": skill_row["skill_id"],
            "bundle_id": bundle_row["bundle_id"],
            "from_revision": str(bundle_row["bundle_revision"]),
            "manifest_hash": str(bundle_row["manifest_hash"]),
            "skill_snapshot": redact(skill_row),
            "bundle_snapshot": redact(bundle_row),
            "reason": reason,
            "created_by_member_id": actor_member_id,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_rollback_point(data)
        return SkillRollbackPoint(**data)

    def _resolve_bundle_root(
        self,
        request: SkillInstallPreviewRequest | BundleInstallRequest,
    ) -> Path:
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

    def _load_manifest(self, root: Path) -> tuple[dict[str, Any], str, str]:
        manifest_path = root / "bundle.yaml"
        skill_path = root / "SKILL.md"
        if not manifest_path.exists() or not skill_path.exists():
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "缺少 bundle.yaml 或 SKILL.md",
                status_code=422,
            )
        manifest_text = manifest_path.read_text(encoding="utf-8")
        skill_md = skill_path.read_text(encoding="utf-8")
        manifest = yaml.safe_load(manifest_text)
        if not isinstance(manifest, dict):
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "bundle.yaml 必须是对象",
                status_code=422,
            )
        return manifest, skill_md, _hash_text(manifest_text + "\n" + skill_md)


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


def _permission_summary(
    manifest: dict[str, Any],
    declared_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    permissions = manifest.get("permissions") or {}
    network = manifest.get("network") or permissions.get("network") or permissions.get("net") or {}
    filesystem = (
        manifest.get("filesystem") or permissions.get("filesystem") or permissions.get("fs") or {}
    )
    assets = permissions.get("assets") or manifest.get("required_assets") or []
    return {
        "tools": [item["tool_name"] for item in declared_tools],
        "assets": assets if isinstance(assets, list) else [],
        "network": network if isinstance(network, dict) else {},
        "filesystem": filesystem if isinstance(filesystem, dict) else {},
    }


def _static_blocked_actions(manifest: dict[str, Any]) -> list[str]:
    text = _json(manifest).lower()
    blocked = [
        action
        for action, reason in BLOCKED_TOOL_PATTERNS.items()
        if action in text or reason in text
    ]
    return sorted(set(blocked))


def _trust_from_manifest(
    manifest: dict[str, Any],
    risk_level: str,
    blocked_actions: list[str],
) -> str:
    if blocked_actions:
        return "blocked"
    permissions = manifest.get("permissions") or {}
    network = manifest.get("network") or permissions.get("network") or permissions.get("net")
    if network or _risk_order(risk_level) >= 3 or _is_manifest_v2(manifest):
        return "restricted"
    return "local"


def _is_manifest_v2(manifest: dict[str, Any]) -> bool:
    revision = str(manifest.get("bundle_revision") or manifest.get("version") or "")
    permissions = manifest.get("permissions") or {}
    return (
        str(manifest.get("manifest_version") or "") == "2"
        or revision.startswith("2")
        or isinstance(permissions.get("tools"), list)
    )


def _manifest_unattended_allowed(manifest: dict[str, Any]) -> bool:
    safety = manifest.get("safety") or {}
    return bool(safety.get("unattended_allowed"))


def _skill_risk_level(skill: SkillRecord) -> str:
    risks = [str(skill.risk_policy.get("default") or "R2")]
    for tool_name in skill.required_tools:
        if tool_name in HIGH_RISK_TOOLS:
            risks.append("R4")
    return _max_risk(risks)


def _risk_level_enum(value: str) -> RiskLevel:
    try:
        return RiskLevel(str(value).upper())
    except ValueError:
        return RiskLevel.R2


def _checkpoint_requirements_for_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name") or step.get("tool") or "")
        if not tool_name:
            continue
        args = dict(step.get("args", {})) if isinstance(step.get("args"), dict) else {}
        availability = rollback_availability_for_tool(tool_name, args)
        requirement = {
            "step_index": index,
            "tool_name": tool_name,
            **availability,
            "governance": "phase48",
        }
        if tool_name in {"file.write", "file.delete", "file.move"}:
            requirement["checkpoint_required"] = True
            requirement["requirement_reason"] = "skill_file_mutation_pre_checkpoint"
        if tool_name == "media.render_edit":
            requirement.update(
                {
                    "rollback_available": False,
                    "checkpoint_required": False,
                    "reason": "rendered_media_derivative_requires_edit_plan_replay",
                    "non_restorable_reason": "media_render_is_derivative_not_original_mutation",
                    "compensating_action": "rerender_from_media_edit_plan",
                }
            )
        requirements.append(redact(requirement))
    return requirements


def _capability_snapshot(
    decision: CapabilityDecision | None,
    grant: dict[str, Any],
) -> dict[str, Any]:
    if decision is None:
        return {
            "fact_source": "capability_graph",
            "status": "not_configured",
            "fallback": "skill_grant_only",
            "skill_grant_id": grant["skill_grant_id"],
        }
    return {
        "fact_source": "capability_graph",
        "decision_id": decision.decision_id,
        "allowed": decision.allowed,
        "approval_required": decision.approval_required,
        "risk_level": decision.risk_level.value,
        "reason": decision.reason,
        "policy_sources": decision.policy_sources,
        "skill_grant_id": grant["skill_grant_id"],
    }


def _attendance(input_data: dict[str, Any]) -> str:
    if str(input_data.get("attendance") or "").lower() == "unattended":
        return "unattended"
    execution_context = input_data.get("execution_context")
    if isinstance(execution_context, dict):
        return str(execution_context.get("attendance") or "").lower()
    return "attended"


def _risk_order(risk: str) -> int:
    try:
        return int(str(risk).upper().removeprefix("R"))
    except ValueError:
        return 1


def _max_risk(values: list[str]) -> str:
    return max((str(item).upper() for item in values), key=_risk_order, default="R1")


def _safe_id(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"_", "-", "."} else "_"
        for char in value.strip().lower()
    )


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
