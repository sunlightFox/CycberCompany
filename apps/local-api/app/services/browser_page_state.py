from __future__ import annotations

import hashlib
import re
from typing import Any

from trace_service import redact

CHALLENGE_MARKERS = (
    "captcha",
    "验证码",
    "二次验证",
    "risk check",
    "风控",
    "人机验证",
    "安全验证",
    "verify you are human",
)

LOGIN_MARKERS = ("登录", "请登录", "sign in", "log in", "password", "账号")
ACTION_MARKERS = ("提交", "保存", "确认", "发布", "发送", "submit", "save", "confirm")
DOWNLOAD_MARKERS = ("下载", "download", ".csv", ".xlsx", ".pdf", "报表")


class BrowserPageStateRuntime:
    def classify_status(
        self,
        *,
        action: str,
        result: dict[str, Any],
        session_context: dict[str, Any],
    ) -> str:
        action_status = str(result.get("action_status") or "")
        text = self._snapshot_text(result)
        lowered = text.lower()
        health_status = str(session_context.get("health_status") or "")
        login_state = str(session_context.get("login_state") or "")
        if any(marker.lower() in lowered for marker in CHALLENGE_MARKERS):
            return "challenge_detected"
        if health_status == "login_required" or login_state == "login_required":
            return "login_required"
        if any(marker.lower() in lowered for marker in LOGIN_MARKERS):
            return "login_required"
        if action_status in {"awaiting_approval", "approval_required"}:
            return "approval_required"
        if action_status in {"failed", "blocked", "http_error"}:
            return "failed"
        if action in {"click", "submit", "upload", "download"}:
            return "completed" if action_status == "completed" else "actionable"
        if self._primary_forms(text) or self._primary_actions(text):
            return "actionable"
        return "observed"

    def summarize_page(self, result: dict[str, Any]) -> str | None:
        text = self._snapshot_text(result)
        if not text:
            return None
        compact = re.sub(r"\s+", " ", text).strip()
        if not compact:
            return None
        return str(redact(compact))[:400]

    def build_page_state(
        self,
        *,
        action: str,
        result: dict[str, Any],
        session_context: dict[str, Any],
        url_source: str,
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        text = self._snapshot_text(result)
        status = self.classify_status(action=action, result=result, session_context=session_context)
        current_url = str(
            redact(
                result.get("url")
                or result.get("browser_page_state", {}).get("current_url")
                or session_context.get("current_url")
                or ""
            )
        ) or None
        return {
            "page_state_id": result.get("browser_page_state", {}).get("page_state_id"),
            "page_key": result.get("browser_page_state", {}).get("page_key"),
            "current_url": current_url,
            "page_title": str(redact(result.get("title") or "")) or None,
            "primary_forms": self._primary_forms(text),
            "primary_actions": self._primary_actions(text),
            "login_required": status == "login_required",
            "challenge_markers": self._challenge_markers(text),
            "download_candidates": self._download_candidates(text),
            "safe_summary": self.summarize_page(result),
            "status": status,
            "url_source": url_source,
            "evidence_refs": evidence_refs,
            "browser_session_id": session_context.get("browser_session_id"),
            "browser_profile_id": session_context.get("browser_profile_id"),
            "page_id": session_context.get("page_id") or result.get("page_id"),
            "last_browser_evidence_id": session_context.get("last_browser_evidence_id"),
            "recoverable": status in {"observed", "actionable", "approval_required"},
        }

    def dom_summary(self, result: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot_text(result)
        payload = {
            "has_snapshot": bool(snapshot),
            "snapshot_preview": str(redact(snapshot))[:500] if snapshot else None,
            "snapshot_hash": (
                "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
                if snapshot
                else None
            ),
        }
        if result.get("selector"):
            payload["selector"] = str(redact(str(result["selector"])))
        if result.get("interaction"):
            payload["interaction"] = redact(result["interaction"])
        return payload

    def _snapshot_text(self, result: dict[str, Any]) -> str:
        return str(result.get("snapshot") or result.get("content_preview") or "")

    def _challenge_markers(self, text: str) -> list[str]:
        lowered = text.lower()
        return [marker for marker in CHALLENGE_MARKERS if marker.lower() in lowered][:5]

    def _download_candidates(self, text: str) -> list[str]:
        lowered = text.lower()
        return [marker for marker in DOWNLOAD_MARKERS if marker.lower() in lowered][:5]

    def _primary_forms(self, text: str) -> list[str]:
        lowered = text.lower()
        matches: list[str] = []
        for marker in ("form", "input", "textarea", "password", "email", "搜索", "登录"):
            if marker in lowered and marker not in matches:
                matches.append(marker)
        return matches[:6]

    def _primary_actions(self, text: str) -> list[str]:
        lowered = text.lower()
        matches: list[str] = []
        for marker in ACTION_MARKERS:
            if marker.lower() in lowered and marker not in matches:
                matches.append(marker)
        return matches[:6]
