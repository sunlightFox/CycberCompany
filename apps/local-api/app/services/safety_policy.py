from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from core_types import RiskLevel

from app.db.repositories.settings_repo import SettingsRepository
from app.services.browser_policy import classify_browser_action_category

PROFILE_STRICT = "strict"
GOVERNANCE_SMOOTH = "smooth"
GOVERNANCE_BALANCED = "balanced"
GOVERNANCE_STRICT = "strict"
PROFILE_BALANCED_PERSONAL = "balanced_personal"
VISIBLE_REDACTION_STRICT = "strict"
VISIBLE_REDACTION_RELAXED = "relaxed"

APPROVAL_CONTROLS = {"approval", "strong_approval"}
_ALWAYS_EXPLICIT_APPROVAL_ACTIONS = {"media.render_edit"}
_SMOOTH_EXPLICIT_APPROVAL_ACTIONS = {
    "account.publish_post",
    "browser.upload",
    "file.delete",
    "host.install_software",
    "host.uninstall_software",
    "media.render_edit",
    "payment.send",
    "wallet.sign",
    "wallet.transfer",
}
_SMOOTH_EXPLICIT_APPROVAL_CATEGORIES = {
    "account_external_post",
    "browser_upload",
    "file_delete",
    "host_install",
    "payment",
}
_SMOOTH_AUTO_APPROVE_ACTIONS = {
    "browser.download",
    "browser.submit",
    "browser.screenshot",
    "browser.vision_snapshot",
    "file.copy",
    "file.move",
    "file.write",
    "project.build",
    "project.clone",
    "project.install_deps",
    "project.run",
    "project.test",
    "runtime.ensure",
}
_SMOOTH_AUTO_APPROVE_CATEGORIES = {
    "browser_download",
    "browser_interact",
    "browser_read",
    "browser_submit",
    "file_write",
    "managed_process",
    "project_clone",
    "project_dependency_install",
    "project_deployment",
}

_DEFAULT_PERSONAL_AUTO_APPROVE_ACTIONS = {
    "browser.screenshot",
    "browser.vision_snapshot",
    "file.write",
    "file.copy",
    "file.move",
    "project.clone",
    "project.build",
    "project.test",
    "runtime.ensure",
    "media.export_artifact",
}
_DEFAULT_PERSONAL_AUTO_APPROVE_CATEGORIES = {
    "browser_read",
    "file_write",
    "project_clone",
}
_DEFAULT_EXPLICIT_APPROVAL_ACTIONS = {
    "browser.download",
    "file.delete",
    "browser.submit",
    "browser.upload",
    "account.publish_post",
    "media.render_edit",
    "host.install_software",
    "host.uninstall_software",
    "project.deployment.run",
    "project.run",
    "project.install_deps",
    "wallet.sign",
    "wallet.transfer",
    "payment.send",
}
_DEFAULT_EXPLICIT_APPROVAL_CATEGORIES = {
    "browser_download",
    "browser_submit",
    "browser_upload",
    "payment",
    "network_write",
    "account_external_post",
    "host_install",
    "managed_process",
    "project_dependency_install",
    "project_deployment",
    "file_delete",
}
_SECRET_RE = re.compile(
    r"(?i)"
    r"(sk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|token|secret|cookie|password|passwd|pwd|private[_-]?key)"
    r"\s*[:=]\s*['\"]?[^'\"\s,;]+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:mnemonic|助记词|私钥|系统密钥)\b)"
)
_TERMINAL_BLOCK_MARKERS = (
    "remove-item",
    " rm ",
    "rm -",
    " del ",
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
    " set-item ",
    " new-item ",
    " move-item ",
    " copy-item ",
    "ren ",
    "rename-item",
    "chmod ",
    "chown ",
    "pip install",
    "npm install",
    "curl ",
    "wget ",
    "invoke-webrequest",
    "invoke-restmethod",
    ">",
    ">>",
)
_TERMINAL_BLOCK_REASON_CODES = {
    "terminal_sensitive_path_denied",
    "terminal_path_traversal_denied",
    "terminal_symlink_escape_denied",
    "terminal_destructive_command_denied",
    "terminal_custom_cwd_denied",
    "sensitive_path_denied",
    "policy_deny_pattern",
}


@dataclass(frozen=True)
class SafetyApprovalPolicy:
    governance_mode: str = GOVERNANCE_STRICT
    approval_profile: str = PROFILE_STRICT
    chat_visible_redaction: str = VISIBLE_REDACTION_STRICT
    approval_policy: dict[str, Any] = field(default_factory=dict)
    require_confirmation: tuple[str, ...] = ()
    deny_paths: tuple[str, ...] = ()
    terminal_policy_profile: str = "task_artifact_sandbox"

    @property
    def is_balanced_personal(self) -> bool:
        return self.approval_profile == PROFILE_BALANCED_PERSONAL

    @property
    def is_smooth(self) -> bool:
        return self.governance_mode == GOVERNANCE_SMOOTH

    @property
    def uses_relaxed_visible_redaction(self) -> bool:
        return self.chat_visible_redaction == VISIBLE_REDACTION_RELAXED

    def should_skip_approval(
        self,
        *,
        action: str,
        risk_level: RiskLevel | str,
        action_category: str | None = None,
        payload: dict[str, Any] | None = None,
        reason_codes: list[str] | tuple[str, ...] | None = None,
        terminal_command_policy: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_balanced_personal and not self.is_smooth:
            return False
        normalized_action = _normalize_key(action)
        normalized_category = _normalize_key(action_category or "")
        if normalized_action in _ALWAYS_EXPLICIT_APPROVAL_ACTIONS:
            return False
        if self.is_smooth:
            if (
                normalized_action in _SMOOTH_EXPLICIT_APPROVAL_ACTIONS
                or normalized_category in _SMOOTH_EXPLICIT_APPROVAL_CATEGORIES
            ):
                return False
            if bool((payload or {}).get("requires_human_approval")):
                return False
            if _contains_sensitive_payload(payload):
                return False
            if normalized_category == "browser_submit" and _contains_payment_payload(payload):
                return False
            if normalized_action == "terminal.run":
                return self._terminal_can_skip_approval(
                    payload=payload or {},
                    reason_codes=reason_codes or (),
                    terminal_command_policy=terminal_command_policy or {},
                )
            return (
                normalized_action in _SMOOTH_AUTO_APPROVE_ACTIONS
                or normalized_category in _SMOOTH_AUTO_APPROVE_CATEGORIES
                or _risk_order(risk_level) <= _risk_order(RiskLevel.R3)
            )
        profile_policy = self._profile_policy()
        require_actions = _string_set(
            profile_policy.get("require_approval_actions"),
            default=_DEFAULT_EXPLICIT_APPROVAL_ACTIONS,
        )
        require_categories = _string_set(
            profile_policy.get("require_approval_categories"),
            default=_DEFAULT_EXPLICIT_APPROVAL_CATEGORIES,
        )
        if normalized_action in require_actions or normalized_category in require_categories:
            return False
        if bool((payload or {}).get("requires_human_approval")):
            return False
        if _contains_sensitive_payload(payload):
            return False
        if normalized_action == "terminal.run":
            return self._terminal_can_skip_approval(
                payload=payload or {},
                reason_codes=reason_codes or (),
                terminal_command_policy=terminal_command_policy or {},
            )
        risk_order = _risk_order(risk_level)
        auto_max = _risk_order(str(profile_policy.get("auto_approve_max_risk") or "R3"))
        if risk_order <= auto_max:
            return True
        auto_actions = _string_set(
            profile_policy.get("auto_approve_actions"),
            default=_DEFAULT_PERSONAL_AUTO_APPROVE_ACTIONS,
        )
        auto_categories = _string_set(
            profile_policy.get("auto_approve_categories"),
            default=_DEFAULT_PERSONAL_AUTO_APPROVE_CATEGORIES,
        )
        return normalized_action in auto_actions or normalized_category in auto_categories

    def without_approval_controls(self, controls: list[str] | tuple[str, ...]) -> list[str]:
        return [control for control in controls if control not in APPROVAL_CONTROLS]

    def _terminal_can_skip_approval(
        self,
        *,
        payload: dict[str, Any],
        reason_codes: list[str] | tuple[str, ...],
        terminal_command_policy: dict[str, Any],
    ) -> bool:
        profile_policy = self._profile_policy()
        if not bool(profile_policy.get("auto_approve_sandboxed_terminal", True)):
            return False
        if any(code in _TERMINAL_BLOCK_REASON_CODES for code in reason_codes):
            return False
        if terminal_command_policy.get("decision") == "deny":
            return False
        if terminal_command_policy.get("reason") == "mutation_requires_approval":
            return False
        command = str(payload.get("command") or "")
        if _terminal_command_has_blocked_marker(command):
            return False
        return (
            terminal_command_policy.get("reason") == "sandboxed_terminal"
            or bool(payload.get("chat_readonly_command"))
        )

    def _profile_policy(self) -> dict[str, Any]:
        profile_specific = _mapping(self.approval_policy.get(self.approval_profile))
        return profile_specific or _mapping(self.approval_policy)


class RuntimeSafetyPolicyService:
    def __init__(
        self,
        *,
        settings_repo: SettingsRepository,
        safety_config: dict[str, Any],
    ) -> None:
        self._settings_repo = settings_repo
        self._safety_config = safety_config

    async def get_policy(
        self,
        *,
        organization_id: str = "org_default",
    ) -> SafetyApprovalPolicy:
        row = await self._settings_repo.get_runtime_settings(organization_id)
        safety = _mapping(row["settings"].get("safety")) if row is not None else {}
        if not safety:
            safety = _default_safety_from_config(self._safety_config)
        return _policy_from_safety_settings(safety)


def classify_action_category(
    *,
    action: str,
    tool_name: str | None = None,
    object_type: str | None = None,
    destination: str | None = None,
) -> str:
    normalized_action = _normalize_key(action)
    normalized_tool = _normalize_key(tool_name or "")
    normalized_object = _normalize_key(object_type or "")
    normalized_destination = _normalize_key(destination or "")
    browser_category = classify_browser_action_category(normalized_tool, {"action": normalized_action, "url": normalized_destination})
    if browser_category is not None:
        return browser_category
    if normalized_tool == "terminal.run" or normalized_action in {"terminal.run", "shell", "command"}:
        return "terminal_command"
    if any(marker in f"{normalized_action} {normalized_tool} {normalized_destination}" for marker in ("publish", "post", "send", "external")):
        return "network_write"
    if normalized_object in {"payment", "wallet"}:
        return "payment"
    if normalized_object == "hardware":
        return "host_install"
    if normalized_tool.startswith("file."):
        if normalized_tool == "file.delete" or "delete" in normalized_action:
            return "file_delete"
        if normalized_tool in {"file.write", "file.copy", "file.move"}:
            return "file_write"
        return "file_read"
    if normalized_tool.startswith("project."):
        if normalized_tool == "project.clone":
            return "project_clone"
        if normalized_tool == "project.install_deps":
            return "project_dependency_install"
        if normalized_tool in {"project.run", "project.stop"}:
            return "managed_process"
        return "project_deployment"
    if normalized_tool.startswith("host."):
        return "host_install" if normalized_tool == "host.install_software" else "host_detect"
    if normalized_tool.startswith("account."):
        return "account_external_post" if normalized_tool == "account.publish_post" else "account_draft"
    return "tool_action"


def _policy_from_safety_settings(safety: dict[str, Any]) -> SafetyApprovalPolicy:
    approval_policy = _mapping(safety.get("approval_policy"))
    governance_mode = str(safety.get("governance_mode") or GOVERNANCE_STRICT)
    if governance_mode not in {GOVERNANCE_SMOOTH, GOVERNANCE_BALANCED, GOVERNANCE_STRICT}:
        governance_mode = GOVERNANCE_STRICT
    approval_profile = str(
        safety.get("approval_profile")
        or safety.get("profile")
        or approval_policy.get("profile")
        or PROFILE_STRICT
    )
    visible_profile = str(
        safety.get("chat_visible_redaction")
        or safety.get("visible_redaction_profile")
        or approval_policy.get("chat_visible_redaction")
        or (
            VISIBLE_REDACTION_RELAXED
            if governance_mode == GOVERNANCE_SMOOTH or approval_profile == PROFILE_BALANCED_PERSONAL
            else VISIBLE_REDACTION_STRICT
        )
    )
    if visible_profile not in {VISIBLE_REDACTION_STRICT, VISIBLE_REDACTION_RELAXED}:
        visible_profile = VISIBLE_REDACTION_STRICT
    if approval_profile not in {PROFILE_STRICT, PROFILE_BALANCED_PERSONAL}:
        approval_profile = PROFILE_STRICT
    return SafetyApprovalPolicy(
        governance_mode=governance_mode,
        approval_profile=approval_profile,
        chat_visible_redaction=visible_profile,
        approval_policy=approval_policy,
        require_confirmation=tuple(str(item) for item in safety.get("require_confirmation") or []),
        deny_paths=tuple(str(item) for item in safety.get("deny_paths") or []),
        terminal_policy_profile=str(
            safety.get("terminal_policy_profile") or "task_artifact_sandbox"
        ),
    )


def _default_safety_from_config(config: dict[str, Any]) -> dict[str, Any]:
    risk = _mapping(config.get("risk"))
    sandbox = _mapping(risk.get("sandbox"))
    return {
        "require_confirmation": list(risk.get("require_confirmation") or []),
        "deny_paths": list(risk.get("deny_paths") or []),
        "terminal_policy_profile": str(
            sandbox.get("terminal_policy_profile") or "task_artifact_sandbox"
        ),
        "approval_policy": _mapping(risk.get("approval_policy")),
        "governance_mode": str(risk.get("governance_mode") or GOVERNANCE_STRICT),
        "approval_profile": str(risk.get("approval_profile") or PROFILE_STRICT),
        "chat_visible_redaction": str(
            risk.get("chat_visible_redaction")
            or (
                VISIBLE_REDACTION_RELAXED
                if str(risk.get("governance_mode") or "") == GOVERNANCE_SMOOTH
                else VISIBLE_REDACTION_STRICT
            )
        ),
    }


def _contains_sensitive_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        text = str(payload)
    return bool(_SECRET_RE.search(text))


def _contains_payment_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str).lower()
    except TypeError:
        text = str(payload).lower()
    return any(marker in text for marker in ("payment", "pay", "checkout", "wallet", "card", "credit"))


def _terminal_command_has_blocked_marker(command: str) -> bool:
    wrapped = f" {command.lower()} "
    return any(marker in wrapped for marker in _TERMINAL_BLOCK_MARKERS)


def _string_set(value: Any, *, default: set[str]) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {_normalize_key(item) for item in value}
    if isinstance(value, str):
        return {_normalize_key(value)}
    return {_normalize_key(item) for item in default}


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _risk_order(value: RiskLevel | str) -> int:
    raw = value.value if isinstance(value, RiskLevel) else str(value)
    try:
        return int(raw.upper().removeprefix("R"))
    except ValueError:
        return 1
