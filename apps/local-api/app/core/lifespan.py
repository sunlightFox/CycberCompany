from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from core_types import TraceSpanStatus, TraceSpanType, TraceStatus
from fastapi import FastAPI
from shell_runtime import ShellRuntime

from app.core.config import ensure_data_dirs, load_app_config
from app.core.logging import configure_logging
from app.db.migrator import run_migrations
from app.db.session import Database
from app.services.registry import build_registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    config = load_app_config()
    ensure_data_dirs(config)

    db = Database(config.storage.sqlite_path)
    await db.connect()
    executed_migrations = await run_migrations(db, config.paths.migrations_dir)

    shell_runtime = ShellRuntime(config.paths.shells_dir, config.app.default_shell)
    registry = build_registry(config, db, shell_runtime)

    startup_trace_id = await registry.trace_service.start_trace()
    root_span_id = await registry.trace_service.start_span(
        startup_trace_id,
        span_type=TraceSpanType.APP_STARTUP,
        name="local api startup",
        metadata={"default_shell": config.app.default_shell},
    )
    try:
        config_span_id = await registry.trace_service.start_span(
            startup_trace_id,
            span_type=TraceSpanType.CONFIG_LOAD,
            name="load runtime config",
            parent_span_id=root_span_id,
            metadata={
                "config_dir": str(config.paths.config_dir),
                "data_dir": str(config.storage.data_dir),
            },
        )
        await registry.trace_service.end_span(config_span_id)

        migration_span_id = await registry.trace_service.start_span(
            startup_trace_id,
            span_type=TraceSpanType.DB_MIGRATION,
            name="run sqlite migrations",
            parent_span_id=root_span_id,
            metadata={"migrations_dir": str(config.paths.migrations_dir)},
        )
        await registry.trace_service.end_span(
            migration_span_id,
            output_data={"executed": executed_migrations},
        )

        shell_span_id = await registry.trace_service.start_span(
            startup_trace_id,
            span_type=TraceSpanType.SHELL_LOAD,
            name="load default shell",
            parent_span_id=root_span_id,
            metadata={"shell_id": config.app.default_shell},
        )
        registry.shell_runtime.load(config.app.default_shell)
        await registry.trace_service.end_span(shell_span_id)

        bootstrap_span_id = await registry.trace_service.start_span(
            startup_trace_id,
            span_type=TraceSpanType.BOOTSTRAP_ORGANIZATION,
            name="ensure default organization",
            parent_span_id=root_span_id,
            metadata={"shell_id": config.app.default_shell},
        )
        await registry.bootstrap_service.ensure_defaults()
        await registry.trace_service.end_span(bootstrap_span_id)

        soul_span_id = await registry.trace_service.start_span(
            startup_trace_id,
            span_type=TraceSpanType.PERSONA_PROFILE,
            name="ensure member SOUL.md manifests",
            parent_span_id=root_span_id,
        )
        soul_manifests = await registry.persona_heart_service.ensure_soul_manifests_for_members(
            trace_id=startup_trace_id
        )
        await registry.trace_service.end_span(
            soul_span_id,
            output_data={"manifest_count": len(soul_manifests)},
        )

        await registry.chat_service.recover_incomplete_turns()
        await registry.tool_runtime.ensure_builtin_tools()
        try:
            await registry.skill_repository_service.ensure_configured(
                trace_id=startup_trace_id
            )
            await registry.skill_repository_service.refresh_all(trace_id=startup_trace_id)
        except Exception as exc:
            skill_span_id = await registry.trace_service.start_span(
                startup_trace_id,
                span_type=TraceSpanType.APP_STARTUP,
                name="refresh skill repositories",
                parent_span_id=root_span_id,
                metadata={"status": "degraded"},
            )
            await registry.trace_service.end_span(
                skill_span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(exc)},
            )
        await registry.task_engine.recover_stale_jobs()
        await registry.memory_service.recover_stale_jobs()
        await registry.memory_service.process_pending_jobs()
        await registry.runtime_contract_service.ensure_seeded()
    except Exception:
        await registry.trace_service.end_span(root_span_id, status=TraceSpanStatus.FAILED)
        await registry.trace_service.end_trace(startup_trace_id, status=TraceStatus.FAILED)
        raise
    await registry.trace_service.end_span(root_span_id)
    await registry.trace_service.end_trace(startup_trace_id)

    app.state.registry = registry
    if registry.config.workers.startup_tick:
        await registry.background_worker_service.manual_tick()
    await registry.background_worker_service.start()
    try:
        yield
    finally:
        await registry.background_worker_service.stop()
        await registry.tool_runtime.close()
        await db.close()
