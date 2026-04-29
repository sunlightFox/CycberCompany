from __future__ import annotations

import json
from typing import Any

from core_types import (
    ErrorCode,
    MemberStatus,
    RiskLevel,
    ShellSwitchPreview,
    ShellTemplateApplication,
    TraceSpanStatus,
    TraceSpanType,
)
from shell_runtime import ShellRuntime, ShellRuntimeError
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.organization_repo import OrganizationRepository
from app.db.repositories.task_repo import TaskRepository
from app.db.session import Database
from app.services.audit import AuditEventService
from app.services.bootstrap import DEFAULT_BRAIN_ID, DEFAULT_ORGANIZATION_ID

BLOCKED_BUSINESS_MUTATIONS = [
    "members.display_name",
    "departments.display_name",
    "roles.display_name",
    "tasks.title",
    "tasks.goal",
    "memory_items.payload",
    "assets.display_name",
    "assets.asset_type",
]


class ShellSwitchService:
    def __init__(
        self,
        *,
        db: Database,
        shell_runtime: ShellRuntime,
        organization_repo: OrganizationRepository,
        task_repo: TaskRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._db = db
        self._shell_runtime = shell_runtime
        self._organization = organization_repo
        self._repo = task_repo
        self._trace = trace_service
        self._audit = audit_service

    def get_shell_detail(self, shell_id: str) -> dict[str, Any]:
        shell = self._load_shell(shell_id)
        return {
            **shell.model_dump(mode="json"),
            "templates": self.templates(shell_id),
        }

    def templates(self, shell_id: str) -> dict[str, Any]:
        self._load_shell(shell_id)
        return {
            "departments": self._shell_runtime.read_shell_file(shell_id, "departments.yaml").get(
                "departments",
                [],
            ),
            "roles": self._shell_runtime.read_shell_file(shell_id, "roles.yaml").get("roles", []),
            "members": self._shell_runtime.read_shell_file(shell_id, "member_templates.yaml").get(
                "members",
                [],
            ),
        }

    async def preview(
        self,
        shell_id: str,
        *,
        trace_id: str | None = None,
    ) -> ShellSwitchPreview:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SHELL_SWITCH_PREVIEW,
            "preview shell switch",
            input_data={"to_shell_id": shell_id},
        )
        try:
            organization = await self._organization.get_current()
            if organization is None:
                raise AppError(ErrorCode.NOT_FOUND, "默认组织不存在", status_code=404)
            from_shell_id = organization["shell_id"]
            from_shell = self._load_shell(from_shell_id)
            to_shell = self._load_shell(shell_id)
            changed_labels = [
                {"key": key, "from": from_shell.terms.get(key), "to": to_shell.terms.get(key)}
                for key in sorted(set(from_shell.terms) | set(to_shell.terms))
                if from_shell.terms.get(key) != to_shell.terms.get(key)
            ]
            preview = ShellSwitchPreview(
                from_shell_id=from_shell_id,
                to_shell_id=shell_id,
                changed_labels=changed_labels,
                fixed_rules={
                    "settings_fixed": True,
                    "asset_second_level_fixed": True,
                    "system_menu_label": to_shell.constraints.system_menu_label,
                    "asset_categories": [
                        item.model_dump(mode="json")
                        for item in to_shell.constraints.asset_categories
                    ],
                },
                blocked_mutations=BLOCKED_BUSINESS_MUTATIONS,
                business_values_unchanged=True,
                trace_id=trace_id,
            )
            await self._repo.insert_shell_switch_event(
                {
                    "event_id": new_id("shsw"),
                    "organization_id": organization["organization_id"],
                    "from_shell_id": preview.from_shell_id,
                    "to_shell_id": preview.to_shell_id,
                    "event_type": "shell.switch_previewed",
                    "preview": preview.model_dump(mode="json"),
                    "blocked_mutations": preview.blocked_mutations,
                    "business_values_unchanged": True,
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )
            await self._audit.write_event(
                actor_type="system",
                action="shell.switch_previewed",
                object_type="shell",
                object_id=shell_id,
                summary="壳切换预览已生成",
                risk_level=RiskLevel.R0,
                payload=preview.model_dump(mode="json"),
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data=preview.model_dump(mode="json"))
            return preview
        except ShellRuntimeError as exc:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise AppError(ErrorCode.SHELL_CONFIG_INVALID, str(exc), status_code=500) from exc

    async def switch(
        self,
        shell_id: str,
        *,
        actor_member_id: str | None = None,
        trace_id: str | None = None,
    ) -> ShellSwitchPreview:
        preview = await self.preview(shell_id, trace_id=trace_id)
        if not preview.business_values_unchanged:
            await self._audit.write_event(
                actor_type="member" if actor_member_id else "system",
                actor_id=actor_member_id,
                action="shell.switch_blocked",
                object_type="shell",
                object_id=shell_id,
                summary="壳切换被业务值变更守卫阻断",
                risk_level=RiskLevel.R3,
                payload=preview.model_dump(mode="json"),
                trace_id=trace_id,
            )
            raise AppError(
                ErrorCode.SHELL_BUSINESS_VALUE_MUTATION_BLOCKED,
                "壳切换会修改业务值，已阻断",
                status_code=409,
            )
        organization = await self._organization.get_current()
        if organization is None:
            raise AppError(ErrorCode.NOT_FOUND, "默认组织不存在", status_code=404)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SHELL_SWITCH,
            "switch shell",
            input_data=preview.model_dump(mode="json"),
        )
        before_snapshot = await self._business_snapshot()
        async with self._db.transaction():
            await self._organization.update_shell(
                organization["organization_id"],
                shell_id=shell_id,
                updated_at=utc_now_iso(),
            )
            after_snapshot = await self._business_snapshot()
            if before_snapshot != after_snapshot:
                raise AppError(
                    ErrorCode.SHELL_BUSINESS_VALUE_MUTATION_BLOCKED,
                    "壳切换修改了业务字段，已回滚",
                    status_code=409,
                )
            await self._repo.insert_shell_switch_event(
                {
                    "event_id": new_id("shsw"),
                    "organization_id": organization["organization_id"],
                    "from_shell_id": preview.from_shell_id,
                    "to_shell_id": preview.to_shell_id,
                    "event_type": "shell.switched",
                    "preview": preview.model_dump(mode="json"),
                    "blocked_mutations": preview.blocked_mutations,
                    "business_values_unchanged": True,
                    "actor_member_id": actor_member_id,
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )
            await self._audit.write_event(
                actor_type="member" if actor_member_id else "system",
                actor_id=actor_member_id,
                action="shell.switched",
                object_type="organization",
                object_id=organization["organization_id"],
                summary="组织当前壳已切换",
                risk_level=RiskLevel.R1,
                payload=preview.model_dump(mode="json"),
                trace_id=trace_id,
            )
        await self._end_span(span_id, output_data={"to_shell_id": shell_id})
        return preview

    async def apply_template(
        self,
        shell_id: str,
        template_key: str,
        *,
        actor_member_id: str | None = None,
        trace_id: str | None = None,
    ) -> ShellTemplateApplication:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SHELL_TEMPLATE_APPLY,
            "apply shell template",
            input_data={"shell_id": shell_id, "template_key": template_key},
        )
        try:
            template_type, key, template = self._resolve_template(shell_id, template_key)
            object_type, object_id, result = await self._apply_template_value(
                shell_id,
                template_type,
                key,
                template,
            )
            data = {
                "application_id": new_id("shtpl"),
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "shell_id": shell_id,
                "template_type": template_type,
                "template_key": key,
                "object_type": object_type,
                "object_id": object_id,
                "status": result["status"],
                "result": result,
                "actor_member_id": actor_member_id,
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
            await self._repo.insert_shell_template_application(data)
            await self._audit.write_event(
                actor_type="member" if actor_member_id else "system",
                actor_id=actor_member_id,
                action="shell.template_applied",
                object_type=object_type or "shell_template",
                object_id=object_id,
                summary="壳模板已应用",
                risk_level=RiskLevel.R1,
                payload=data,
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data=redact(data))
            return ShellTemplateApplication(**data)
        except ShellRuntimeError as exc:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise AppError(ErrorCode.SHELL_TEMPLATE_INVALID, str(exc), status_code=422) from exc

    def _resolve_template(
        self,
        shell_id: str,
        template_key: str,
    ) -> tuple[str, str, dict[str, Any]]:
        requested_type: str | None = None
        requested_key = template_key
        if ":" in template_key:
            requested_type, requested_key = template_key.split(":", 1)
        templates = self.templates(shell_id)
        search_order = [
            ("member_template", "members"),
            ("department_template", "departments"),
            ("role_template", "roles"),
        ]
        for template_type, collection_key in search_order:
            if requested_type and requested_type != template_type:
                continue
            for item in templates[collection_key]:
                if item.get("key") == requested_key:
                    return template_type, requested_key, dict(item)
        raise AppError(ErrorCode.SHELL_TEMPLATE_INVALID, "壳模板不存在", status_code=404)

    async def _apply_template_value(
        self,
        shell_id: str,
        template_type: str,
        key: str,
        template: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        if template_type == "department_template":
            object_id = f"dept_{key}"
            now = utc_now_iso()
            await self._db.execute(
                """
                INSERT INTO departments (
                  department_id, organization_id, parent_department_id, key, display_name,
                  description, sort_order, metadata_json, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(department_id) DO NOTHING
                """,
                (
                    object_id,
                    DEFAULT_ORGANIZATION_ID,
                    key,
                    template["display_name"],
                    template.get("description"),
                    int(template.get("sort_order", 0)),
                    json.dumps(
                        {"created_from_shell_id": shell_id, "created_from_template_id": key},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            await self._ensure_skill_policy(
                "department",
                object_id,
                template.get("default_skills", []),
            )
            return "department", object_id, {"status": "applied_or_already_exists"}
        if template_type == "role_template":
            object_id = f"role_{key}"
            now = utc_now_iso()
            await self._db.execute(
                """
                INSERT INTO roles (
                  role_id, organization_id, key, display_name, description,
                  default_department_id, default_skills_json, authority_level,
                  metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(role_id) DO NOTHING
                """,
                (
                    object_id,
                    DEFAULT_ORGANIZATION_ID,
                    key,
                    template["display_name"],
                    template.get("description"),
                    f"dept_{template.get('default_department_key')}",
                    json.dumps(template.get("default_skills", []), ensure_ascii=False),
                    int(template.get("authority_level", 0)),
                    json.dumps(
                        {"created_from_shell_id": shell_id, "created_from_template_id": key},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            await self._ensure_skill_policy(
                "role",
                object_id,
                template.get("default_skills", []),
            )
            return "role", object_id, {"status": "applied_or_already_exists"}
        if template_type == "member_template":
            object_id = f"mem_{key}"
            now = utc_now_iso()
            await self._db.execute(
                """
                INSERT INTO members (
                  member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                  status, default_brain_id, persona_profile_id, heart_profile_json,
                  memory_policy_json, created_from_shell_id, created_from_template_id,
                  metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(member_id) DO NOTHING
                """,
                (
                    object_id,
                    DEFAULT_ORGANIZATION_ID,
                    f"dept_{template['department']}",
                    f"role_{template['role']}",
                    template["name"],
                    MemberStatus.NEEDS_CONFIGURATION.value,
                    DEFAULT_BRAIN_ID,
                    template["persona"],
                    json.dumps({"tone": "professional_warm"}, ensure_ascii=False),
                    json.dumps({"write_requires_source": True}, ensure_ascii=False),
                    shell_id,
                    key,
                    json.dumps(
                        {"default_skills": template.get("default_skills", [])},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            await self._ensure_member_runtime_defaults(
                object_id,
                template.get("default_skills", []),
            )
            return "member", object_id, {"status": "applied_or_already_exists"}
        raise AppError(ErrorCode.SHELL_TEMPLATE_INVALID, "不支持的模板类型", status_code=422)

    def _load_shell(self, shell_id: str):
        try:
            return self._shell_runtime.load(shell_id)
        except ShellRuntimeError as exc:
            raise AppError(ErrorCode.SHELL_CONFIG_INVALID, str(exc), status_code=500) from exc

    async def _business_snapshot(self) -> dict[str, Any]:
        members = await self._db.fetch_all(
            "SELECT member_id, display_name, department_id, role_id FROM members ORDER BY member_id"
        )
        departments = await self._db.fetch_all(
            "SELECT department_id, display_name FROM departments ORDER BY department_id"
        )
        roles = await self._db.fetch_all(
            "SELECT role_id, display_name FROM roles ORDER BY role_id"
        )
        tasks = await self._db.fetch_all(
            "SELECT task_id, title, goal FROM tasks ORDER BY task_id"
        )
        return {
            "members": [dict(row) for row in members],
            "departments": [dict(row) for row in departments],
            "roles": [dict(row) for row in roles],
            "tasks": [dict(row) for row in tasks],
        }

    async def _ensure_member_runtime_defaults(
        self,
        member_id: str,
        allowed_skills: list[str],
    ) -> None:
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO member_availability (
              member_id, organization_id, status, capacity, current_load,
              unavailable_reason, schedule_json, source, updated_at
            ) VALUES (?, ?, 'available', 1, 0, NULL, '{}', 'shell_template', ?)
            ON CONFLICT(member_id) DO NOTHING
            """,
            (member_id, DEFAULT_ORGANIZATION_ID, now),
        )
        await self._ensure_skill_policy("member", member_id, allowed_skills)

    async def _ensure_skill_policy(
        self,
        subject_type: str,
        subject_id: str,
        allowed_skills: list[str],
    ) -> None:
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO member_skill_policies (
              policy_id, organization_id, subject_type, subject_id, allowed_skills_json,
              denied_skills_json, allowed_mcp_tools_json, denied_mcp_tools_json,
              risk_policy_json, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '{}', 'shell_template', ?, ?)
            ON CONFLICT(organization_id, subject_type, subject_id) DO NOTHING
            """,
            (
                f"msp_{subject_type}_{subject_id}",
                DEFAULT_ORGANIZATION_ID,
                subject_type,
                subject_id,
                json.dumps(allowed_skills, ensure_ascii=False),
                now,
                now,
            ),
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
            input_data=input_data,
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
