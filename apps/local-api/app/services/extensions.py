from __future__ import annotations

from typing import Any

from core_types import (
    CanonicalCompatibilityReport,
    CanonicalExtensionPackage,
    ErrorCode,
    PluginBundle,
    SkillRecord,
)

from app.core.errors import AppError
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.extensions import (
    ExtensionBindingResponse,
    ExtensionDiagnosticResponse,
    ExtensionImportRequest,
    ExtensionPlanRunRequest,
    ExtensionPlanRunResponse,
    ExtensionPreviewResponse,
    ExtensionTaskLaunchRequest,
)
from app.schemas.skills import BundleInstallRequest, SkillMatchRequest
from app.schemas.tasks import TaskCreateRequest
from app.services.extensions_compat import (
    bind_canonical_package,
    import_extension_from_root,
    skill_rows_from_package,
)
from app.services.extension_runtime import (
    ExtensionRuntimeActivationContext,
    ExtensionRuntimeActivationResult,
    ExtensionRuntimeContributionDraft,
    ExtensionRuntimeDriverRegistry,
)
from app.services.skill_plugin import SkillPluginService
from app.services.skill_source_resolver import SkillSourceResolver


class ExtensionService:
    def __init__(
        self,
        *,
        repo: SkillMcpRepository,
        task_repo: TaskRepository,
        source_resolver: SkillSourceResolver,
        skill_plugin_service: SkillPluginService,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo
        self._source_resolver = source_resolver
        self._skill_plugin = skill_plugin_service
        self._runtime_drivers = ExtensionRuntimeDriverRegistry()
        self._task_engine: Any | None = None

    def set_task_engine(self, task_engine: Any) -> None:
        self._task_engine = task_engine

    async def preview_import(
        self,
        request: ExtensionImportRequest,
    ) -> ExtensionPreviewResponse:
        resolved = await self._source_resolver.resolve(_bundle_request(request))
        imported = import_extension_from_root(
            resolved.root,
            source_type=resolved.source_type,
            source_uri=resolved.source_uri,
        )
        bound_package = await self._bind_package(imported.package)
        bundle_preview = PluginBundle(
            bundle_id=bound_package.bundle_id,
            organization_id="org_default",
            extension_id=bound_package.extension_id,
            display_name=bound_package.display_name,
            description=bound_package.description,
            author=imported.synthesized_manifest.get("author"),
            bundle_revision=str(bound_package.version or "1.0.0"),
            package_kind=bound_package.package_kind,
            source_type=resolved.source_type,
            source_format=bound_package.source_format,
            source_uri=resolved.source_uri,
            package_uri=f"bundle://{bound_package.bundle_id}",
            manifest_hash=imported.manifest_hash,
            canonical_version=bound_package.canonical_version,
            compatibility_status=bound_package.compatibility_status,
            compatibility_notes=bound_package.compatibility_notes,
            signature_status="unsigned",
            trust_level=bound_package.trust_level,
            status="preview",
            binding_status=_bundle_binding_status(bound_package),
            binding_summary=_bundle_binding_summary(bound_package),
            permission_summary=imported.permission_preview.model_dump(mode="json"),
            risk_summary={},
            manifest=imported.synthesized_manifest,
            canonical_snapshot=bound_package.model_dump(mode="json"),
        )
        skills_preview = [
            SkillRecord(
                skill_id=row["skill_id"],
                organization_id="org_default",
                bundle_id=bound_package.bundle_id,
                extension_id=bound_package.extension_id,
                name=row["name"],
                display_name=row["display_name"],
                description=row.get("description"),
                entrypoint_path=row["entrypoint_path"],
                instructions=row["instructions"],
                runtime_kind=row["runtime_kind"],
                source_format=bound_package.source_format,
                canonical_version=bound_package.canonical_version,
                compatibility_status=row["compatibility_status"],
                compatibility_notes=row["compatibility_notes"],
                binding_status=row["binding_status"],
                binding_summary=row["binding_summary"],
                instruction_spec=row["instruction_spec"],
                execution_binding=row["execution_binding"],
                trigger=row["trigger"],
                input_schema=row["input_schema"],
                output_schema=row["output_schema"],
                required_tools=row["required_tools"],
                required_assets=row["required_assets"],
                permission=row["permission"],
                risk_policy=row["risk_policy"],
                eval_summary={},
                steps=row["steps"],
                status="preview",
            )
            for row in skill_rows_from_package(bound_package)
        ]
        return ExtensionPreviewResponse(
            extension_id=bound_package.extension_id,
            package_kind=bound_package.package_kind,
            source_format=bound_package.source_format,
            canonical_version=bound_package.canonical_version,
            compatibility_status=bound_package.compatibility_status,
            compatibility_notes=bound_package.compatibility_notes,
            permission_preview=imported.permission_preview,
            bundle_preview=bundle_preview,
            skills_preview=skills_preview,
        )

    async def install(
        self,
        request: ExtensionImportRequest,
        *,
        trace_id: str | None = None,
    ) -> tuple[PluginBundle, list[SkillRecord], Any]:
        return await self._skill_plugin.install_bundle(_bundle_request(request), trace_id=trace_id)

    async def list_extensions(self) -> list[PluginBundle]:
        return await self._skill_plugin.list_bundles()

    async def get_extension(self, extension_id: str) -> PluginBundle:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        return await self._skill_plugin.get_bundle(package["bundle_id"])

    async def compatibility(self, extension_id: str) -> list[CanonicalCompatibilityReport]:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        return [
            CanonicalCompatibilityReport(
                extension_id=extension_id,
                source_format=row["source_format"],
                canonical_version=row["canonical_version"],
                compatibility_status=row["compatibility_status"],
                compatibility_notes=row.get("compatibility_notes", []),
                missing_items=row.get("missing_items", []),
                warnings=row.get("warnings", []),
                compatibility_tier=row.get("compatibility_tier", "manifest_compatible"),
                smoke_check=row.get("smoke_check", {}),
                package_compatibility=row.get("package_compatibility", {}),
                blocked_reasons=row.get("blocked_reasons", []),
            )
            for row in await self._repo.list_extension_compatibility_reports(extension_id)
        ]

    async def diagnostics(
        self,
        extension_id: str,
        *,
        trace_id: str | None = None,
    ) -> ExtensionDiagnosticResponse:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        diagnostic = await self._build_diagnostics(extension_id, package=package, trace_id=trace_id)
        return ExtensionDiagnosticResponse(
            extension_id=extension_id,
            bundle_id=package["bundle_id"],
            status=diagnostic["status"],
            summary=diagnostic["summary"],
            compatibility=diagnostic["compatibility"],
            binding=diagnostic["binding"],
            mcp=diagnostic["mcp"],
            config=diagnostic["config"],
            secrets=diagnostic["secrets"],
            env=diagnostic["env"],
            contributions=diagnostic["contributions"],
            health=diagnostic["health"],
            next_actions=diagnostic["next_actions"],
            runtime_snapshot=diagnostic["runtime_snapshot"],
        )

    async def enable(
        self,
        extension_id: str,
        *,
        actor_member_id: str,
        trace_id: str | None = None,
    ) -> PluginBundle:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        bundle = await self._skill_plugin.enable_bundle(
            package["bundle_id"],
            actor_member_id=actor_member_id,
            trace_id=trace_id,
        )
        await self._activate_extension_runtime(extension_id, package=package, trace_id=trace_id)
        return await self._skill_plugin.get_bundle(bundle.bundle_id)

    async def disable(
        self,
        extension_id: str,
        *,
        actor_member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> PluginBundle:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        bundle = await self._skill_plugin.disable_bundle(
            package["bundle_id"],
            actor_member_id=actor_member_id,
            reason=reason,
            trace_id=trace_id,
        )
        await self._deactivate_extension_runtime(extension_id, package=package, trace_id=trace_id)
        return bundle

    async def bind(
        self,
        extension_id: str,
        *,
        trace_id: str | None = None,
    ) -> ExtensionBindingResponse:
        package = await self._repo.get_extension_package(extension_id)
        if package is None:
            raise AppError(ErrorCode.PLUGIN_NOT_FOUND, "Extension not found", status_code=404)
        bound_package = await self._skill_plugin._refresh_extension_binding(  # noqa: SLF001
            package["bundle_id"],
            trace_id=trace_id,
        )
        bundle = await self._skill_plugin.get_bundle(package["bundle_id"])
        skills = [
            await self._skill_plugin.get_skill(skill.skill_id) for skill in bound_package.skills
        ]
        return ExtensionBindingResponse(
            bundle=bundle,
            skills=skills,
            snapshots=await self._binding_snapshots(extension_id),
        )

    async def plan_run(
        self,
        extension_id: str,
        request: ExtensionPlanRunRequest,
        *,
        trace_id: str | None = None,
    ) -> ExtensionPlanRunResponse:
        bundle = await self.get_extension(extension_id)
        match_request = SkillMatchRequest(
            owner_member_id=request.owner_member_id,
            goal=request.goal,
            intent=request.intent,
        )
        matches = await self._skill_plugin.match_skills(match_request, trace_id=trace_id)
        filtered = [match for match in matches if match.bundle_id == bundle.bundle_id]
        diagnostic = await self._build_diagnostics(
            extension_id,
            package=await self._repo.get_extension_package(extension_id) or {},
            trace_id=trace_id,
        )
        runnable = False
        for match in filtered[:3]:
            skill = await self._skill_plugin.get_skill(match.skill_id)
            if skill.binding_status in {"ready", "not_required"} and bool(skill.steps):
                runnable = True
                break
        runnable_state = _runnable_state(bundle, diagnostic, runnable)
        return ExtensionPlanRunResponse(
            extension_id=extension_id,
            bundle=bundle,
            matches=[match.model_dump(mode="json") for match in filtered],
            runnable=runnable,
            runnable_state=runnable_state,
            blocked_by=diagnostic["summary"].get("blocked_by", []),
            missing_bindings=diagnostic["binding"].get("missing_bindings", []),
            required_approvals=diagnostic["summary"].get("required_approvals", []),
            selected_capabilities=diagnostic["contributions"],
            next_actions=diagnostic["next_actions"],
            runtime_snapshot=diagnostic["runtime_snapshot"],
        )

    async def launch_task(
        self,
        extension_id: str,
        request: ExtensionTaskLaunchRequest,
        *,
        trace_id: str | None = None,
    ) -> Any:
        if self._task_engine is None:
            raise AppError(
                ErrorCode.TASK_PLAN_FAILED,
                "Task Engine 未初始化",
                status_code=500,
            )
        plan = await self.plan_run(
            extension_id,
            ExtensionPlanRunRequest(
                owner_member_id=request.owner_member_id,
                goal=request.goal,
                intent=request.intent,
            ),
            trace_id=trace_id,
        )
        if not plan.runnable and plan.runnable_state == "needs_binding":
            await self.bind(extension_id, trace_id=trace_id)
            plan = await self.plan_run(
                extension_id,
                ExtensionPlanRunRequest(
                    owner_member_id=request.owner_member_id,
                    goal=request.goal,
                    intent=request.intent,
                ),
                trace_id=trace_id,
            )
        selected_match = next(
            (
                match
                for match in plan.matches
                if str(match.get("skill_id") or "").strip()
            ),
            None,
        )
        if selected_match is None:
            binding = await self.bind(extension_id, trace_id=trace_id)
            fallback_skill = binding.skills[0] if binding.skills else None
            if fallback_skill is not None:
                if fallback_skill.status != "enabled":
                    fallback_skill = await self._skill_plugin.enable_skill(
                        fallback_skill.skill_id,
                        actor_member_id=request.owner_member_id,
                        trace_id=trace_id,
                    )
                selected_match = {
                    "skill_id": fallback_skill.skill_id,
                    "bundle_id": fallback_skill.bundle_id,
                    "confidence": 1.0,
                    "reason": "extension_launch_bundle_default",
                }
        if selected_match is None:
            raise AppError(
                ErrorCode.TASK_PLAN_FAILED,
                "扩展没有匹配到可执行 Skill",
                status_code=409,
                details={
                    "extension_id": extension_id,
                    "runnable": plan.runnable,
                    "runnable_state": plan.runnable_state,
                    "blocked_by": plan.blocked_by,
                    "missing_bindings": plan.missing_bindings,
                },
            )
        if not plan.runnable and (plan.blocked_by or plan.missing_bindings):
            raise AppError(
                ErrorCode.TASK_PLAN_FAILED,
                "扩展尚未进入可执行状态",
                status_code=409,
                details={
                    "extension_id": extension_id,
                    "runnable_state": plan.runnable_state,
                    "blocked_by": plan.blocked_by,
                    "missing_bindings": plan.missing_bindings,
                    "required_approvals": plan.required_approvals,
                },
            )
        runtime_snapshot = dict(plan.runtime_snapshot or {})
        selected_skill_id = str(selected_match.get("skill_id") or "")
        planner_context = {
            "intent": {
                "name": request.intent or "extension_plan_run",
                "source": "extension_task_launch",
            },
            "route_intent": "extension_plan_run",
            "extension_task": {
                "extension_id": extension_id,
                "bundle_id": plan.bundle.bundle_id,
                "selected_skill_id": selected_skill_id,
                "selected_match": dict(selected_match),
                "runtime_snapshot": runtime_snapshot,
                "selected_capabilities": list(plan.selected_capabilities or []),
                "runnable_state": plan.runnable_state,
            },
        }
        constraints = {
            **dict(request.constraints or {}),
            "skill_id": selected_skill_id,
            "skill_input": dict(request.skill_input or {}) or {"goal": request.goal},
        }
        task_request = TaskCreateRequest(
            conversation_id=request.conversation_id,
            owner_member_id=request.owner_member_id,
            goal=request.goal,
            domain="extension_ecosystem",
            constraints=constraints,
            planner_context=planner_context,
            success_criteria=request.success_criteria or ["扩展任务形成可回放运行时交付证据"],
            client_request_id=request.client_request_id,
            auto_start=request.auto_start,
        )
        return await self._task_engine.create_task(task_request, trace_id=trace_id)

    async def _bind_package(self, package: CanonicalExtensionPackage) -> CanonicalExtensionPackage:
        builtin_tool_names = {
            str(tool["tool_name"]) for tool in await self._task_repo.list_tools()
        }
        return bind_canonical_package(
            package,
            builtin_tool_names=builtin_tool_names,
            active_mcp_tools=await self._repo.list_active_mcp_tools(),
        )

    async def _binding_snapshots(self, extension_id: str) -> list[Any]:
        from app.schemas.extensions import ExtensionBindingSnapshot

        return [
            ExtensionBindingSnapshot(
                snapshot_id=row["snapshot_id"],
                extension_id=row["extension_id"],
                bundle_id=row.get("bundle_id"),
                skill_id=row.get("skill_id"),
                binding_status=row["binding_status"],
                binding_summary=row.get("binding_summary", {}),
                details=row.get("details", {}),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in await self._repo.list_extension_binding_snapshots(extension_id)
        ]

    async def _build_diagnostics(
        self,
        extension_id: str,
        *,
        package: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        canonical = package.get("canonical_snapshot") or {}
        bundle_row = (
            await self._repo.get_bundle(str(package.get("bundle_id") or ""))
            if package.get("bundle_id")
            else None
        )
        binding_snapshots = await self._repo.list_extension_binding_snapshots(extension_id)
        reports = await self._repo.list_extension_compatibility_reports(extension_id)
        contributions = await self._repo.list_extension_runtime_contributions(extension_id)
        mcp_servers = [
            server
            for server in await self._repo.list_mcp_servers()
            if extension_id in str(server.get("allowed_skills", ""))
            or server.get("server_id")
            in {
                item.get("name")
                for item in contributions
                if item.get("contribution_type") == "mcp"
            }
        ]
        binding_summary = package.get("canonical_snapshot", {}).get("binding_summary") or package.get(
            "binding_summary",
            {},
        )
        missing_bindings = sorted(
            {
                item
                for snapshot in binding_snapshots
                for item in snapshot.get("details", {}).get("missing_requirements", [])
            }
            | set(binding_summary.get("missing_bindings", []))
        )
        runtime_context = await self._runtime_context(
            extension_id,
            package=package,
            trace_id=trace_id,
        )
        driver_health = self._runtime_drivers.health_all(runtime_context)
        next_actions = _diagnostic_next_actions(
            canonical=canonical,
            missing_bindings=missing_bindings,
            contributions=contributions,
            driver_health=driver_health,
        )
        status = _diagnostic_status(
            compatibility_status=package.get("compatibility_status", "compatible"),
            missing_bindings=missing_bindings,
            contributions=contributions,
        )
        diagnostic = {
            "diagnostic_id": f"extdiag.{extension_id}",
            "extension_id": extension_id,
            "organization_id": package.get("organization_id", "org_default"),
            "bundle_id": package.get("bundle_id"),
            "status": status,
            "summary": {
                "runtime_compatibility": canonical.get("runtime_compatibility", "manifest_compatible"),
                "blocked_by": reports[0].get("blocked_reasons", []) if reports else [],
                "required_approvals": [],
                "runtime_drivers": [item["driver_id"] for item in driver_health],
            },
            "compatibility": reports[0] if reports else {},
            "binding": {
                "snapshots": binding_snapshots,
                "missing_bindings": missing_bindings,
                "status": package.get("binding_status", "not_required"),
            },
            "mcp": {"servers": mcp_servers, "active_tools": await self._repo.list_active_mcp_tools()},
            "config": {"requirements": canonical.get("config_requirements", [])},
            "secrets": {"requirements": canonical.get("secret_requirements", [])},
            "env": {"requirements": canonical.get("env_requirements", [])},
            "contributions": contributions,
            "health": _runtime_health_summary(contributions, driver_health=driver_health),
            "next_actions": next_actions,
            "runtime_snapshot": _extension_runtime_snapshot(
                extension_id=extension_id,
                package={**package, **(bundle_row or {})},
                binding_snapshots=binding_snapshots,
                contributions=contributions,
                driver_health=driver_health,
                diagnostic_status=status,
            ),
            "trace_id": trace_id,
        }
        from app.core.time import utc_now_iso

        now = utc_now_iso()
        await self._repo.upsert_extension_diagnostic(
            {**diagnostic, "created_at": now, "updated_at": now}
        )
        return diagnostic

    async def _runtime_context(
        self,
        extension_id: str,
        *,
        package: dict[str, Any],
        trace_id: str | None = None,
    ) -> ExtensionRuntimeActivationContext:
        canonical = package.get("canonical_snapshot") or {}
        sources = await self._repo.list_extension_sources(extension_id)
        return ExtensionRuntimeActivationContext(
            extension_id=extension_id,
            organization_id=package.get("organization_id", "org_default"),
            bundle_id=package.get("bundle_id"),
            canonical=canonical,
            package=package,
            source_root=_extension_source_root(sources),
            trace_id=trace_id,
        )

    async def _activate_extension_runtime(
        self,
        extension_id: str,
        *,
        package: dict[str, Any],
        trace_id: str | None = None,
    ) -> None:
        context = await self._runtime_context(extension_id, package=package, trace_id=trace_id)
        for result in self._runtime_drivers.activate_all(context):
            await self._persist_runtime_activation_result(context, result)
        await self._build_diagnostics(extension_id, package=package, trace_id=trace_id)

    async def _deactivate_extension_runtime(
        self,
        extension_id: str,
        *,
        package: dict[str, Any],
        trace_id: str | None = None,
    ) -> None:
        context = await self._runtime_context(extension_id, package=package, trace_id=trace_id)
        for result in self._runtime_drivers.deactivate_all(context):
            await self._persist_runtime_activation_result(context, result)
        await self._deactivate_extension_mcp(package.get("bundle_id"), trace_id=trace_id)
        await self._deactivate_extension_tools(extension_id)
        await self._build_diagnostics(extension_id, package=package, trace_id=trace_id)

    async def _persist_runtime_activation_result(
        self,
        context: ExtensionRuntimeActivationContext,
        result: ExtensionRuntimeActivationResult,
    ) -> None:
        from app.core.time import utc_now_iso

        now = utc_now_iso()
        for draft in result.contributions:
            contribution_id = draft.contribution_id or _runtime_contribution_id(context, draft)
            await self._repo.upsert_extension_runtime_contribution(
                {
                    "contribution_id": contribution_id,
                    "extension_id": context.extension_id,
                    "organization_id": context.organization_id,
                    "bundle_id": context.bundle_id,
                    "contribution_type": draft.contribution_type,
                    "runtime_kind": draft.runtime_kind,
                    "name": draft.name,
                    "status": draft.status,
                    "details": draft.details,
                    "evidence": {
                        "driver_id": result.driver_id,
                        "activation_status": result.status,
                        "health": result.health,
                        "errors": result.errors,
                        **draft.evidence,
                    },
                    "trace_id": context.trace_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            if draft.contribution_type == "tool" and draft.status == "ready":
                await self._task_repo.upsert_tool(
                    {
                        "tool_name": str(draft.details.get("tool_name") or draft.name),
                        "display_name": str(draft.details.get("display_name") or draft.name),
                        "description": draft.details.get("description"),
                        "source": "extension_python",
                        "input_schema": draft.details.get("input_schema", {}),
                        "output_schema": draft.details.get("output_schema", {}),
                        "risk_policy": draft.details.get("risk_policy", {"risk_level": "R2"}),
                        "required_handle_types": [],
                        "status": "active",
                        "bundle_id": context.bundle_id,
                        "adapter_config": {
                            "extension_id": context.extension_id,
                            "runtime_kind": result.driver_id,
                            "execution": "not_direct",
                        },
                        "trust_level": "restricted",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            elif draft.contribution_type == "tool":
                tool_name = str(draft.details.get("tool_name") or draft.name)
                existing = await self._task_repo.get_tool(tool_name)
                if existing is not None and existing.get("bundle_id") == context.bundle_id:
                    await self._task_repo.upsert_tool(
                        {
                            **existing,
                            "status": "disabled" if draft.status == "disabled" else "degraded",
                            "updated_at": now,
                            "created_at": existing.get("created_at") or now,
                        }
                    )

    async def _deactivate_extension_tools(self, extension_id: str) -> None:
        from app.core.time import utc_now_iso

        now = utc_now_iso()
        for tool in await self._task_repo.list_tools():
            adapter_config = dict(tool.get("adapter_config") or {})
            if tool.get("source") == "extension_python" and adapter_config.get("extension_id") == extension_id:
                await self._task_repo.upsert_tool(
                    {
                        **tool,
                        "status": "disabled",
                        "updated_at": now,
                        "created_at": tool.get("created_at") or now,
                    }
                )

    async def _deactivate_extension_mcp(
        self,
        bundle_id: str | None,
        *,
        trace_id: str | None = None,
    ) -> None:
        if self._skill_plugin._mcp_service is None or not bundle_id:  # noqa: SLF001
            return
        extension_row = await self._repo.get_extension_package_by_bundle(bundle_id)
        if extension_row is None:
            return
        for server in extension_row.get("manifest", {}).get("_cycber_mcp_servers", []):
            server_id = str(server.get("server_id") or "")
            if server_id:
                await self._skill_plugin._mcp_service.disable_server(server_id, trace_id=trace_id)  # noqa: SLF001


def _bundle_request(request: ExtensionImportRequest) -> BundleInstallRequest:
    return BundleInstallRequest(**request.model_dump(mode="json"))


def _bundle_binding_status(package: CanonicalExtensionPackage) -> str:
    statuses = [skill.execution_binding.status for skill in package.skills]
    if not statuses:
        return "not_required"
    if all(status == "ready" for status in statuses):
        return "ready"
    if any(status == "ready" for status in statuses):
        return "degraded"
    if all(status == "not_required" for status in statuses):
        return "not_required"
    return "degraded"


def _bundle_binding_summary(package: CanonicalExtensionPackage) -> dict[str, Any]:
    return {
        "skill_count": len(package.skills),
        "ready_skills": sum(1 for skill in package.skills if skill.execution_binding.status == "ready"),
        "degraded_skills": sum(
            1 for skill in package.skills if skill.execution_binding.status == "degraded"
        ),
        "runtime_compatibility": package.runtime_compatibility,
        "runtime_contribution_count": len(package.runtime_contributions),
    }


def _diagnostic_status(
    *,
    compatibility_status: str,
    missing_bindings: list[str],
    contributions: list[dict[str, Any]],
) -> str:
    if compatibility_status == "blocked":
        return "blocked"
    if missing_bindings:
        return "needs_binding"
    if any(item.get("runtime_kind") == "external_runtime" for item in contributions):
        if not any(item.get("status") == "ready" for item in contributions):
            return "external_runtime_required"
    return "ready"


def _diagnostic_next_actions(
    *,
    canonical: dict[str, Any],
    missing_bindings: list[str],
    contributions: list[dict[str, Any]],
    driver_health: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in missing_bindings:
        if item.startswith("mcp:"):
            actions.append({"kind": "enable_mcp", "target": item.removeprefix("mcp:")})
        elif item.startswith("builtin:"):
            actions.append({"kind": "enable_tool", "target": item.removeprefix("builtin:")})
    for requirement in canonical.get("env_requirements", []):
        actions.append({"kind": "set_env", "target": requirement.get("name")})
    for requirement in canonical.get("secret_requirements", []):
        actions.append({"kind": "set_secret", "target": requirement.get("key") or requirement.get("name")})
    external_runtime_required = any(
        contribution.get("runtime_kind") == "external_runtime"
        and contribution.get("status") != "ready"
        for contribution in contributions
    ) or any(
        item.get("status") == "external_runtime_required" for item in (driver_health or [])
    )
    for contribution in contributions:
        if contribution.get("runtime_kind") == "external_runtime" and external_runtime_required:
            actions.append({"kind": "start_external_runtime", "target": contribution.get("name")})
    return actions


def _runtime_health_summary(
    contributions: list[dict[str, Any]],
    *,
    driver_health: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    health_checks = [
        item
        for item in contributions
        if item.get("contribution_type") == "health_check"
    ]
    external = [
        item
        for item in contributions
        if item.get("runtime_kind") == "external_runtime"
    ]
    if any(item.get("status") in {"blocked", "runtime_error"} for item in health_checks):
        return {"status": "error", "checks": health_checks, "drivers": driver_health or []}
    if external and not any(item.get("status") == "ready" for item in external):
        return {
            "status": "external_runtime_required",
            "checks": health_checks,
            "external_runtime": external,
            "drivers": driver_health or [],
        }
    if health_checks:
        return {"status": "ready", "checks": health_checks, "drivers": driver_health or []}
    if driver_health:
        if any(item.get("status") == "external_runtime_required" for item in driver_health):
            return {"status": "external_runtime_required", "drivers": driver_health}
        return {"status": "unknown" if contributions else "not_required", "drivers": driver_health}
    return {"status": "unknown" if contributions else "not_required"}


def _runnable_state(
    bundle: PluginBundle,
    diagnostic: dict[str, Any],
    runnable: bool,
) -> str:
    if diagnostic["status"] == "blocked" or bundle.compatibility_status == "blocked":
        return "blocked"
    if diagnostic["status"] == "external_runtime_required":
        return "external_runtime_required"
    if diagnostic["binding"].get("missing_bindings"):
        return "needs_binding"
    if runnable:
        return "ready"
    if bundle.binding_status == "degraded":
        return "degraded"
    return "needs_binding"


def _extension_runtime_snapshot(
    *,
    extension_id: str,
    package: dict[str, Any],
    binding_snapshots: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    driver_health: list[dict[str, Any]],
    diagnostic_status: str,
) -> dict[str, Any]:
    bundle_status = str(package.get("status") or "installed_disabled")
    binding_status = str(package.get("binding_status") or "not_required")
    contribution_status_counts: dict[str, int] = {}
    contribution_type_counts: dict[str, int] = {}
    for item in contributions:
        item_status = str(item.get("status") or "unknown")
        contribution_status_counts[item_status] = contribution_status_counts.get(item_status, 0) + 1
        item_type = str(item.get("contribution_type") or "unknown")
        contribution_type_counts[item_type] = contribution_type_counts.get(item_type, 0) + 1
    missing_bindings = sorted(
        {
            str(req)
            for snapshot in binding_snapshots
            for req in list(dict(snapshot.get("details") or {}).get("missing_requirements") or [])
            if str(req).strip()
        }
    )
    if bundle_status == "disabled":
        lifecycle_status = "disabled"
    elif diagnostic_status == "ready":
        lifecycle_status = "ready"
    elif bundle_status == "enabled" and not missing_bindings:
        lifecycle_status = "bound"
    elif bundle_status == "enabled":
        lifecycle_status = "enabled"
    else:
        lifecycle_status = "installed"
    runtime_sync_state = "pending"
    if bundle_status == "disabled":
        runtime_sync_state = "synced"
    elif bundle_status == "enabled" and contributions:
        runtime_sync_state = "synced"
    present_proof = ["diagnostic_snapshot"]
    if binding_snapshots:
        present_proof.append("binding_snapshot")
    if contributions:
        present_proof.append("runtime_contribution")
    return {
        "contract_version": "phase112.extension_runtime_snapshot.v1",
        "extension_id": extension_id,
        "bundle_status": bundle_status,
        "binding_status": binding_status,
        "diagnostic_status": diagnostic_status,
        "lifecycle_status": lifecycle_status,
        "runtime_sync_state": runtime_sync_state,
        "missing_bindings": missing_bindings,
        "contribution_status_counts": contribution_status_counts,
        "contribution_type_counts": contribution_type_counts,
        "driver_health_statuses": {
            str(item.get("driver_id") or "unknown"): str(item.get("status") or "unknown")
            for item in driver_health
        },
        "deliverable_proof": {
            "required": ["binding_snapshot", "runtime_contribution", "diagnostic_snapshot"],
            "present": present_proof,
            "final_deliverable": runtime_sync_state == "synced" and bool(contributions),
        },
    }


def _extension_source_root(sources: list[dict[str, Any]]) -> Any:
    from pathlib import Path

    for source in sources:
        descriptor = source.get("source_descriptor") or {}
        root = descriptor.get("root")
        if isinstance(root, str) and root.strip():
            return Path(root)
    return None


def _runtime_contribution_id(
    context: ExtensionRuntimeActivationContext,
    draft: ExtensionRuntimeContributionDraft,
) -> str:
    return (
        f"extcontrib.{context.bundle_id}."
        f"{draft.contribution_type}.{_runtime_safe_id(draft.name)}"
    )


def _runtime_safe_id(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "contribution"
