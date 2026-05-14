from __future__ import annotations

from typing import Any

from core_types import ErrorCode

from app.core.errors import AppError
from app.schemas.assets import AssetResolveForToolRequest
from app.schemas.browser import BrowserSessionRestoreContextRequest

_BROWSER_PAGE_STATE_ACTIONS = {
    "open",
    "snapshot",
    "screenshot",
    "vision_snapshot",
    "fill",
    "type",
    "select",
    "check",
    "click",
    "submit",
    "wait",
    "dialog",
    "tabs",
    "frame_action",
    "console",
    "network_summary",
    "download",
    "upload",
    "extract",
}


class BrowserSessionRuntime:
    def __init__(
        self,
        *,
        browser_sessions: Any | None,
        asset_broker: Any,
        replay_store: Any,
    ) -> None:
        self._browser_sessions = browser_sessions
        self._asset_broker = asset_broker
        self._replay_store = replay_store

    async def get_or_create(
        self,
        task_id: str | None,
        member_id: str,
        *,
        tool_name: str,
        args: dict[str, Any],
        approval_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            return self._merge_browser_page_args({}, args)
        handle_id = str(args.get("session_handle_id") or args.get("browser_session_handle_id") or "")
        if handle_id:
            resolved = await self._asset_broker.resolve_for_tool(
                handle_id,
                AssetResolveForToolRequest(
                    subject_id=member_id,
                    action=self._asset_action_for_tool(tool_name),
                    tool_name=tool_name,
                    task_id=task_id,
                    conversation_id=None,
                    approval_id=approval_id,
                ),
                trace_id=trace_id,
            )
            resource = resolved.resource if isinstance(resolved.resource, dict) else {}
            config = resource.get("config") if isinstance(resource.get("config"), dict) else {}
            context = await self._browser_sessions.validate_session_context(
                browser_profile_id=str(config.get("browser_profile_id") or config.get("profile_id") or "") or None,
                browser_session_id=str(config.get("browser_session_id") or "") or None,
                member_id=member_id,
                task_id=task_id,
                url=str(args.get("url") or args.get("current_url") or "") or None,
                allow_login_recovery=bool(args.get("allow_login_recovery")),
            )
            return self._merge_browser_page_args(
                {
                    **context,
                    "asset_handle_id": handle_id,
                    "asset_id": resolved.asset_id,
                    "asset_summary": resolved.summary,
                    "session_handle_resolved": True,
                    "cookie_material_exposed": False,
                },
                args,
            )
        if args.get("browser_profile_id") or args.get("browser_session_id"):
            context = await self._browser_sessions.validate_session_context(
                browser_profile_id=str(args.get("browser_profile_id") or "") or None,
                browser_session_id=str(args.get("browser_session_id") or "") or None,
                member_id=member_id,
                task_id=task_id,
                url=str(args.get("url") or args.get("current_url") or "") or None,
                allow_login_recovery=bool(args.get("allow_login_recovery")),
            )
            return self._merge_browser_page_args(context, args)
        return self._merge_browser_page_args({}, args)

    async def restore_context(
        self,
        *,
        browser_session_id: str,
        task_id: str | None,
        member_id: str | None,
        page_key: str | None,
        current_url: str | None,
        requested_action: str | None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            return {}
        response = await self._browser_sessions.restore_context(
            browser_session_id,
            BrowserSessionRestoreContextRequest(
                task_id=task_id,
                member_id=member_id,
                page_key=page_key,
                current_url=current_url,
                requested_action=requested_action,
            ),
            trace_id=trace_id,
        )
        return response.context

    async def resolve_page_url(
        self,
        *,
        task_id: str | None,
        args: dict[str, Any],
        action: str,
        session_context: dict[str, Any],
    ) -> str:
        direct_url = str(args.get("url") or args.get("current_url") or args.get("expected_url") or "").strip()
        if direct_url:
            session_context.setdefault("current_url", direct_url)
            return direct_url
        if task_id:
            page_state = await self._replay_store.latest_page_state(task_id)
            if page_state is not None:
                evidence_refs = page_state.get("evidence_refs") or []
                session_context.update(
                    {
                        "current_url": page_state.get("current_url"),
                        "page_id": page_state.get("page_id") or session_context.get("page_id"),
                        "last_browser_evidence_id": (evidence_refs[0] or {}).get("id")
                        if evidence_refs
                        else (
                            page_state.get("browser_evidence_id")
                            or session_context.get("last_browser_evidence_id")
                        ),
                    }
                )
                if page_state.get("current_url"):
                    return str(page_state["current_url"])
        context_url = str(session_context.get("current_url") or "").strip()
        if context_url:
            return context_url
        if action in _BROWSER_PAGE_STATE_ACTIONS:
            raise AppError(
                "BROWSER_SESSION_REQUIRED",
                "请先打开页面，或提供 current_url/browser_session_id 后再执行浏览器交互。",
                status_code=409,
                details={
                    "reason_code": "BROWSER_SESSION_REQUIRED",
                    "recoverable": True,
                    "next_step": "先执行 browser.open，或在参数中提供 current_url。",
                    "action": action,
                },
            )
        return ""

    async def ensure_url_allowed(
        self,
        *,
        url: str,
        session_context: dict[str, Any],
        blocked_callback: Any | None = None,
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            if not url.startswith(("http://", "https://")):
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器 URL 被安全策略阻断",
                    status_code=403,
                    details={"reason_codes": ["browser_session_service_unavailable"]},
                )
            return {
                "allowed": True,
                "url": url,
                "reason_codes": ["browser_url_allowed_without_profile"],
            }
        decision = self._browser_sessions.classify_url(url, session_context=session_context)
        payload = decision.as_dict()
        if decision.allowed:
            return payload
        if blocked_callback is not None:
            await blocked_callback(payload)
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "浏览器 URL 被安全策略阻断",
            status_code=403,
            details={
                "reason_codes": decision.reason_codes,
                "blocked_reason": decision.blocked_reason,
                "url": decision.redacted_url,
            },
        )

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "browser_session_runtime",
            "session_reuse": "task_scoped_with_handle_support",
            "restore_context_supported": self._browser_sessions is not None,
            "health_gate": "fail_closed",
            "session_preflight": "browser_sessions.validate_session_context",
        }

    def _asset_action_for_tool(self, tool_name: str) -> str:
        if tool_name == "browser.download":
            return "download"
        if tool_name in {"browser.snapshot", "browser.open", "browser.extract"}:
            return "read"
        return "interact"

    def _merge_browser_page_args(
        self,
        session_context: dict[str, Any],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(session_context)
        for key in ("browser_session_id", "browser_profile_id", "page_id", "current_url"):
            value = args.get(key)
            if value:
                merged[key] = str(value)
        return merged
