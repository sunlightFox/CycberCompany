from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.schemas.notifications import NotificationMessageCreateRequest
from core_types import ErrorCode


class MCPConversationBridge:
    def __init__(
        self,
        *,
        chat_repo: Any,
        task_repo: Any,
        approval_service: Any | None = None,
        notification_gateway: Any | None = None,
        event_bridge: Any | None = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._task_repo = task_repo
        self._approval_service = approval_service
        self._notification_gateway = notification_gateway
        self._event_bridge = event_bridge

    async def list_conversations(self) -> list[dict[str, Any]]:
        return [dict(row) for row in await self._chat_repo.list_conversations()]

    async def read_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = await self._chat_repo.get_conversation(conversation_id)
        if conversation is None:
            raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
        messages = await self._chat_repo.list_messages(conversation_id)
        return {
            "conversation": dict(conversation),
            "messages": [dict(item) for item in messages],
        }

    async def poll_events(
        self,
        *,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if turn_id:
            events = [dict(item) for item in await self._chat_repo.list_events(turn_id)]
            if self._event_bridge is not None:
                return self._event_bridge.normalize_events(events, source="chat")
            return events
        if task_id:
            events = [dict(item) for item in await self._task_repo.list_events(task_id)]
            if self._event_bridge is not None:
                return self._event_bridge.normalize_events(events, source="task")
            return events
        return []

    async def wait_events(
        self,
        *,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.poll_events(turn_id=turn_id, task_id=task_id)

    async def list_approvals(self, task_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in await self._task_repo.list_approvals(task_id)]

    async def respond_approval(
        self,
        *,
        approval_id: str,
        decision: str,
        actor_member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        if self._approval_service is None:
            raise AppError(ErrorCode.APPROVAL_REQUIRED, "审批服务未配置", status_code=409)
        if decision == "approve":
            return await self._approval_service.approve(
                approval_id,
                actor_type="member",
                actor_id=actor_member_id,
                reason=reason,
                trace_id=trace_id,
            )
        if decision == "deny":
            return await self._approval_service.deny(
                approval_id,
                actor_type="member",
                actor_id=actor_member_id,
                reason=reason,
                trace_id=trace_id,
            )
        raise AppError(ErrorCode.VALIDATION_ERROR, "不支持的审批决策", status_code=422)

    async def send_channel_target(
        self,
        *,
        channel_id: str,
        recipient: str,
        body: str,
        message_type: str = "mcp_bridge_message",
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        if self._notification_gateway is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "通知网关未配置", status_code=501)
        return await self._notification_gateway.create_message(
            NotificationMessageCreateRequest(
                channel_id=channel_id,
                message_type=message_type,
                recipient=recipient,
                subject="MCP bridge message",
                body=body,
                metadata=metadata or {},
            ),
            trace_id=trace_id,
        )
