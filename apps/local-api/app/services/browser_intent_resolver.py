from __future__ import annotations

from typing import Any

from core_types import BrowserWorkflowIntent
from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.schemas.browser_workflows import (
    BrowserWorkflowIntentResolveRequest,
    BrowserWorkflowIntentResolveResponse,
)
from app.services.browser_workflows import (
    _classify_action,
    _content_summary,
    _extract_url,
    _host,
    _normalize_action_type,
    _normalize_url,
)


class BrowserIntentResolver:
    def __init__(self, *, repo: Any) -> None:
        self._repo = repo

    async def resolve_intent(
        self,
        request: BrowserWorkflowIntentResolveRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowIntentResolveResponse:
        now = utc_now_iso()
        target_url = _normalize_url(request.target_url) or _extract_url(request.text)
        action_type = _normalize_action_type(request.action_type) or _classify_action(
            request.text,
            request.constraints,
        )
        missing_fields: list[str] = []
        if not target_url:
            missing_fields.append("target_url")
        account_candidates = request.constraints.get("account_candidates") or []
        if (
            isinstance(account_candidates, list)
            and len(account_candidates) > 1
            and not request.constraints.get("selected_account_id")
            and not request.constraints.get("session_handle_id")
        ):
            missing_fields.append("account")
        status = "clarification_needed" if missing_fields else "resolved"
        target_key = _host(target_url) if target_url else None
        intent_data = {
            "intent_id": new_id("bwint"),
            "organization_id": request.organization_id,
            "member_id": request.member_id,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "trace_id": trace_id,
            "natural_language_goal": str(redact(request.text)),
            "action_type": action_type,
            "target_url": target_url,
            "target_key": target_key,
            "content_summary": request.content_summary or _content_summary(request.text),
            "constraints": redact(request.constraints),
            "missing_fields": missing_fields,
            "status": status,
            "confidence": 0.86 if status == "resolved" else 0.45,
            "resolver_evidence": {
                "resolver": "browser_intent_resolver",
                "target_url_detected": bool(target_url),
                "action_type_detected": action_type,
                "account_ambiguous": "account" in missing_fields,
            },
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_intent(intent_data)
        intent = BrowserWorkflowIntent(**intent_data)
        if "target_url" in missing_fields:
            message = "我需要先知道目标网站或网页地址，然后才能自动观察和规划浏览器操作。"
            next_step = "ask_target_url"
        elif "account" in missing_fields:
            message = "这个目标有多个可用账号，请先告诉我要用哪个账号。"
            next_step = "ask_account"
        else:
            message = "目标已明确，我可以创建浏览器工作流计划。"
            next_step = "create_plan"
        return BrowserWorkflowIntentResolveResponse(
            intent=intent,
            message=message,
            next_step=next_step,
        )
