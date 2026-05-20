from __future__ import annotations

from typing import Any

from brain.adapters import ModelAdapterError
from core_types import ChatEvent, ChatTurnRequest, ContextPacket, TaskMode

from app.core.time import new_id, utc_now_iso
from app.services.chat_experience import ClarificationDecision
from app.services.chat_intent_router import (
    OfficeChatRequest,
    preferred_office_bundle_id,
    preferred_office_tool_name,
)
from app.services.chat_runtime_host_helpers import (
    browser_capability_explanation_reply as _browser_capability_explanation_reply,
    channel_profile_for_turn as _channel_profile_for_turn,
    content_payload as _content_payload,
    debounce_delay_seconds as _debounce_delay_seconds,
    deterministic_boundary_reply as _deterministic_boundary_reply,
    direct_route_reply as _direct_route_reply,
    error_signature as _error_signature,
    event_from_persisted as _event_from_persisted,
    first_office_artifact as _first_office_artifact,
    message_user_text as _message_user_text,
    model_failure_type as _model_failure_type,
    office_content_summary as _office_content_summary,
    office_doc_visible_name as _office_doc_visible_name,
    office_next_edit_hint as _office_next_edit_hint,
    office_package_ref_suffix as _office_package_ref_suffix,
    office_reply_detail as _office_reply_detail,
    phase52_deploy_or_install_explain_only as _phase52_deploy_or_install_explain_only,
    prompt_payload_from_metadata as _prompt_payload_from_metadata,
    queue_lock_until as _queue_lock_until,
    queue_payload as _queue_payload,
    reply_option_items as _reply_option_items,
    request_text as _request_text,
    session_id_from_message as _session_id_from_message,
    title_from_text as _title_from_text,
)
from app.services.natural_chat import pending_action_from_approval

DEFAULT_USER_ID = "user_local_owner"


class ChatFacadeShellMixin:
    def _clarification_from_brain(
        self,
        turn: dict[str, Any],
        brain_decision: Any,
    ) -> ClarificationDecision | None:
        data = dict(brain_decision.clarification or {})
        if not data.get("needs_clarification"):
            return None
        now = utc_now_iso()
        return ClarificationDecision(
            clarification_id=new_id("clarify"),
            turn_id=turn["turn_id"],
            conversation_id=turn["conversation_id"],
            needs_clarification=True,
            reason=str(data.get("reason") or "clarification_required"),
            clarification_type=str(data.get("clarification_type") or "missing_goal"),
            blocking_level=str(data.get("blocking_level") or "requires_answer"),
            questions=[str(item) for item in data.get("questions", [])][:3],
            can_answer_partially=bool(data.get("safe_partial_answer_allowed", False)),
            trace_id=turn["trace_id"],
            created_at=now,
            updated_at=now,
        )

    def _task_mode_from_brain(self, mode: str | None) -> TaskMode:
        if mode == TaskMode.DIRECT_WITH_MEMORY.value:
            return TaskMode.DIRECT_WITH_MEMORY
        if mode == TaskMode.WORKFLOW.value:
            return TaskMode.WORKFLOW
        if mode == TaskMode.AGENT.value:
            return TaskMode.AGENT
        if mode == TaskMode.SUPERVISOR.value:
            return TaskMode.SUPERVISOR
        return TaskMode.DIRECT

    def _intent_creates_task(self, intent: str) -> bool:
        return self._task_coordinator.intent_creates_task(intent)

    def _message_user_text_from_message(self, message: dict[str, Any] | None) -> str:
        return _message_user_text(message)

    def _session_id_from_message(self, message: dict[str, Any] | None) -> str | None:
        return _session_id_from_message(message)

    def _content_payload_for_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return _content_payload(envelope)

    def _queue_payload_for_item(self, queue_item: dict[str, Any]) -> dict[str, Any]:
        return _queue_payload(queue_item)

    def _event_from_persisted_row(self, row: dict[str, Any]) -> ChatEvent:
        return _event_from_persisted(row)

    def _request_text_from_request(self, request: ChatTurnRequest) -> str:
        return _request_text(request)

    def _title_from_text(self, text: str) -> str:
        return _title_from_text(text)

    def _new_id(self, prefix: str) -> str:
        return new_id(prefix)

    def _debounce_delay_seconds(
        self,
        metadata: dict[str, Any],
        queue_policy: str,
    ) -> float:
        return _debounce_delay_seconds(metadata, queue_policy)

    def _queue_lock_until(self, seconds: int = 300) -> str:
        return _queue_lock_until(seconds)

    def _error_signature(self, stage: str, failure_type: str, root_cause: str) -> str:
        return _error_signature(stage, failure_type, root_cause)

    def _model_failure_type(self, error: ModelAdapterError | None) -> str:
        return _model_failure_type(error)

    def _redaction_summary(
        self,
        context: ContextPacket,
        *,
        sensitivity_hits: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        return self._context_coordinator.redaction_summary(
            context,
            sensitivity_hits=sensitivity_hits,
        )

    def _deterministic_boundary_reply_text(self, user_text: str) -> str | None:
        return _deterministic_boundary_reply(user_text)

    def _browser_capability_explanation_reply_text(self, user_text: str) -> str | None:
        return _browser_capability_explanation_reply(user_text)

    def _direct_route_reply_for_decision(
        self,
        route_type: str,
        user_text: str,
    ) -> tuple[str, str, dict[str, Any]] | None:
        return _direct_route_reply(route_type, user_text)

    def _phase52_deploy_or_install_explain_only(self, user_text: str) -> bool:
        return _phase52_deploy_or_install_explain_only(user_text)

    def _default_user_id(self) -> str:
        return DEFAULT_USER_ID

    def _pending_action_from_approval(
        self,
        approval: Any,
        *,
        session_id: str | None,
        source_turn_id: str,
    ) -> dict[str, Any]:
        return pending_action_from_approval(
            approval,
            session_id=session_id,
            source_turn_id=source_turn_id,
        )

    def _reply_option_items(self, options: list[str]) -> list[dict[str, str]]:
        return _reply_option_items(options)

    def _channel_profile_for_turn(self, turn: dict[str, Any]) -> str:
        return _channel_profile_for_turn(turn)

    def _prompt_payload_from_metadata(
        self,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _prompt_payload_from_metadata(metadata)

    def _context_persona_payload(self, context: ContextPacket) -> dict[str, Any]:
        return (
            context.persona.model_dump(mode="json")
            if getattr(context, "persona", None) is not None
            and hasattr(context.persona, "model_dump")
            else {}
        )

    def _context_heart_payload(self, context: ContextPacket) -> dict[str, Any]:
        return (
            context.heart.model_dump(mode="json")
            if getattr(context, "heart", None) is not None
            and hasattr(context.heart, "model_dump")
            else {}
        )

    async def _enabled_office_skill_id(self, office_request: OfficeChatRequest) -> str | None:
        if self._skill_plugins is None:
            return None
        preferred_bundle = preferred_office_bundle_id(office_request)
        try:
            for skill in await self._skill_plugins.list_skills(status="enabled"):
                if skill.bundle_id == preferred_bundle and skill.status == "enabled":
                    return str(skill.skill_id)
        except Exception:
            return None
        return None

    async def _office_skill_has_grant(
        self,
        skill_id: str,
        tool_name: str,
        member_id: str,
    ) -> bool:
        if self._skill_governance is None:
            return True
        try:
            for grant in await self._skill_governance.list_grants(skill_id):
                if grant.status != "active":
                    continue
                if grant.subject_type != "member" or grant.subject_id != member_id:
                    continue
                if tool_name in set(grant.allowed_tools):
                    return True
        except Exception:
            return False
        return False

    async def _latest_office_artifact_id(
        self,
        conversation_id: str,
        document_type: str,
    ) -> str | None:
        if self._task_engine is None:
            return None
        content_marker = {
            "word": "wordprocessingml.document",
            "excel": "spreadsheetml.sheet",
            "ppt": "presentationml.presentation",
        }.get(document_type)
        try:
            for task in await self._task_engine.list_tasks(limit=50):
                if str(task.conversation_id or "") != conversation_id:
                    continue
                for artifact in reversed(await self._task_engine.artifacts(task.task_id)):
                    if content_marker and content_marker in str(artifact.content_type or ""):
                        return str(artifact.artifact_id)
        except Exception:
            return None
        return None

    def _office_missing_capability_text(
        self,
        office_request: OfficeChatRequest,
        reason: str,
    ) -> str:
        doc_name = _office_doc_visible_name(office_request.document_type)
        action = "编辑" if office_request.operation == "edit" else "生成"
        source_ref = f"clawhub:official/office/{_office_package_ref_suffix(office_request)}"
        if reason == "missing_enabled_skill":
            return (
                f"你是想{action}{doc_name}，但对应 Office Skill 还没装好。\n"
                "我先把这步按住，没有假装已经生成。\n"
                f"可以用 CLI 装上：`cycber skills install {source_ref} --enable --grant-default`。"
            )
        return (
            f"你是想{action}{doc_name}，Skill 已经找到了，但当前成员还没授权"
            f" `{preferred_office_tool_name(office_request)}`。\n"
            "没有授权我先不写文件，免得把边界踩歪。"
        )

    def _office_next_actions(self, office_request: OfficeChatRequest, reason: str) -> list[str]:
        source_ref = f"clawhub:official/office/{_office_package_ref_suffix(office_request)}"
        if reason == "missing_enabled_skill":
            return [f"cycber skills install {source_ref} --enable --grant-default"]
        return [
            f"cycber skills grant <skill_id> --tool {preferred_office_tool_name(office_request)}"
        ]

    def _office_task_reply(
        self,
        office_request: OfficeChatRequest,
        task: Any,
        artifacts: list[Any],
    ) -> str:
        doc_name = _office_doc_visible_name(office_request.document_type)
        action = "编辑" if office_request.operation == "edit" else "生成"
        if task.status.value != "completed":
            if task.status.value == "waiting_approval":
                return (
                    f"{doc_name}{action}任务已经起好了，但还在等确认。\n"
                    "你点头前我不会写入或改动文件。"
                )
            if task.status.value == "failed":
                return (
                    f"{doc_name}{action}任务这次没跑完。\n"
                    "你可以让我缩小范围、换内容，或者看一下失败原因再来一遍。"
                )
            return (
                f"{doc_name}{action}任务已经起步，当前状态是 {task.status.value}。\n"
                "我会按真实状态继续告诉你。"
            )
        office_artifact = _first_office_artifact(artifacts, office_request.document_type)
        if office_artifact is None:
            return (
                f"{doc_name}{action}任务已经跑完，但没找到对应的文件结果。\n"
                "我不会把这当成真正完成，还是得回头看一下 Skill 输出。"
            )
        detail = _office_reply_detail(office_request)
        summary = _office_content_summary(office_request)
        next_hint = _office_next_edit_hint(office_request.document_type)
        return (
            f"文件已产出：{office_artifact.display_name}。\n"
            f"这次{doc_name}已经{action}完成。\n"
            f"{detail}"
            f"{summary}"
            f"{next_hint}"
        )
