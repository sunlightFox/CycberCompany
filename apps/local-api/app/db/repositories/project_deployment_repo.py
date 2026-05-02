from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.db.session import Database

WORKSPACE_UPDATE_COLUMNS = {
    "task_id",
    "source_uri",
    "root_uri",
    "backend_type",
    "status",
    "stack_summary",
    "stack_summary_json",
    "policy_snapshot",
    "policy_snapshot_json",
    "trace_id",
    "updated_at",
}
DEPLOYMENT_UPDATE_COLUMNS = {
    "status",
    "backend_type",
    "plan",
    "plan_json",
    "current_step_key",
    "endpoint",
    "endpoint_json",
    "health",
    "health_json",
    "failure_reason",
    "trace_id",
    "updated_at",
}
TOOLCHAIN_UPDATE_COLUMNS = {
    "source_uri",
    "checksum",
    "status",
    "policy_snapshot",
    "policy_snapshot_json",
    "trace_id",
    "updated_at",
}
HOST_PLAN_UPDATE_COLUMNS = {
    "install_source",
    "install_source_json",
    "command_preview",
    "command_preview_json",
    "impact_summary",
    "impact_summary_json",
    "risk_level",
    "status",
    "approval_id",
    "trace_id",
    "updated_at",
}
PROCESS_UPDATE_COLUMNS = {
    "command_redacted",
    "command_redacted_json",
    "status",
    "port",
    "endpoint_url",
    "log_artifact_id",
    "started_at",
    "stopped_at",
    "trace_id",
    "updated_at",
}
PORT_UPDATE_COLUMNS = {"task_id", "deployment_id", "status", "leased_until", "updated_at"}


class ProjectDeploymentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._db.transaction():
            yield

    async def insert_workspace(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO project_workspaces (
              workspace_id, organization_id, task_id, owner_member_id, source_type,
              source_uri, root_uri, backend_type, status, stack_summary_json,
              policy_snapshot_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["workspace_id"],
                data["organization_id"],
                data.get("task_id"),
                data["owner_member_id"],
                data["source_type"],
                data.get("source_uri"),
                data["root_uri"],
                data["backend_type"],
                data["status"],
                _json(data.get("stack_summary", {})),
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM project_workspaces WHERE workspace_id = ?",
            (workspace_id,),
        )
        return _workspace_from_row(dict(row)) if row else None

    async def list_workspaces(
        self,
        *,
        owner_member_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if owner_member_id:
            where.append("owner_member_id = ?")
            params.append(owner_member_id)
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT * FROM project_workspaces
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_workspace_from_row(dict(row)) for row in rows]

    async def update_workspace(self, workspace_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "project_workspaces",
            "workspace_id",
            workspace_id,
            fields,
            WORKSPACE_UPDATE_COLUMNS,
            {"stack_summary": "stack_summary_json", "policy_snapshot": "policy_snapshot_json"},
        )

    async def insert_deployment(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO project_deployments (
              deployment_id, organization_id, workspace_id, task_id, status,
              backend_type, plan_json, current_step_key, endpoint_json,
              health_json, failure_reason, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["deployment_id"],
                data["organization_id"],
                data["workspace_id"],
                data["task_id"],
                data["status"],
                data["backend_type"],
                _json(data.get("plan", {})),
                data.get("current_step_key"),
                _json(data.get("endpoint", {})),
                _json(data.get("health", {})),
                data.get("failure_reason"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_deployment(self, deployment_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM project_deployments WHERE deployment_id = ?",
            (deployment_id,),
        )
        return _deployment_from_row(dict(row)) if row else None

    async def list_deployments_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM project_deployments WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        return [_deployment_from_row(dict(row)) for row in rows]

    async def update_deployment(self, deployment_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "project_deployments",
            "deployment_id",
            deployment_id,
            fields,
            DEPLOYMENT_UPDATE_COLUMNS,
            {"plan": "plan_json", "endpoint": "endpoint_json", "health": "health_json"},
        )

    async def insert_toolchain(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO toolchain_installs (
              toolchain_id, organization_id, runtime_name, version, install_mode,
              root_uri, source_uri, checksum, status, policy_snapshot_json,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, runtime_name, version, install_mode)
            DO UPDATE SET
              source_uri = excluded.source_uri,
              checksum = excluded.checksum,
              status = excluded.status,
              policy_snapshot_json = excluded.policy_snapshot_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["toolchain_id"],
                data["organization_id"],
                data["runtime_name"],
                data["version"],
                data["install_mode"],
                data["root_uri"],
                data.get("source_uri"),
                data.get("checksum"),
                data["status"],
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_toolchains(self, *, runtime_name: str | None = None) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if runtime_name:
            where.append("runtime_name = ?")
            params.append(runtime_name)
        sql = (
            "SELECT * FROM toolchain_installs "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC"
        )
        rows = await self._db.fetch_all(sql, tuple(params))
        return [_toolchain_from_row(dict(row)) for row in rows]

    async def get_toolchain(
        self,
        *,
        runtime_name: str,
        version: str,
        install_mode: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT * FROM toolchain_installs
            WHERE organization_id = 'org_default'
              AND runtime_name = ?
              AND version = ?
              AND install_mode = ?
            """,
            (runtime_name, version, install_mode),
        )
        return _toolchain_from_row(dict(row)) if row else None

    async def insert_host_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO host_install_plans (
              host_install_plan_id, organization_id, task_id, requested_software,
              install_source_json, command_preview_json, impact_summary_json,
              risk_level, status, approval_id, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["host_install_plan_id"],
                data["organization_id"],
                data["task_id"],
                data["requested_software"],
                _json(data.get("install_source", {})),
                _json(data.get("command_preview", {})),
                _json(data.get("impact_summary", {})),
                data["risk_level"],
                data["status"],
                data.get("approval_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_host_plan(self, host_install_plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM host_install_plans WHERE host_install_plan_id = ?",
            (host_install_plan_id,),
        )
        return _host_plan_from_row(dict(row)) if row else None

    async def get_host_plan_by_approval_id(self, approval_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM host_install_plans
            WHERE approval_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (approval_id,),
        )
        return _host_plan_from_row(dict(row)) if row else None

    async def update_host_plan(self, host_install_plan_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "host_install_plans",
            "host_install_plan_id",
            host_install_plan_id,
            fields,
            HOST_PLAN_UPDATE_COLUMNS,
            {
                "install_source": "install_source_json",
                "command_preview": "command_preview_json",
                "impact_summary": "impact_summary_json",
            },
        )

    async def insert_host_execution(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO host_install_executions (
              host_install_execution_id, organization_id, host_install_plan_id,
              task_id, status, exit_code, log_artifact_id, version_detected,
              install_path_summary, failure_reason, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["host_install_execution_id"],
                data["organization_id"],
                data["host_install_plan_id"],
                data["task_id"],
                data["status"],
                data.get("exit_code"),
                data.get("log_artifact_id"),
                data.get("version_detected"),
                data.get("install_path_summary"),
                data.get("failure_reason"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def insert_managed_process(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO managed_processes (
              managed_process_id, organization_id, deployment_id, task_id,
              workspace_id, process_kind, command_redacted_json, backend_type,
              status, port, endpoint_url, log_artifact_id, started_at, stopped_at,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["managed_process_id"],
                data["organization_id"],
                data.get("deployment_id"),
                data["task_id"],
                data.get("workspace_id"),
                data["process_kind"],
                _json(data.get("command_redacted", {})),
                data["backend_type"],
                data["status"],
                data.get("port"),
                data.get("endpoint_url"),
                data.get("log_artifact_id"),
                data.get("started_at"),
                data.get("stopped_at"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_managed_process_for_deployment(
        self,
        deployment_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT * FROM managed_processes
            WHERE deployment_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (deployment_id,),
        )
        return _process_from_row(dict(row)) if row else None

    async def update_managed_process(
        self,
        managed_process_id: str,
        fields: dict[str, Any],
    ) -> None:
        await self._update(
            "managed_processes",
            "managed_process_id",
            managed_process_id,
            fields,
            PROCESS_UPDATE_COLUMNS,
            {"command_redacted": "command_redacted_json"},
        )

    async def insert_port_lease(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO port_leases (
              port_lease_id, organization_id, task_id, deployment_id, port,
              protocol, status, leased_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["port_lease_id"],
                data["organization_id"],
                data.get("task_id"),
                data.get("deployment_id"),
                data["port"],
                data["protocol"],
                data["status"],
                data.get("leased_until"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_active_port_lease(self, port: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT * FROM port_leases
            WHERE organization_id = 'org_default' AND port = ? AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (port,),
        )
        return _port_from_row(dict(row)) if row else None

    async def get_port_lease_for_deployment(self, deployment_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT * FROM port_leases
            WHERE deployment_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (deployment_id,),
        )
        return _port_from_row(dict(row)) if row else None

    async def update_port_lease(self, port_lease_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "port_leases",
            "port_lease_id",
            port_lease_id,
            fields,
            PORT_UPDATE_COLUMNS,
            {},
        )

    async def _update(
        self,
        table: str,
        key_column: str,
        key_value: str,
        fields: dict[str, Any],
        allowed: set[str],
        json_aliases: dict[str, str],
    ) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in allowed},
            json_aliases,
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
            (*values.values(), key_value),
        )


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if column.endswith("_json") else value
    return values


def _workspace_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["stack_summary"] = _load_json(row.pop("stack_summary_json", None), {})
    row["policy_snapshot"] = _load_json(row.pop("policy_snapshot_json", None), {})
    return row


def _deployment_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["plan"] = _load_json(row.pop("plan_json", None), {})
    row["endpoint"] = _load_json(row.pop("endpoint_json", None), {})
    row["health"] = _load_json(row.pop("health_json", None), {})
    return row


def _toolchain_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["policy_snapshot"] = _load_json(row.pop("policy_snapshot_json", None), {})
    return row


def _host_plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["install_source"] = _load_json(row.pop("install_source_json", None), {})
    row["command_preview"] = _load_json(row.pop("command_preview_json", None), {})
    row["impact_summary"] = _load_json(row.pop("impact_summary_json", None), {})
    return row


def _process_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["command_redacted"] = _load_json(row.pop("command_redacted_json", None), {})
    return row


def _port_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return row
