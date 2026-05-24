from __future__ import annotations

from typing import cast

from core_types import TraceSpanStatus, TraceSpanType, TraceStatus
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from shell_runtime import ShellRuntimeError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from app.api import (
    routes_agent_workbench,
    routes_approvals,
    routes_artifacts,
    routes_assets,
    routes_audit,
    routes_brains,
    routes_browser,
    routes_browser_workflows,
    routes_capabilities,
    routes_channels,
    routes_chat,
    routes_checkpoints,
    routes_execution_boundary,
    routes_extensions,
    routes_external_platform,
    routes_goals,
    routes_health,
    routes_knowledge,
    routes_mcp,
    routes_media,
    routes_members,
    routes_memory,
    routes_model_routing,
    routes_notifications,
    routes_org_policies,
    routes_organization,
    routes_persona,
    routes_plugins,
    routes_project_deployments,
    routes_release,
    routes_response_composer,
    routes_retrieval,
    routes_safety,
    routes_scheduled_tasks,
    routes_settings,
    routes_shells,
    routes_skills,
    routes_system,
    routes_tasks,
    routes_tools,
    routes_traces,
    routes_voice,
    routes_vector,
)
from app.core.config import ConfigError
from app.core.errors import (
    AppError,
    app_error_handler,
    config_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.lifespan import lifespan
from app.db.migrator import MigrationError


def create_app() -> FastAPI:
    app = FastAPI(title="Agent OS Local API", version="0.1.0", lifespan=lifespan)
    app.add_exception_handler(AppError, cast(ExceptionHandler, app_error_handler))
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, http_error_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_error_handler),
    )
    app.add_exception_handler(ConfigError, cast(ExceptionHandler, config_error_handler))
    app.add_exception_handler(MigrationError, cast(ExceptionHandler, config_error_handler))
    app.add_exception_handler(ShellRuntimeError, cast(ExceptionHandler, config_error_handler))
    app.add_exception_handler(Exception, cast(ExceptionHandler, unhandled_error_handler))

    @app.middleware("http")
    async def api_trace_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        registry = getattr(request.app.state, "registry", None)
        if registry is None:
            return await call_next(request)

        trace_id = await registry.trace_service.start_trace()
        request.state.trace_id = trace_id
        span_id = await registry.trace_service.start_span(
            trace_id,
            span_type=TraceSpanType.API_REQUEST,
            name=f"{request.method} {request.url.path}",
            metadata={"method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            await registry.trace_service.end_span(span_id, status=TraceSpanStatus.FAILED)
            await registry.trace_service.end_trace(trace_id, status=TraceStatus.FAILED)
            raise

        span_status = (
            TraceSpanStatus.FAILED if response.status_code >= 500 else TraceSpanStatus.COMPLETED
        )
        trace_status = TraceStatus.FAILED if response.status_code >= 500 else TraceStatus.COMPLETED
        await registry.trace_service.end_span(
            span_id,
            status=span_status,
            output_data={"status_code": response.status_code},
        )
        await registry.trace_service.end_trace(trace_id, status=trace_status)
        response.headers["X-Trace-Id"] = trace_id
        return response

    app.include_router(routes_health.router)
    app.include_router(routes_shells.router)
    app.include_router(routes_organization.router)
    app.include_router(routes_org_policies.departments_router)
    app.include_router(routes_org_policies.roles_router)
    app.include_router(routes_members.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_agent_workbench.router)
    app.include_router(routes_brains.router)
    app.include_router(routes_brains.decision_router)
    app.include_router(routes_model_routing.router)
    app.include_router(routes_assets.router)
    app.include_router(routes_browser.router)
    app.include_router(routes_browser_workflows.router)
    app.include_router(routes_project_deployments.workspace_router)
    app.include_router(routes_project_deployments.deployment_router)
    app.include_router(routes_project_deployments.toolchain_router)
    app.include_router(routes_project_deployments.host_install_router)
    app.include_router(routes_capabilities.router)
    app.include_router(routes_channels.router)
    app.include_router(routes_knowledge.router)
    app.include_router(routes_goals.router)
    app.include_router(routes_tasks.router)
    app.include_router(routes_checkpoints.router)
    app.include_router(routes_scheduled_tasks.router)
    app.include_router(routes_scheduled_tasks.run_router)
    app.include_router(routes_notifications.router)
    app.include_router(routes_approvals.router)
    app.include_router(routes_tools.router)
    app.include_router(routes_execution_boundary.router)
    app.include_router(routes_extensions.router)
    app.include_router(routes_external_platform.router)
    app.include_router(routes_media.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_skills.router)
    app.include_router(routes_plugins.router)
    app.include_router(routes_mcp.router)
    app.include_router(routes_release.release_router)
    app.include_router(routes_release.eval_router)
    app.include_router(routes_release.security_router)
    app.include_router(routes_release.backup_router)
    app.include_router(routes_release.restore_router)
    app.include_router(routes_release.benchmark_router)
    app.include_router(routes_release.diagnostic_router)
    app.include_router(routes_safety.router)
    app.include_router(routes_persona.persona_router)
    app.include_router(routes_persona.heart_router)
    app.include_router(routes_persona.persona_heart_router)
    app.include_router(routes_response_composer.router)
    app.include_router(routes_retrieval.router)
    app.include_router(routes_vector.router)
    app.include_router(routes_artifacts.router)
    app.include_router(routes_memory.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_audit.router)
    app.include_router(routes_system.router)
    app.include_router(routes_traces.router)
    return app


app = create_app()
