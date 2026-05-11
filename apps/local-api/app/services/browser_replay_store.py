from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.time import utc_now_iso
from app.schemas.browser_workflows import BrowserWorkflowReplayResponse


class BrowserReplayStore:
    def __init__(
        self,
        *,
        browser_sessions: Any | None,
        workflow_repo: Any | None = None,
    ) -> None:
        self._browser_sessions = browser_sessions
        self._workflow_repo = workflow_repo

    async def record_observation(
        self,
        *,
        request: Any,
        tool_call_id: str,
        organization_id: str,
        action: str,
        result: dict[str, Any],
        session_context: dict[str, Any],
        page_state: dict[str, Any],
        safety: dict[str, Any],
        trace_id: str | None,
        screenshot_artifact_id: str | None = None,
        download_artifact_id: str | None = None,
        artifact_ids: list[str] | None = None,
        task_checkpoint: dict[str, Any],
        dom_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            return result
        evidence = await self._browser_sessions.record_evidence(
            task_id=request.task_id,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            action_status=str(result.get("action_status") or "completed"),
            url=result.get("url"),
            title=result.get("title"),
            http_status=result.get("http_status"),
            evidence_summary=str(result.get("evidence_summary") or "browser evidence"),
            snapshot_preview=str(result.get("snapshot") or result.get("content_preview") or "") or None,
            screenshot_artifact_id=screenshot_artifact_id,
            download_artifact_id=download_artifact_id,
            artifact_ids=artifact_ids,
            network_summary=dict(
                result.get("network_summary")
                or {
                    "request_count": 1 if result.get("url") else 0,
                    "failed_count": 1 if result.get("action_status") == "http_error" else 0,
                    "http_status": result.get("http_status"),
                }
            ),
            console_summary=dict(
                result.get("console_summary") or {"error_count": 0, "warning_count": 0}
            ),
            redaction_summary={
                "session_handle_redacted": True,
                "executor_backend": result.get("backend"),
                "backend_status": result.get("backend_status"),
            },
            safety_decision=safety,
            session_context=session_context,
            trace_id=trace_id,
        )
        result["browser_evidence_id"] = evidence.browser_evidence_id
        result["browser_evidence"] = evidence.model_dump(mode="json")
        refs = [
            {
                "type": "browser_evidence",
                "id": evidence.browser_evidence_id,
                "action": action,
                "recorded_at": utc_now_iso(),
            }
        ]
        page_state = {**page_state, "evidence_refs": refs}
        stored_page_state = await self._browser_sessions.record_page_state(
            task_id=request.task_id,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            action_status=str(result.get("action_status") or "completed"),
            page_key=str(page_state.get("page_key") or "browser_page"),
            current_url=page_state.get("current_url"),
            title=page_state.get("page_title"),
            http_status=result.get("http_status"),
            dom_summary=dom_summary,
            network_summary=dict(
                result.get("network_summary")
                or {
                    "request_count": 1 if result.get("url") else 0,
                    "failed_count": 1 if result.get("action_status") == "http_error" else 0,
                    "http_status": result.get("http_status"),
                }
            ),
            console_summary=dict(
                result.get("console_summary") or {"error_count": 0, "warning_count": 0}
            ),
            task_checkpoint=task_checkpoint,
            redaction_summary={
                "session_handle_redacted": True,
                "storage_state_redacted": True,
                "download_path_visible": False,
            },
            session_context=session_context,
            trace_id=trace_id,
            browser_evidence_id=evidence.browser_evidence_id,
        )
        result["browser_page_state"] = {
            **page_state,
            "page_state_id": stored_page_state.page_state_id,
            "page_key": stored_page_state.page_key,
            "evidence_refs": refs,
        }
        return result

    async def latest_page_state(
        self,
        task_id: str,
        page_key: str | None = None,
    ) -> dict[str, Any] | None:
        if self._browser_sessions is None:
            return None
        rows = await self._browser_sessions.list_page_states(task_id=task_id, page_key=page_key)
        if not rows:
            return None
        return rows[-1].model_dump(mode="json")

    async def replay_bundle(
        self,
        plan_id: str,
        *,
        base_response: BrowserWorkflowReplayResponse,
    ) -> BrowserWorkflowReplayResponse:
        if self._workflow_repo is None:
            return base_response
        task_id = base_response.plan.task_id
        page_states = []
        if task_id and self._browser_sessions is not None:
            page_states = [
                item.model_dump(mode="json")
                for item in await self._browser_sessions.list_page_states(task_id=task_id)
            ]
        return base_response.model_copy(
            update={
                "redaction_summary": {
                    **base_response.redaction_summary,
                    "replay_store": "browser_replay_store",
                    "page_state_count": len(page_states),
                },
            }
        )
