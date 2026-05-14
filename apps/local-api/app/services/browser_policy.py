from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import RiskLevel


@dataclass(frozen=True)
class BrowserActionPolicySnapshot:
    action: str
    category: str
    default_risk_level: RiskLevel
    required_controls: tuple[str, ...]
    auto_approve_eligible: bool
    workflow_bypass_allowed: bool
    backend_capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "category": self.category,
            "risk_level": self.default_risk_level.value,
            "required_controls": list(self.required_controls),
            "auto_approve_eligible": self.auto_approve_eligible,
            "workflow_bypass_allowed": self.workflow_bypass_allowed,
            "backend_capabilities": list(self.backend_capabilities),
        }


_BROWSER_ACTION_MATRIX: dict[str, BrowserActionPolicySnapshot] = {
    "open": BrowserActionPolicySnapshot(
        action="open",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "search": BrowserActionPolicySnapshot(
        action="search",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "snapshot": BrowserActionPolicySnapshot(
        action="snapshot",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "extract": BrowserActionPolicySnapshot(
        action="extract",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "wait": BrowserActionPolicySnapshot(
        action="wait",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "console": BrowserActionPolicySnapshot(
        action="console",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "network_summary": BrowserActionPolicySnapshot(
        action="network_summary",
        category="browser_read",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "screenshot": BrowserActionPolicySnapshot(
        action="screenshot",
        category="browser_read",
        default_risk_level=RiskLevel.R3,
        required_controls=("approval",),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "vision_snapshot": BrowserActionPolicySnapshot(
        action="vision_snapshot",
        category="browser_read",
        default_risk_level=RiskLevel.R3,
        required_controls=("approval",),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("dom_snapshot",),
    ),
    "fill": BrowserActionPolicySnapshot(
        action="fill",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "type": BrowserActionPolicySnapshot(
        action="type",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "select": BrowserActionPolicySnapshot(
        action="select",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "check": BrowserActionPolicySnapshot(
        action="check",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "click": BrowserActionPolicySnapshot(
        action="click",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "dialog": BrowserActionPolicySnapshot(
        action="dialog",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "tabs": BrowserActionPolicySnapshot(
        action="tabs",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("cross_tab",),
    ),
    "frame_action": BrowserActionPolicySnapshot(
        action="frame_action",
        category="browser_interact",
        default_risk_level=RiskLevel.R2,
        required_controls=(),
        auto_approve_eligible=True,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "submit": BrowserActionPolicySnapshot(
        action="submit",
        category="browser_submit",
        default_risk_level=RiskLevel.R5,
        required_controls=("approval", "strong_approval"),
        auto_approve_eligible=False,
        workflow_bypass_allowed=False,
        backend_capabilities=("interactive_fill",),
    ),
    "download": BrowserActionPolicySnapshot(
        action="download",
        category="browser_download",
        default_risk_level=RiskLevel.R3,
        required_controls=("approval",),
        auto_approve_eligible=False,
        workflow_bypass_allowed=True,
        backend_capabilities=("file_download",),
    ),
    "upload": BrowserActionPolicySnapshot(
        action="upload",
        category="browser_upload",
        default_risk_level=RiskLevel.R5,
        required_controls=("approval", "strong_approval"),
        auto_approve_eligible=False,
        workflow_bypass_allowed=False,
        backend_capabilities=("file_upload",),
    ),
}

_BROWSER_BACKEND_CAPABILITIES: dict[str, dict[str, bool]] = {
    "playwright": {
        "dom_snapshot": True,
        "interactive_fill": True,
        "file_download": True,
        "file_upload": True,
        "cross_tab": True,
        "challenge_recovery": True,
        "persistent_identity": False,
    },
    "playwright_ephemeral": {
        "dom_snapshot": True,
        "interactive_fill": True,
        "file_download": True,
        "file_upload": True,
        "cross_tab": True,
        "challenge_recovery": True,
        "persistent_identity": False,
    },
    "local_cdp": {
        "dom_snapshot": True,
        "interactive_fill": True,
        "file_download": True,
        "file_upload": True,
        "cross_tab": True,
        "challenge_recovery": True,
        "persistent_identity": True,
    },
    "remote_cdp": {
        "dom_snapshot": True,
        "interactive_fill": True,
        "file_download": True,
        "file_upload": True,
        "cross_tab": True,
        "challenge_recovery": True,
        "persistent_identity": True,
    },
    "http_fallback": {
        "dom_snapshot": True,
        "interactive_fill": True,
        "file_download": True,
        "file_upload": False,
        "cross_tab": False,
        "challenge_recovery": False,
        "persistent_identity": False,
    },
    "remote_cdp_unavailable": {
        "dom_snapshot": False,
        "interactive_fill": False,
        "file_download": False,
        "file_upload": False,
        "cross_tab": False,
        "challenge_recovery": False,
        "persistent_identity": False,
    },
}

_SESSION_STATE_PRIORITY = (
    "revoked",
    "cleared",
    "expired",
    "challenge_detected",
    "login_required",
    "recovery_required",
    "degraded",
    "active",
)


def browser_action_policy(tool_name: str, args: dict[str, Any] | None = None) -> BrowserActionPolicySnapshot:
    args = args or {}
    action = tool_name.removeprefix("browser.")
    policy = _BROWSER_ACTION_MATRIX.get(action)
    if policy is None:
        return BrowserActionPolicySnapshot(
            action=action,
            category="browser_read",
            default_risk_level=RiskLevel.R2,
            required_controls=(),
            auto_approve_eligible=True,
            workflow_bypass_allowed=False,
            backend_capabilities=("dom_snapshot",),
        )
    if action == "download" and args.get("workflow_low_risk_download"):
        return BrowserActionPolicySnapshot(
            action=policy.action,
            category=policy.category,
            default_risk_level=RiskLevel.R2,
            required_controls=(),
            auto_approve_eligible=True,
            workflow_bypass_allowed=True,
            backend_capabilities=policy.backend_capabilities,
        )
    return policy


def classify_browser_action_category(tool_name: str, args: dict[str, Any] | None = None) -> str | None:
    if not tool_name.startswith("browser."):
        return None
    policy = browser_action_policy(tool_name, args=args)
    return policy.category


def browser_backend_capabilities(backend: str | None) -> dict[str, bool]:
    normalized = str(backend or "").strip().lower()
    if normalized in _BROWSER_BACKEND_CAPABILITIES:
        return dict(_BROWSER_BACKEND_CAPABILITIES[normalized])
    if normalized == "auto":
        return dict(_BROWSER_BACKEND_CAPABILITIES["playwright"])
    return dict(_BROWSER_BACKEND_CAPABILITIES["http_fallback"])


def browser_backend_for_session_context(session_context: dict[str, Any] | None) -> str:
    context = session_context or {}
    for key in ("provider_mode", "execution_backend"):
        value = str(context.get(key) or "").strip().lower()
        if value in _BROWSER_BACKEND_CAPABILITIES:
            return value
        if value == "playwright_ephemeral":
            return "playwright_ephemeral"
        if value == "auto":
            return "playwright"
    return "playwright_ephemeral"


def browser_session_state(
    *,
    session_status: str | None,
    health_status: str | None,
    login_state: str | None,
) -> str:
    candidates = {
        _normalize_session_state(session_status),
        _normalize_session_state(health_status),
        _normalize_session_state(login_state),
    }
    for state in _SESSION_STATE_PRIORITY:
        if state in candidates:
            return state
    return "active"


def browser_session_preflight(
    *,
    session_status: str | None,
    health_status: str | None,
    login_state: str | None,
    execution_backend: str | None,
    identity_binding_status: str | None,
    login_capture_mode: str | None,
) -> dict[str, Any]:
    session_state = browser_session_state(
        session_status=session_status,
        health_status=health_status,
        login_state=login_state,
    )
    return {
        "session_state": session_state,
        "backend_capabilities": browser_backend_capabilities(execution_backend),
        "identity_binding_status": str(identity_binding_status or "unbound"),
        "recovery_allowed": session_state in {"login_required", "challenge_detected", "recovery_required"},
        "login_reuse_allowed": session_state == "active",
        "login_capture_mode": str(login_capture_mode or "manual_handoff"),
    }


def browser_execution_summary(
    *,
    session_context: dict[str, Any],
    action_status: str,
    degraded_reason: str | None,
    challenge_reason_code: str | None = None,
    verification_evidence: dict[str, Any] | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    verification = verification_evidence or {}
    session_state = str(session_context.get("session_state") or "active")
    preflight_outcome = "ready" if session_state == "active" else session_state
    verification_outcome = "not_applicable"
    if verification:
        verification_outcome = str(
            verification.get("status")
            or ("confirmed" if verification.get("present") else "missing")
        )
    return {
        "preflight_outcome": preflight_outcome,
        "step_outcome_counts": {action_status: 1},
        "verification_outcome": verification_outcome,
        "next_step": next_step,
        "human_intervention_required": action_status in {"challenge_detected", "awaiting_human"},
        "challenge_reason_code": challenge_reason_code,
        "degraded_reason": degraded_reason,
    }


def _normalize_session_state(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"active", "ready", "authenticated", "healthy"}:
        return "active"
    if normalized in {"login_required"}:
        return "login_required"
    if normalized in {"challenge_detected"}:
        return "challenge_detected"
    if normalized in {"recovery_required"}:
        return "recovery_required"
    if normalized in {"degraded", "identity_unavailable"}:
        return "degraded"
    if normalized in {"unknown", ""}:
        return ""
    if normalized in {"expired", "session_expired"}:
        return "expired"
    if normalized in {"revoked"}:
        return "revoked"
    if normalized in {"cleared"}:
        return "cleared"
    return normalized
