from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    IntentType,
    ModelParams,
    ModelRouteDecision,
    PrivacyLevel,
    TaskMode,
)
from pydantic import Field

from brain.adapters.token_estimator import estimate_text_tokens


class BrainRouteRequest(ApiModel):
    text: str
    member_id: EntityId | None = None
    conversation_id: EntityId | None = None
    default_brain_id: EntityId | None = None
    privacy_level: PrivacyLevel | str = PrivacyLevel.MEDIUM
    estimated_input_tokens: int | None = None
    available_brains: list[dict[str, Any]] = Field(default_factory=list)
    model_routing_config: dict[str, Any] = Field(default_factory=dict)
    requires_tool_calling: bool = False
    requires_vision: bool = False


class BrainRouteDecision(ApiModel):
    intent: str
    mode: TaskMode
    model_route: ModelRouteDecision | None = None
    reason: str
    reason_codes: list[str] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    needs_tool: bool = False


class ModelRouteSelection(ApiModel):
    route: ModelRouteDecision | None = None
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)


class IntentClassifier:
    def classify(self, text: str) -> tuple[str, list[str], bool]:
        lower = text.lower()
        if any(keyword in lower for keyword in ("总结", "summary", "summarize")):
            return IntentType.SUMMARIZATION.value, ["summary_request"], False
        if any(keyword in lower for keyword in ("写", "draft", "创作", "生成一篇")):
            return IntentType.CREATIVE_WRITING.value, ["creative_request"], False
        if any(keyword in lower for keyword in ("记得", "之前", "memory", "偏好")):
            return IntentType.MEMORY_QUERY.value, ["memory_visible_scope"], False
        if any(
            keyword in lower
            for keyword in (
                "不要执行",
                "不执行",
                "别执行",
                "只分析",
                "只要方案",
                "只给方案",
                "只生成方案",
                "先给方案",
                "先写方案",
                "生成草稿",
                "只写草稿",
            )
        ):
            return IntentType.QUESTION_ANSWER.value, ["safe_plan_only"], False
        if any(
            keyword in lower
            for keyword in (
                "打开",
                "运行",
                "执行",
                "发送",
                "登录",
                "浏览器",
                "文件夹",
                "shell",
                "删除",
                "清空",
                "覆盖",
                "移动",
                "整理",
                "发帖",
                "发布",
                "购买",
                "下单",
                "转账",
                "支付",
                "签名",
            )
        ):
            return IntentType.TASK_EXECUTION.value, ["tool_required"], True
        if any(keyword in lower for keyword in ("配置", "设置", "模型", "大脑")):
            return IntentType.SYSTEM_SETTINGS.value, ["settings_intent"], False
        if (
            text.endswith("?")
            or text.endswith("？")
            or any(k in lower for k in ("什么", "为什么", "how", "why"))
        ):
            return IntentType.QUESTION_ANSWER.value, ["question"], False
        return IntentType.CHAT.value, ["daily_chat"], False


class ModeSelector:
    def select(self, intent: str, *, needs_tool: bool) -> tuple[TaskMode, list[str]]:
        if needs_tool:
            return TaskMode.WORKFLOW, ["tool_intent_requires_task_runtime"]
        if intent == "memory_query":
            return TaskMode.DIRECT_WITH_MEMORY, ["memory_contract_only"]
        if intent in {"task_execution", "system_settings"}:
            return TaskMode.WORKFLOW, ["non_direct_intent"]
        return TaskMode.DIRECT, ["direct_answer_supported"]


class ModelRouter:
    def select_route(self, request: BrainRouteRequest) -> ModelRouteDecision | None:
        return self.select_route_result(request).route

    def select_route_result(self, request: BrainRouteRequest) -> ModelRouteSelection:
        estimated_input = request.estimated_input_tokens or estimate_text_tokens(request.text)
        reserved_output = _routing_int(request.model_routing_config, "reserved_output_tokens", 1024)
        privacy_level = (
            request.privacy_level.value
            if isinstance(request.privacy_level, PrivacyLevel)
            else request.privacy_level
        )
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for brain in request.available_brains:
            usable, reason = _usable_reason(brain)
            if usable:
                candidates.append(brain)
            else:
                rejected.append({"brain_id": brain.get("brain_id"), "reason": reason})

        if privacy_level == "high":
            next_candidates = []
            for brain in candidates:
                if bool(brain.get("is_local")):
                    next_candidates.append(brain)
                else:
                    rejected.append({"brain_id": brain.get("brain_id"), "reason": "privacy_high"})
            candidates = next_candidates
        elif not _allow_cloud(request.model_routing_config, privacy_level):
            next_candidates = []
            for brain in candidates:
                if bool(brain.get("is_local")):
                    next_candidates.append(brain)
                else:
                    rejected.append(
                        {"brain_id": brain.get("brain_id"), "reason": "cloud_policy_denied"}
                    )
            candidates = next_candidates

        if request.requires_tool_calling:
            next_candidates = []
            for brain in candidates:
                if bool(brain.get("supports_tools")):
                    next_candidates.append(brain)
                else:
                    rejected.append(
                        {"brain_id": brain.get("brain_id"), "reason": "tools_not_supported"}
                    )
            candidates = next_candidates
        if request.requires_vision:
            next_candidates = []
            for brain in candidates:
                if bool(brain.get("supports_vision")):
                    next_candidates.append(brain)
                else:
                    rejected.append(
                        {"brain_id": brain.get("brain_id"), "reason": "vision_not_supported"}
                    )
            candidates = next_candidates

        next_candidates = []
        for brain in candidates:
            if int(brain.get("context_window") or 8192) >= estimated_input + reserved_output:
                next_candidates.append(brain)
            else:
                rejected.append({"brain_id": brain.get("brain_id"), "reason": "context_too_small"})
        candidates = next_candidates
        if not candidates:
            return ModelRouteSelection(route=None, rejected_candidates=rejected)

        primary = _prefer_default(candidates, request.default_brain_id)
        fallbacks = [
            brain["brain_id"]
            for brain in candidates
            if brain["brain_id"] != primary["brain_id"] and bool(brain.get("allow_fallback", True))
        ]
        return ModelRouteSelection(
            route=ModelRouteDecision(
                route_id=f"route_{primary['brain_id']}",
                primary_brain_id=primary["brain_id"],
                fallback_brain_ids=fallbacks,
                reason_codes=["privacy_checked", "healthy_model", "context_window_ok"],
                privacy_level=privacy_level,
                privacy_policy=(
                    "local_only"
                    if privacy_level == "high"
                    else "allow_cloud_without_secrets"
                ),
                context_budget={
                    "max_input_tokens": int(primary.get("context_window") or 8192)
                    - reserved_output,
                    "reserved_output_tokens": reserved_output,
                },
                model_params=ModelParams(
                    temperature=float(primary.get("default_temperature") or 0.3),
                    top_p=float(primary.get("default_top_p") or 0.9),
                    max_output_tokens=int(
                        primary.get("default_max_output_tokens") or reserved_output
                    ),
                    timeout_seconds=int(primary.get("timeout_seconds") or 180),
                    retry_count=int(primary.get("retry_count") or 1),
                ),
                rejected_candidates=rejected,
                metadata={
                    "provider": primary.get("provider"),
                    "is_local": bool(primary.get("is_local")),
                },
            ),
            rejected_candidates=rejected,
        )


class BrainRouter:
    def __init__(self) -> None:
        self._intent = IntentClassifier()
        self._mode = ModeSelector()
        self._router = ModelRouter()

    async def route(self, request: BrainRouteRequest) -> BrainRouteDecision:
        intent, reason_codes, needs_tool = self._intent.classify(request.text)
        mode, mode_reasons = self._mode.select(intent, needs_tool=needs_tool)
        model_route = None
        rejected_candidates: list[dict[str, Any]] = []
        if mode in {TaskMode.DIRECT, TaskMode.DIRECT_WITH_MEMORY} and not needs_tool:
            route_selection = self._router.select_route_result(request)
            model_route = route_selection.route
            rejected_candidates = route_selection.rejected_candidates
        return BrainRouteDecision(
            intent=intent,
            mode=mode,
            model_route=model_route,
            reason=";".join(reason_codes + mode_reasons),
            reason_codes=reason_codes + mode_reasons,
            rejected_candidates=rejected_candidates,
            needs_tool=needs_tool,
        )


def _is_usable(brain: dict[str, Any]) -> bool:
    usable, _ = _usable_reason(brain)
    return usable


def _usable_reason(brain: dict[str, Any]) -> tuple[bool, str]:
    status = str(brain.get("status") or "")
    if status in {"disabled", "unhealthy", "not_configured", "needs_configuration"}:
        return False, status or "not_configured"
    if not brain.get("endpoint") or not brain.get("model_name"):
        return False, "missing_endpoint_or_model"
    if not bool(brain.get("is_local")) and not bool(brain.get("allow_cloud")):
        return False, "cloud_not_allowed"
    if status not in {"configured", "healthy"}:
        return False, status or "not_configured"
    return True, "usable"


def _prefer_default(
    candidates: list[dict[str, Any]],
    default_brain_id: EntityId | None,
) -> dict[str, Any]:
    if default_brain_id:
        for brain in candidates:
            if brain["brain_id"] == default_brain_id:
                return brain
    local_candidates = [brain for brain in candidates if bool(brain.get("is_local"))]
    return (local_candidates or candidates)[0]


def _allow_cloud(config: dict[str, Any], privacy_level: str) -> bool:
    try:
        return bool(config["routing"]["privacy"][privacy_level].get("allow_cloud", False))
    except (KeyError, AttributeError):
        return privacy_level != "high"


def _routing_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config["routing"].get(key, default))
    except (KeyError, TypeError, ValueError):
        return default
