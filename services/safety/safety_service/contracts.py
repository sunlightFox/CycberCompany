from __future__ import annotations

import re
from typing import Any

from core_types import ApiModel, RiskLevel, SafetyDecision
from pydantic import Field


class ActionRequest(ApiModel):
    actor_id: str
    actor_type: str = "member"
    organization_id: str = "org_default"
    task_id: str | None = None
    action_type: str = "generic"
    action: str
    object_type: str
    object_id: str | None = None
    tool_name: str | None = None
    skill_id: str | None = None
    mcp_server_id: str | None = None
    mcp_tool_id: str | None = None
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    asset_handles: list[str] = Field(default_factory=list)
    destination: str | None = None
    risk_hints: list[str] = Field(default_factory=list)
    untrusted_refs: list[dict[str, Any]] = Field(default_factory=list)


class PrivacyClassification(ApiModel):
    privacy_level: str
    sensitivity_hits: list[str] = Field(default_factory=list)
    allow_cloud: bool
    redacted_text: str


class SafetyService:
    SECRET_PATTERNS = {
        "api_key": re.compile(
            r"sk-[A-Za-z0-9_-]{12,}|(?i:api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+"
        ),
        "token": re.compile(r"(?i:token)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        "password": re.compile(r"(?i:password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        "cookie": re.compile(r"(?i:cookie)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        "private_key": re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
            r"|(?i:private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+",
            re.S,
        ),
        "mnemonic": re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", re.I),
        "local_path": re.compile(
            r"\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s,;]+)*|/(?:Users|home)/[^\s,;]+"
        ),
    }

    async def evaluate_action(self, request: ActionRequest) -> SafetyDecision:
        text = _text_for_request(request)
        privacy = self.classify_chat_input(text)
        risk = _risk_from_request(request)
        checks = [
            "actor",
            "capability_boundary",
            "dlp",
            "prompt_injection",
            "outbound_policy",
            "risk_policy",
        ]
        redactions = privacy.sensitivity_hits
        required_controls: list[str] = []
        policy_sources = ["safety.default_risk_matrix"]
        reason = "allowed_by_policy"
        decision = "allow"
        allowed = True
        approval_required = False

        if _looks_like_prompt_injection(text):
            risk = _max_risk(risk, RiskLevel.R4)
            required_controls.append("treat_untrusted_content_as_data")
            policy_sources.append("safety.prompt_injection")

        if privacy.sensitivity_hits:
            if not _local_readonly_browser_evidence(request):
                risk = _max_risk(risk, RiskLevel.R5)
            required_controls.append("dlp_redaction")
            policy_sources.append("safety.dlp")
            if _is_external_or_exfiltration(request):
                decision = "deny"
                allowed = False
                reason = "sensitive_payload_blocked"

        if _terminal_danger(request):
            risk = RiskLevel.R7
            decision = "deny"
            allowed = False
            reason = "dangerous_terminal_command"
            policy_sources.append("safety.terminal_policy")

        if _hard_deny_action(request):
            risk = RiskLevel.R7
            decision = "deny"
            allowed = False
            reason = "r6_r7_action_denied"
            policy_sources.append("safety.hard_deny")

        if allowed and _risk_order(risk) >= _risk_order(RiskLevel.R6):
            decision = "deny"
            allowed = False
            reason = "r6_r7_action_denied"
            policy_sources.append("safety.hard_deny")

        if allowed and _risk_order(risk) >= _risk_order(RiskLevel.R3):
            decision = "approval_required"
            approval_required = True
            reason = "risk_requires_approval"
            required_controls.append(
                "strong_approval" if _risk_order(risk) >= _risk_order(RiskLevel.R5)
                else "approval"
            )
            policy_sources.append("safety.approval_policy")

        return SafetyDecision(
            decision=decision,
            allowed=allowed,
            risk_level=risk,
            approval_required=approval_required,
            reason=reason,
            checks=checks,
            redactions=redactions,
            required_controls=sorted(set(required_controls)),
            policy_sources=sorted(set(policy_sources)),
            trace_refs=[],
            payload_summary=request.payload_summary,
        )

    def classify_chat_input(self, text: str) -> PrivacyClassification:
        hits = [
            name
            for name, pattern in self.SECRET_PATTERNS.items()
            if pattern.search(text)
        ]
        privacy_level = "high" if hits else "medium"
        redacted = text
        for name, pattern in self.SECRET_PATTERNS.items():
            replacement = f"[REDACTED_{name.upper()}]"
            redacted = pattern.sub(replacement, redacted)
        return PrivacyClassification(
            privacy_level=privacy_level,
            sensitivity_hits=hits,
            allow_cloud=not hits,
            redacted_text=redacted,
        )


def _text_for_request(request: ActionRequest) -> str:
    return " ".join(
        str(part)
        for part in (
            request.action,
            request.object_type,
            request.object_id or "",
            request.tool_name or "",
            request.destination or "",
            request.payload_summary,
            request.payload,
            request.risk_hints,
            request.untrusted_refs,
        )
    )


def _risk_from_request(request: ActionRequest) -> RiskLevel:
    candidates = []
    for hint in request.risk_hints:
        value = str(hint).upper()
        if value in RiskLevel.__members__:
            candidates.append(RiskLevel[value])
        elif value in {risk.value for risk in RiskLevel}:
            candidates.append(RiskLevel(value))
    action = request.action.lower()
    tool_name = (request.tool_name or "").lower()
    object_type = request.object_type.lower()
    if any(word in action or word in tool_name for word in ("delete", "remove", "move")):
        delete_like = "delete" in action or "delete" in tool_name
        candidates.append(RiskLevel.R5 if delete_like else RiskLevel.R3)
    if tool_name == "terminal.run" or action in {"terminal.run", "shell", "command"}:
        candidates.append(RiskLevel.R5)
    if tool_name.startswith("browser.") and any(
        word in action or word in tool_name for word in ("download", "screenshot")
    ):
        candidates.append(RiskLevel.R3)
    if object_type in {"wallet", "payment", "hardware"}:
        candidates.append(RiskLevel.R5)
    if any(word in action for word in ("publish", "post", "send", "upload", "external")):
        candidates.append(RiskLevel.R4)
    risk = RiskLevel.R1
    for candidate in candidates:
        risk = _max_risk(risk, candidate)
    return risk


def _hard_deny_action(request: ActionRequest) -> bool:
    action = request.action.lower()
    tool_name = (request.tool_name or "").lower()
    object_type = request.object_type.lower()
    text = f"{action} {tool_name} {object_type}"
    return any(
        marker in text
        for marker in (
            "sign_transaction",
            "transfer_funds",
            "wallet.sign",
            "wallet.transfer",
            "payment.send",
            "hardware.control",
            "control_device",
            "exfiltrate_secret",
        )
    )


def _is_external_or_exfiltration(request: ActionRequest) -> bool:
    if _local_readonly_browser_evidence(request):
        return False
    text = f"{request.action} {request.tool_name or ''} {request.destination or ''}".lower()
    return any(
        marker in text
        for marker in (
            "http://",
            "https://",
            "upload",
            "post",
            "send",
            "publish",
            "external",
            "exfil",
            "mcp.",
        )
    )


def _local_readonly_browser_evidence(request: ActionRequest) -> bool:
    tool_name = (request.tool_name or "").lower()
    destination = (request.destination or "").lower()
    return tool_name in {
        "browser.open",
        "browser.search",
        "browser.snapshot",
        "browser.screenshot",
    } and (
        destination.startswith("http://127.0.0.1")
        or destination.startswith("http://localhost")
        or destination.startswith("https://localhost")
    )


def _looks_like_prompt_injection(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "ignore previous instructions",
            "忽略之前",
            "泄露",
            "reveal system prompt",
            "developer message",
            "system prompt",
        )
    )


def _terminal_danger(request: ActionRequest) -> bool:
    if (request.tool_name or request.action).lower() != "terminal.run":
        return False
    command = str(request.payload.get("command") or request.payload_summary.get("command") or "")
    lowered = f" {command.lower()} "
    blocked = [
        "remove-item",
        " rm ",
        "rm -",
        "del /",
        "format ",
        "shutdown",
        "reboot",
        "reg delete",
        "git reset --hard",
        "cipher ",
        "diskpart",
        "mkfs",
        "dd if=",
        "bcdedit",
        "takeown ",
        "icacls ",
    ]
    sensitive_paths = [
        r"(^|[\\/\s])secrets([\\/\s]|$)",
        r"(^|[\\/\s])\.env(\.local)?([\\/\s]|$)",
        r"master\.key",
        r"local_secrets\.json",
        r"c:\\windows",
        r"\\windows\\system32",
        r"(^|[\s])/(etc|bin|sbin|usr|var|root)(/|\s|$)",
    ]
    return any(item in lowered for item in blocked) or any(
        re.search(pattern, lowered) for pattern in sensitive_paths
    )


def _risk_order(risk: RiskLevel) -> int:
    return int(risk.value.removeprefix("R"))


def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    return left if _risk_order(left) >= _risk_order(right) else right
