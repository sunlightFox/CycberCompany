from __future__ import annotations

from typing import Any

from core_types import ResponsePlan
from trace_service import redact

from app.schemas.chat_quality_shadow import (
    ActionDialogueMappingShadow,
    ChatDialogueStateShadow,
    ChatQualityShadowEvaluation,
    ConversationUnderstandingShadow,
    ResponsePolicyShadow,
    ShadowPolicyAdvisoryGate,
    ShadowPolicyComparison,
)
from app.services.action_dialogue_mapper_shadow import ActionDialogueMapperShadowService
from app.services.conversation_understanding import ConversationUnderstandingService
from app.services.dialogue_state_shadow import ChatDialogueStateShadowService
from app.services.response_policy_shadow import ResponsePolicyShadowService

CHAT_QUALITY_SHADOW_VERSION = "chat_quality_shadow.openclaw_hermes.v1"
_POLICY_DIFF_FIELDS = (
    "opening_style",
    "depth_mode",
    "followthrough_mode",
    "continuation_expectation",
)


class ShadowPolicyAdvisoryGateService:
    def evaluate(
        self,
        *,
        understanding: ConversationUnderstandingShadow,
        response_plan: ResponsePlan,
        privacy_level: str | None,
        shadow_meta: dict[str, Any],
        turn_status: str | None,
        clarification_decision: dict[str, Any] | None,
    ) -> ShadowPolicyAdvisoryGate:
        tags: list[str] = []
        scene = understanding.primary_scene
        if understanding.action_request:
            tags.append("action_request_excluded")
        if understanding.memory_related:
            tags.append("memory_related_excluded")
        if understanding.latest_instruction_override:
            tags.append("latest_instruction_override_excluded")
        if scene not in {"casual_chat", "multi_turn_followup"}:
            tags.append(f"{scene}_excluded")
        if privacy_level not in {"low", "medium", None, ""}:
            tags.append("privacy_high_excluded")
        if clarification_decision is not None:
            tags.append("clarification_excluded")
        if turn_status in {"failed", "cancelled"}:
            tags.append(f"{turn_status}_excluded")

        brain_flags = dict(shadow_meta.get("brain_capabilities") or {})
        for key in ("needs_tool", "needs_task", "needs_skill", "needs_mcp"):
            if brain_flags.get(key):
                tags.append(f"{key}_excluded")

        if response_plan.approval_prompt:
            tags.append("approval_prompt_excluded")
        structured = response_plan.structured_payload
        natural = structured.get("natural_interaction")
        if isinstance(natural, dict) and natural.get("status") == "pending_action":
            tags.append("pending_action_excluded")
        task_status = structured.get("task_status_semantics") or structured.get("task_status") or {}
        if isinstance(task_status, dict):
            status = str(task_status.get("status") or "")
            if status in {"queued", "running", "waiting_approval", "pending_action"}:
                tags.append(f"task_status_{status}_excluded")
        route_semantics = structured.get("route_semantics") or {}
        if isinstance(route_semantics, dict):
            route = str(route_semantics.get("route") or "")
            if any(marker in route for marker in ["browser", "terminal", "host", "tool"]):
                tags.append("route_semantics_excluded")

        eligible = not tags
        return ShadowPolicyAdvisoryGate(
            eligible_for_policy_advisory=eligible,
            eligibility_reason="eligible" if eligible else tags[0],
            eligibility_tags=tags if tags else ["eligible"],
            eligible_scene=scene if eligible else None,
        )


class ShadowPolicyBaselineExtractor:
    def extract(
        self,
        *,
        response_plan: ResponsePlan,
        assistant_text: str,
        understanding: ConversationUnderstandingShadow,
    ) -> ResponsePolicyShadow:
        text = str(assistant_text or "").strip()
        if text.startswith(("你好", "老板", "嗨", "哈喽", "早", "晚上好")):
            opening_style = "warm_open"
        else:
            opening_style = "natural_direct"
        if any(marker in text for marker in ["接上", "刚才", "继续", "补充"]):
            followthrough_mode = "explicit_followthrough"
        elif understanding.continues_previous_turn:
            followthrough_mode = "implicit_followthrough"
        else:
            followthrough_mode = "standalone"
        structure_hits = sum(
            1 for marker in ["1.", "2.", "3.", "第一", "第二", "第三", "首先", "其次"] if marker in text
        )
        depth_mode = "medium" if structure_hits >= 3 else "light"
        continuation_expectation = (
            "strong"
            if understanding.primary_scene == "multi_turn_followup" or understanding.continues_previous_turn
            else "optional"
        )
        return ResponsePolicyShadow(
            opening_style=opening_style,
            depth_mode=depth_mode,
            followthrough_mode=followthrough_mode,
            boundary_mode="none",
            tool_narration_mode="answer_directly",
            memory_reference_mode="do_not_force",
            continuation_expectation=continuation_expectation,
            quality_dimensions=[],
            risk_notes=[],
        )


class ShadowPromotionCandidateEvaluator:
    def evaluate(
        self,
        *,
        comparison: ShadowPolicyComparison,
        quality_eval: ChatQualityShadowEvaluation,
        gate: ShadowPolicyAdvisoryGate,
    ) -> tuple[bool, list[str], str | None]:
        blockers: list[str] = []
        if not comparison.comparison_enabled:
            blockers.append("comparison_not_enabled")
        blocked_tags = {
            "system_tone_detected",
            "premature_acknowledgement_detected",
            "continuity_drop_risk",
            "over_template_risk",
        }
        for tag in quality_eval.quality_tags:
            if tag in blocked_tags:
                blockers.append(tag)
        if not comparison.policy_diffs:
            blockers.append("no_policy_diffs")
        diff_set = set(comparison.policy_diffs)
        if diff_set and not diff_set.issubset({"opening_style", "followthrough_mode"}):
            blockers.append("diff_scope_not_promotable")
        if not gate.eligible_for_policy_advisory:
            blockers.append("gate_not_eligible")
        if blockers:
            return False, blockers, None
        if "followthrough_mode" in diff_set:
            return True, [], "followthrough_opening"
        if "opening_style" in diff_set:
            return True, [], "casual_chat_opening"
        return False, ["no_promotable_target"], None


class ChatQualityShadowService:
    def __init__(
        self,
        *,
        understanding_service: ConversationUnderstandingService | None = None,
        dialogue_state_service: ChatDialogueStateShadowService | None = None,
        response_policy_service: ResponsePolicyShadowService | None = None,
        action_mapper_service: ActionDialogueMapperShadowService | None = None,
    ) -> None:
        self._understanding = understanding_service or ConversationUnderstandingService()
        self._dialogue_state = dialogue_state_service or ChatDialogueStateShadowService()
        self._response_policy = response_policy_service or ResponsePolicyShadowService()
        self._action_mapper = action_mapper_service or ActionDialogueMapperShadowService()
        self._policy_gate = ShadowPolicyAdvisoryGateService()
        self._baseline = ShadowPolicyBaselineExtractor()
        self._promotion = ShadowPromotionCandidateEvaluator()

    def analyze_turn(
        self,
        *,
        user_text: str,
        recent_messages: list[dict[str, Any]],
        brain_decision: Any | None = None,
        channel_profile: str | None = None,
    ) -> dict[str, Any]:
        understanding = self._understanding.analyze(
            user_text=user_text,
            recent_messages=recent_messages,
            brain_decision=brain_decision,
            channel_profile=channel_profile,
        )
        dialogue_state = self._dialogue_state.build(
            user_text=user_text,
            recent_messages=recent_messages,
            brain_dialogue_state=getattr(brain_decision, "dialogue_state", None),
            understanding=understanding,
        )
        return {
            "version": CHAT_QUALITY_SHADOW_VERSION,
            "advisory_only": True,
            "conversation_understanding": understanding.model_dump(mode="json"),
            "dialogue_state": dialogue_state.model_dump(mode="json"),
            "brain_capabilities": {
                "needs_tool": bool(getattr(getattr(brain_decision, "intent", None), "needs_tool", False)),
                "needs_task": bool(getattr(getattr(brain_decision, "intent", None), "needs_task", False)),
                "needs_skill": bool(getattr(getattr(brain_decision, "intent", None), "needs_skill", False)),
                "needs_mcp": bool(getattr(getattr(brain_decision, "intent", None), "needs_mcp", False)),
            },
        }

    def decorate_response_plan(
        self,
        *,
        response_plan: ResponsePlan,
        assistant_text: str,
        shadow_state: dict[str, Any] | None,
        privacy_level: str | None = None,
        turn_status: str | None = None,
        clarification_decision: dict[str, Any] | None = None,
    ) -> tuple[ResponsePlan, dict[str, Any]]:
        state = dict(shadow_state or {})
        understanding = ConversationUnderstandingShadow(
            **dict(state.get("conversation_understanding") or {})
        )
        dialogue_state = ChatDialogueStateShadow(
            **dict(state.get("dialogue_state") or {})
        )
        gate = self._policy_gate.evaluate(
            understanding=understanding,
            response_plan=response_plan,
            privacy_level=privacy_level,
            shadow_meta=state,
            turn_status=turn_status,
            clarification_decision=clarification_decision,
        )
        baseline = self._baseline.extract(
            response_plan=response_plan,
            assistant_text=assistant_text,
            understanding=understanding,
        )
        response_policy = self._response_policy.recommend(
            understanding=understanding,
            dialogue_state=dialogue_state,
            response_plan=response_plan.structured_payload,
            privacy_level=privacy_level,
        )
        advisory_policy = (
            response_policy
            if gate.eligible_for_policy_advisory
            else None
        )
        action_mapping = self._action_mapper.map(
            response_plan=response_plan.structured_payload,
            understanding=understanding,
        )
        quality_eval = self._evaluate(
            assistant_text=assistant_text,
            response_plan=response_plan,
            understanding=understanding,
            response_policy=response_policy,
            action_mapping=action_mapping,
        )
        diffs = self._policy_diffs(baseline, advisory_policy)
        comparison = ShadowPolicyComparison(
            comparison_enabled=gate.eligible_for_policy_advisory,
            baseline_policy=baseline.model_dump(mode="json"),
            advisory_policy=advisory_policy.model_dump(mode="json") if advisory_policy else None,
            policy_diffs=diffs,
            advisory_summary=self._advisory_summary(gate, diffs),
            safe_to_promote_hint=gate.eligible_for_policy_advisory and bool(diffs),
        )
        promotion_candidate, promotion_blockers, promotion_target = self._promotion.evaluate(
            comparison=comparison,
            quality_eval=quality_eval,
            gate=gate,
        )
        payload = {
            "version": CHAT_QUALITY_SHADOW_VERSION,
            "advisory_only": True,
            "conversation_understanding": understanding.model_dump(mode="json"),
            "dialogue_state": dialogue_state.model_dump(mode="json"),
            "response_policy": response_policy.model_dump(mode="json"),
            "policy_advisory_gate": gate.model_dump(mode="json"),
            "response_policy_baseline": baseline.model_dump(mode="json"),
            "response_policy_advisory": (
                advisory_policy.model_dump(mode="json") if advisory_policy else None
            ),
            "response_policy_comparison": comparison.model_dump(mode="json"),
            "action_dialogue_mapping": action_mapping.model_dump(mode="json"),
            "quality_eval": quality_eval.model_dump(mode="json"),
            "promotion_candidate": promotion_candidate,
            "promotion_blockers": promotion_blockers,
            "promotion_target": promotion_target,
        }
        updated_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "chat_quality_shadow": payload,
                }
            }
        )
        return updated_plan, self.trace_payload(payload)

    def trace_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": str(payload.get("version") or CHAT_QUALITY_SHADOW_VERSION),
            "advisory_only": True,
            "conversation_understanding": redact(payload.get("conversation_understanding") or {}),
            "dialogue_state": redact(payload.get("dialogue_state") or {}),
            "response_policy": redact(payload.get("response_policy") or {}),
            "policy_advisory_gate": redact(payload.get("policy_advisory_gate") or {}),
            "response_policy_baseline": redact(payload.get("response_policy_baseline") or {}),
            "response_policy_advisory": redact(payload.get("response_policy_advisory") or {}),
            "response_policy_comparison": redact(payload.get("response_policy_comparison") or {}),
            "action_dialogue_mapping": redact(payload.get("action_dialogue_mapping") or {}),
            "quality_eval": redact(payload.get("quality_eval") or {}),
            "promotion_candidate": bool(payload.get("promotion_candidate")),
            "promotion_blockers": redact(payload.get("promotion_blockers") or []),
            "promotion_target": payload.get("promotion_target"),
        }

    def _policy_diffs(
        self,
        baseline: ResponsePolicyShadow,
        advisory: ResponsePolicyShadow | None,
    ) -> list[str]:
        if advisory is None:
            return []
        baseline_data = baseline.model_dump(mode="json")
        advisory_data = advisory.model_dump(mode="json")
        return [
            field
            for field in _POLICY_DIFF_FIELDS
            if baseline_data.get(field) != advisory_data.get(field)
        ]

    def _advisory_summary(
        self,
        gate: ShadowPolicyAdvisoryGate,
        diffs: list[str],
    ) -> str | None:
        if not gate.eligible_for_policy_advisory:
            return None
        if not diffs:
            return "shadow advisory matches current low-risk reply policy"
        return "shadow advisory differs on " + ", ".join(diffs)

    def _evaluate(
        self,
        *,
        assistant_text: str,
        response_plan: ResponsePlan,
        understanding: ConversationUnderstandingShadow,
        response_policy: ResponsePolicyShadow,
        action_mapping: ActionDialogueMappingShadow,
    ) -> ChatQualityShadowEvaluation:
        text = str(assistant_text or "")
        lowered = text.lower()
        tags: list[str] = []
        risk_notes: list[str] = []

        system_tone_markers = [
            "我先接住你",
            "系统状态报告",
            "我将为你执行如下步骤",
            "接下来我会为你",
            "以下是处理流程",
        ]
        if any(marker in text for marker in system_tone_markers):
            tags.append("system_tone_detected")
            risk_notes.append("reply_contains_overt_system_speech")

        if any(text.startswith(marker) for marker in ["好的，我来处理", "收到，我来执行", "我先"]):
            tags.append("premature_acknowledgement_detected")

        if action_mapping.blocked_by_approval and any(
            marker in text for marker in ["已完成", "已经处理", "搞定了"]
        ):
            tags.append("false_done_risk")
            risk_notes.append("approval_pending_but_reply_sounds_done")
        elif not action_mapping.should_claim_completion and any(
            marker in text for marker in ["已完成", "已经执行", "我刚执行了"]
        ):
            tags.append("false_done_risk")

        if understanding.continues_previous_turn and not any(
            marker in text for marker in ["接上", "刚才", "继续", "补充"]
        ):
            tags.append("continuity_drop_risk")

        if (
            sum(1 for marker in ["首先", "其次", "最后", "第1", "第2", "第3"] if marker in text)
            >= 4
        ):
            tags.append("over_template_risk")

        if getattr(understanding, "action_request", False):
            if action_mapping.should_explain_pending and not any(
                marker in text for marker in ["确认", "继续", "等你", "我会先停"]
            ):
                tags.append("tool_narration_missing")
            if not action_mapping.should_explain_pending and any(
                marker in text for marker in ["我正在调用", "系统已进入执行流程", "准备为你执行"]
            ):
                tags.append("tool_narration_overexposed")

        if (
            understanding.memory_related
            and "记得" in text
            and "你之前" not in text
            and "上次" not in text
        ):
            tags.append("memory_reference_misaligned")

        if understanding.primary_scene == "boundary_question" and any(
            marker in lowered for marker in ["系统状态", "内部策略", "规则如下"]
        ):
            tags.append("boundary_reply_too_mechanical")

        score = max(0.0, 1.0 - 0.12 * len(set(tags)))
        if response_policy.boundary_mode == "approval_boundary" and "false_done_risk" not in tags:
            risk_notes.append("pending_action_honesty_preserved")
        return ChatQualityShadowEvaluation(
            quality_tags=sorted(set(tags)),
            quality_score_hint=score,
            risk_notes=sorted(set(risk_notes)),
        )
