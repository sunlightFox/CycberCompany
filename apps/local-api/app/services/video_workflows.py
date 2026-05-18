from __future__ import annotations

from typing import Any

from core_types import (
    ErrorCode,
    MediaAnalysis,
    MediaEditPlan,
    VideoWorkflowPlan,
    VideoWorkflowProfile,
    VideoWorkflowResult,
    VideoWorkflowStep,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.media_repo import MediaRepository
from app.schemas.media import (
    MediaEditPlanCreateRequest,
    MediaExportArtifactRequest,
    MediaExtractFramesRequest,
    MediaProbeRequest,
    MediaRenderEditRequest,
    MediaSceneDetectRequest,
    MediaTimelineRequest,
    VideoWorkflowCreateRequest,
    VideoWorkflowExecuteRequest,
    VideoWorkflowResponse,
)
from app.schemas.tasks import ToolExecuteRequest
from app.services.media import MediaService
from app.services.tools import ToolRuntime


class VideoWorkflowService:
    def __init__(
        self,
        *,
        repo: MediaRepository,
        media_service: MediaService,
        tool_runtime: ToolRuntime,
    ) -> None:
        self._repo = repo
        self._media = media_service
        self._tools = tool_runtime

    async def create(
        self,
        request: VideoWorkflowCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> VideoWorkflowResponse:
        media = await self._media.get_media(request.media_id)
        if media.task_id != request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "视频工作流必须绑定同一任务下的媒体",
                status_code=403,
            )
        if media.media_type != "video":
            raise AppError(
                ErrorCode.MEDIA_PLAN_INVALID,
                "video workflow 只接受 video media asset",
                status_code=422,
            )
        profile = _normalize_profile(request.workflow_profile)
        now = utc_now_iso()
        result = VideoWorkflowResult(
            provider_status=_provider_status(self._media.runtime_status()),
            not_run_effects=_planned_not_run_effects(request.goal, profile),
        )
        data = {
            "workflow_id": new_id("vwf"),
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "media_id": media.media_id,
            "goal": str(redact(request.goal)),
            "status": "planned",
            "profile": profile.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "evidence": {
                "source_boundary": "task_artifact_only",
                "media_id": media.media_id,
                "source_artifact_id": media.source_artifact_id,
            },
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_video_workflow(data)
        await self._record_benchmark(
            workflow_id=data["workflow_id"],
            organization_id=media.organization_id,
            task_id=media.task_id,
            scenario_key="schema_and_api",
            layer="workflow",
            expected_result={"status": "planned"},
            observed_result={"status": "planned", "media_id": media.media_id},
            status="passed",
        )
        workflow = await self._workflow(data["workflow_id"])
        return VideoWorkflowResponse(
            workflow=workflow,
            media=media,
            message="视频工作流已创建",
            next_step="execute",
        )

    async def get(self, workflow_id: str) -> VideoWorkflowResponse:
        workflow = await self._workflow(workflow_id)
        media = await self._media.get_media(workflow.media_id)
        return VideoWorkflowResponse(
            workflow=workflow,
            steps=await self._steps(workflow_id),
            media=media,
            message="视频工作流已加载",
            next_step=_next_step_for(workflow),
        )

    async def execute(
        self,
        workflow_id: str,
        request: VideoWorkflowExecuteRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> VideoWorkflowResponse:
        request = request or VideoWorkflowExecuteRequest()
        workflow = await self._workflow(workflow_id)
        media = await self._media.get_media(workflow.media_id)
        profile = workflow.profile
        result = workflow.result
        status = "running"
        await self._repo.update_video_workflow(
            workflow_id,
            {"status": status, "updated_at": utc_now_iso(), "trace_id": trace_id},
        )

        max_size = int(profile.constraints.get("max_size_bytes") or 0)
        if max_size and media.size_bytes and media.size_bytes > max_size:
            result.residual_risk.append("媒体文件超过视频工作流大小限制，已降级为摘要/计划输出。")
            result.not_run_effects.append("heavy_video_render")
            result.provider_status = _provider_status(self._media.runtime_status())
            await self._update_result(workflow_id, result, status="degraded")
            return await self.get(workflow_id)

        try:
            probe = await self._run_step(
                workflow,
                "probe",
                {"refresh": False},
                lambda: self._media.probe(media.media_id, MediaProbeRequest(), trace_id=trace_id),
            )
            media = probe.media or await self._media.get_media(workflow.media_id)

            frames = await self._run_step(
                workflow,
                "extract_frames",
                {"interval_ms": profile.frame_interval_ms, "max_frames": profile.max_frames},
                lambda: self._media.extract_frames(
                    media.media_id,
                    MediaExtractFramesRequest(
                        interval_ms=profile.frame_interval_ms,
                        max_frames=profile.max_frames,
                    ),
                    trace_id=trace_id,
                ),
            )
            scene = await self._run_step(
                workflow,
                "scene_map",
                {"threshold": profile.scene_threshold, "max_segments": profile.max_segments},
                lambda: self._media.scene_detect(
                    media.media_id,
                    MediaSceneDetectRequest(
                        threshold=profile.scene_threshold,
                        max_segments=profile.max_segments,
                    ),
                    trace_id=trace_id,
                ),
            )
            timeline = await self._run_step(
                workflow,
                "timeline_summary",
                {
                    "include_transcript": profile.include_transcript,
                    "include_frames": profile.include_frames,
                },
                lambda: self._media.timeline(
                    media.media_id,
                    MediaTimelineRequest(
                        include_transcript=profile.include_transcript,
                        include_frames=profile.include_frames,
                    ),
                    trace_id=trace_id,
                ),
            )
        except AppError as exc:
            if exc.code != ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                raise
            result.provider_status = _provider_status(self._media.runtime_status())
            result.residual_risk.append(f"媒体后端不可用：{exc.message}")
            result.deliverable = False
            await self._update_result(workflow_id, result, status="degraded")
            return await self.get(workflow_id)
        result.timeline_summary = _timeline_summary(media, timeline.analysis, frames.evidence)
        result.scene_map = _scene_map(scene.analysis)
        result.provider_status = _provider_status(self._media.runtime_status())
        await self._record_benchmark(
            workflow_id=workflow_id,
            organization_id=workflow.organization_id,
            task_id=workflow.task_id,
            scenario_key="timeline_scene_edl",
            layer="workflow",
            expected_result={"timeline": True, "scene_map": True},
            observed_result={
                "timeline_summary": result.timeline_summary,
                "scene_map_count": len(result.scene_map),
            },
            status="passed" if result.timeline_summary and result.scene_map else "failed",
        )

        if _any_degraded(probe, frames, scene, timeline):
            result.residual_risk.append("至少一个视频观察步骤降级，结果不可视为完全交付。")

        plan_response = await self._run_step(
            workflow,
            "edit_decision_list",
            {"goal": workflow.goal, "render": False},
            lambda: self._media.create_edit_plan(
                media.media_id,
                MediaEditPlanCreateRequest(
                    goal=workflow.goal,
                    output_profile={"container": "mp4", "workflow_id": workflow.workflow_id},
                    operations=_workflow_operations(media, workflow.goal),
                    render=False,
                ),
                trace_id=trace_id,
            ),
        )
        edit_plan = plan_response.edit_plan
        result.edit_decision_list = _edit_decision_list(edit_plan)
        await self._record_benchmark(
            workflow_id=workflow_id,
            organization_id=workflow.organization_id,
            task_id=workflow.task_id,
            scenario_key="timeline_scene_edl",
            layer="workflow",
            expected_result={"edit_decision_list": True},
            observed_result={"edit_decision_list_count": len(result.edit_decision_list)},
            status="passed" if result.edit_decision_list else "failed",
        )
        await self._repo.update_video_workflow(
            workflow_id,
            {"edit_plan_id": edit_plan.edit_plan_id, "updated_at": utc_now_iso()},
        )

        if not profile.require_render:
            result.deliverable = not result.residual_risk
            await self._update_result(workflow_id, result, status="completed")
            return await self.get(workflow_id)

        render_response = await self._render_with_approval(
            workflow,
            edit_plan,
            approval_id=request.approval_id,
            trace_id=trace_id,
        )
        if render_response["status"] == "approval_required":
            await self._record_benchmark(
                workflow_id=workflow_id,
                organization_id=workflow.organization_id,
                task_id=workflow.task_id,
                scenario_key="render_approval_repair",
                layer="approval",
                expected_result={"approval_required": True},
                observed_result=render_response,
                status="passed" if render_response.get("approval_id") else "failed",
            )
            result.residual_risk.append("视频渲染仍在等待审批，尚不可视为最终交付。")
            await self._update_result(
                workflow_id,
                result,
                status="waiting_approval",
                approval_id=render_response.get("approval_id"),
            )
            return await self.get(workflow_id)

        result.render_output = render_response
        await self._record_benchmark(
            workflow_id=workflow_id,
            organization_id=workflow.organization_id,
            task_id=workflow.task_id,
            scenario_key="render_approval_repair",
            layer="approval",
            expected_result={"status": "rendered"},
            observed_result=render_response,
            status="passed" if render_response.get("status") == "rendered" else "failed",
        )
        if render_response.get("status") == "rendered":
            result.deliverable = True
            await self._run_export_if_needed(workflow, result, trace_id=trace_id)
            await self._update_result(workflow_id, result, status="completed")
        else:
            result.deliverable = False
            result.residual_risk.append("视频渲染失败，未生成最终可交付文件。")
            await self._update_result(workflow_id, result, status="degraded")
        return await self.get(workflow_id)

    async def resume(
        self,
        workflow_id: str,
        approval_id: str | None,
        *,
        trace_id: str | None = None,
    ) -> VideoWorkflowResponse:
        workflow = await self._workflow(workflow_id)
        return await self.execute(
            workflow_id,
            VideoWorkflowExecuteRequest(approval_id=approval_id or workflow.approval_id),
            trace_id=trace_id,
        )

    async def replay_task_video_workflows(self, task_id: str) -> list[dict[str, Any]]:
        workflows = await self._repo.list_video_workflows_by_task(task_id)
        results: list[dict[str, Any]] = []
        for workflow in workflows:
            steps = await self._repo.list_video_workflow_steps(workflow["workflow_id"])
            benchmarks = await self._repo.list_video_workflow_benchmarks_by_task(task_id)
            results.append(
                redact(
                    {
                        "workflow": workflow,
                        "steps": steps,
                        "benchmarks": [
                            item
                            for item in benchmarks
                            if item.get("workflow_id") == workflow["workflow_id"]
                        ],
                        "source_boundary": "task_artifact_only",
                        "raw_media_content_included": False,
                    }
                )
            )
        return results

    async def _workflow(self, workflow_id: str) -> VideoWorkflowPlan:
        row = await self._repo.get_video_workflow(workflow_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "视频工作流不存在", status_code=404)
        return VideoWorkflowPlan(**row)

    async def _steps(self, workflow_id: str) -> list[VideoWorkflowStep]:
        return [
            VideoWorkflowStep(**row)
            for row in await self._repo.list_video_workflow_steps(workflow_id)
        ]

    async def _run_step(
        self,
        workflow: VideoWorkflowPlan,
        step_key: str,
        step_input: dict[str, Any],
        operation: Any,
    ) -> Any:
        now = utc_now_iso()
        step_id = new_id("vwfs")
        await self._repo.insert_video_workflow_step(
            {
                "step_id": step_id,
                "workflow_id": workflow.workflow_id,
                "organization_id": workflow.organization_id,
                "task_id": workflow.task_id,
                "media_id": workflow.media_id,
                "step_key": step_key,
                "status": "running",
                "input": step_input,
                "started_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            response = await operation()
            output = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
            await self._repo.update_video_workflow_step(
                step_id,
                {
                    "status": output.get("status", "completed"),
                    "output": output,
                    "completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                },
            )
            return response
        except AppError as exc:
            await self._repo.update_video_workflow_step(
                step_id,
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "error_summary": exc.message,
                    "completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                },
            )
            raise

    async def _render_with_approval(
        self,
        workflow: VideoWorkflowPlan,
        edit_plan: MediaEditPlan,
        *,
        approval_id: str | None,
        trace_id: str | None,
    ) -> dict[str, Any]:
        render_args = {
            "edit_plan_id": edit_plan.edit_plan_id,
            "render_strategy": workflow.profile.render_strategy,
            "requires_human_approval": True,
        }
        try:
            first = await self._tool_render_step(
                workflow,
                render_args,
                approval_id=approval_id,
                trace_id=trace_id,
                attempt=1,
            )
        except AppError as exc:
            if exc.code != ErrorCode.MEDIA_RUNTIME_FAILED.value:
                return {
                    "status": "failed",
                    "error_code": exc.code,
                    "error_summary": exc.message,
                    "repair_attempted": False,
                }
            repaired = await self._render_repair_attempt(
                workflow,
                edit_plan,
                approval_id=approval_id,
                trace_id=trace_id,
                first_error=exc,
            )
            return repaired
        if first.tool_call.status == "approval_required":
            return {
                "status": "approval_required",
                "approval_id": first.approval.approval_id if first.approval else None,
                "tool_call_id": first.tool_call.tool_call_id,
            }
        result = dict(first.result or {})
        rendered_plan = dict(result.get("edit_plan") or {})
        return {
            "status": rendered_plan.get("status") or first.tool_call.status,
            "artifact_id": (first.artifacts[0].artifact_id if first.artifacts else None),
            "rendered_media_id": rendered_plan.get("rendered_media_id"),
            "renderer": dict(rendered_plan.get("evidence") or {}).get("renderer"),
            "strategy": workflow.profile.render_strategy,
            "tool_call_id": first.tool_call.tool_call_id,
            "repair_attempted": False,
        }

    async def _render_repair_attempt(
        self,
        workflow: VideoWorkflowPlan,
        edit_plan: MediaEditPlan,
        *,
        approval_id: str | None,
        trace_id: str | None,
        first_error: AppError,
    ) -> dict[str, Any]:
        await self._record_render_failure(
            workflow,
            attempt=1,
            error_code=first_error.code,
            error_summary=first_error.message,
            evidence={"details": first_error.details or {}},
        )
        try:
            retry = await self._tool_render_step(
                workflow,
                {"edit_plan_id": edit_plan.edit_plan_id, "render_strategy": "safe_reencode"},
                approval_id=approval_id,
                trace_id=trace_id,
                attempt=2,
            )
        except AppError as exc:
            await self._record_render_failure(
                workflow,
                attempt=2,
                error_code=exc.code,
                error_summary=exc.message,
                evidence={"strategy": "safe_reencode", "details": exc.details or {}},
            )
            return {
                "status": "failed",
                "error_code": exc.code,
                "error_summary": exc.message,
                "repair_attempted": True,
                "repair_outcome": "failed",
            }
        result = dict(retry.result or {})
        rendered_plan = dict(result.get("edit_plan") or {})
        return {
            "status": rendered_plan.get("status") or retry.tool_call.status,
            "artifact_id": (retry.artifacts[0].artifact_id if retry.artifacts else None),
            "rendered_media_id": rendered_plan.get("rendered_media_id"),
            "renderer": dict(rendered_plan.get("evidence") or {}).get("renderer"),
            "strategy": "safe_reencode",
            "tool_call_id": retry.tool_call.tool_call_id,
            "repair_attempted": True,
            "repair_outcome": "resolved",
        }

    async def _tool_render_step(
        self,
        workflow: VideoWorkflowPlan,
        render_args: dict[str, Any],
        *,
        approval_id: str | None,
        trace_id: str | None,
        attempt: int,
    ) -> Any:
        now = utc_now_iso()
        step_id = new_id("vwfs")
        await self._repo.insert_video_workflow_step(
            {
                "step_id": step_id,
                "workflow_id": workflow.workflow_id,
                "organization_id": workflow.organization_id,
                "task_id": workflow.task_id,
                "media_id": workflow.media_id,
                "step_key": "render_output",
                "status": "running",
                "attempt": attempt,
                "input": render_args,
                "started_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            response = await self._tools.execute(
                ToolExecuteRequest(
                    task_id=workflow.task_id,
                    tool_name="media.render_edit",
                    approval_id=approval_id,
                    args=render_args,
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            await self._repo.update_video_workflow_step(
                step_id,
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "error_summary": exc.message,
                    "completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                },
            )
            raise
        await self._repo.update_video_workflow_step(
            step_id,
            {
                "status": response.tool_call.status,
                "output": response.model_dump(mode="json"),
                "completed_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        return response

    async def _record_render_failure(
        self,
        workflow: VideoWorkflowPlan,
        *,
        attempt: int,
        error_code: str,
        error_summary: str,
        evidence: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        await self._repo.insert_video_workflow_step(
            {
                "step_id": new_id("vwfs"),
                "workflow_id": workflow.workflow_id,
                "organization_id": workflow.organization_id,
                "task_id": workflow.task_id,
                "media_id": workflow.media_id,
                "step_key": "render_repair",
                "status": "failed",
                "attempt": attempt,
                "error_code": error_code,
                "error_summary": error_summary,
                "evidence": evidence,
                "created_at": now,
                "updated_at": now,
                "completed_at": now,
            }
        )

    async def _run_export_if_needed(
        self,
        workflow: VideoWorkflowPlan,
        result: VideoWorkflowResult,
        *,
        trace_id: str | None,
    ) -> None:
        if not workflow.profile.require_export:
            return
        rendered_media_id = result.render_output.get("rendered_media_id")
        if not rendered_media_id:
            result.residual_risk.append("缺少 rendered_media_id，无法准备导出。")
            return
        export = await self._media.export_artifact(
            str(rendered_media_id),
            request=MediaExportArtifactRequest(export_mode="prepare"),
            trace_id=trace_id,
        )
        result.export_summary = export.evidence

    async def _update_result(
        self,
        workflow_id: str,
        result: VideoWorkflowResult,
        *,
        status: str,
        approval_id: str | None = None,
    ) -> None:
        await self._repo.update_video_workflow(
            workflow_id,
            {
                "status": status,
                "approval_id": approval_id,
                "result": result.model_dump(mode="json"),
                "updated_at": utc_now_iso(),
            },
        )
        workflow = await self._workflow(workflow_id)
        await self._record_benchmark(
            workflow_id=workflow_id,
            organization_id=workflow.organization_id,
            task_id=workflow.task_id,
            scenario_key="degraded_provider",
            layer="provider",
            expected_result={"video_generation_status": "degraded"},
            observed_result={
                "status": status,
                "provider_status": result.provider_status,
                "deliverable": result.deliverable,
            },
            status=(
                "passed"
                if dict(result.provider_status.get("video_generation") or {}).get("status")
                == "degraded"
                else "failed"
            ),
        )
        await self._record_benchmark(
            workflow_id=workflow_id,
            organization_id=workflow.organization_id,
            task_id=workflow.task_id,
            scenario_key="task_replay_result",
            layer="replay",
            expected_result={"source_boundary": "task_artifact_only"},
            observed_result={
                "status": status,
                "source_boundary": "task_artifact_only",
                "raw_media_content_included": False,
                "deliverable": result.deliverable,
            },
            status="passed",
        )

    async def _record_benchmark(
        self,
        *,
        workflow_id: str,
        organization_id: str,
        task_id: str,
        scenario_key: str,
        layer: str,
        expected_result: dict[str, Any],
        observed_result: dict[str, Any],
        status: str,
    ) -> None:
        now = utc_now_iso()
        await self._repo.insert_video_workflow_benchmark(
            {
                "benchmark_id": new_id("vwb"),
                "workflow_id": workflow_id,
                "organization_id": organization_id,
                "task_id": task_id,
                "scenario_key": scenario_key,
                "layer": layer,
                "expected_result": expected_result,
                "observed_result": redact(observed_result),
                "status": status,
                "created_at": now,
                "updated_at": now,
            }
        )


def _normalize_profile(profile: VideoWorkflowProfile) -> VideoWorkflowProfile:
    caps = {
        "local_probe": True,
        "local_timeline": True,
        "local_render": True,
        "video_generation": False,
        "generation_provider_status": "not_configured",
    }
    return profile.model_copy(
        update={
            "provider_capabilities": {
                **caps,
                **dict(profile.provider_capabilities or {}),
            }
        }
    )


def _provider_status(runtime_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "media_runtime": runtime_status,
        "video_generation": {
            "status": "degraded",
            "degraded_reason": "provider_not_configured",
            "implemented_in_phase102": False,
        },
    }


def _planned_not_run_effects(goal: str, profile: VideoWorkflowProfile) -> list[str]:
    text = goal.lower()
    effects: list[str] = []
    if any(token in text for token in ["字幕", "subtitle", "caption"]):
        effects.append("subtitle_render")
    if any(token in text for token in ["音轨", "配乐", "music", "audio"]):
        effects.append("advanced_audio_mix")
    if any(token in text for token in ["生成视频", "text-to-video", "image-to-video"]):
        effects.append("video_generation_provider")
    if not profile.require_export:
        effects.append("external_upload")
    return sorted(set(effects))


def _timeline_summary(
    media: Any,
    analysis: MediaAnalysis | None,
    frame_evidence: dict[str, Any],
) -> dict[str, Any]:
    segments = list(analysis.segments if analysis is not None else [])
    return {
        "media_id": media.media_id,
        "duration_ms": media.duration_ms,
        "segment_count": len(segments),
        "segments": segments,
        "evidence_artifact_ids": list(
            analysis.evidence_artifact_ids if analysis is not None else []
        ),
        "frame_count": frame_evidence.get("frame_count"),
        "include_frames": True,
    }


def _scene_map(analysis: MediaAnalysis | None) -> list[dict[str, Any]]:
    return list(analysis.segments if analysis is not None else [])


def _workflow_operations(media: Any, goal: str) -> list[dict[str, Any]]:
    duration = media.duration_ms or 30000
    end_ms = min(duration, 5000 if "5" in goal else 30000)
    return [
        {
            "type": "trim",
            "source_start_ms": 0,
            "source_end_ms": max(1000, end_ms),
            "reason": "video_workflow_safe_default",
        }
    ]


def _edit_decision_list(edit_plan: MediaEditPlan) -> list[dict[str, Any]]:
    return [
        {
            **dict(operation),
            "edit_plan_id": edit_plan.edit_plan_id,
            "status": edit_plan.status,
        }
        for operation in edit_plan.operations
    ]


def _any_degraded(*responses: Any) -> bool:
    return any(getattr(item, "status", "") == "degraded" for item in responses)


def _next_step_for(workflow: VideoWorkflowPlan) -> str | None:
    if workflow.status == "planned":
        return "execute"
    if workflow.status == "waiting_approval":
        return "approve media.render_edit and resume"
    if workflow.status in {"completed", "degraded", "failed"}:
        return None
    return "poll"
