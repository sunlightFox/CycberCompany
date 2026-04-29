from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from brain.adapters import (
    CancelToken,
    ModelAdapterError,
    ModelChatRequest,
    OpenAICompatibleClient,
)
from core_types import (
    AgentNextActionDecision,
    ModelPlanGenerationResult,
    ModelPlanRequest,
    ModelRecoverySuggestion,
    PlanCandidate,
    PlanDeltaSuggestion,
    PlannerCapabilityCandidate,
    PlanPolicyPrune,
    PlanQualityScore,
    PlanVerificationResult,
    RiskLevel,
    TaskMode,
    TaskPlan,
    ToolFailureRecoveryPlan,
)
from pydantic import ValidationError
from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.schemas.tasks import TaskCreateRequest

if TYPE_CHECKING:
    from app.db.repositories.brain_repo import BrainRepository
    from app.services.model_routing import ModelRoutingService
    from app.services.secrets import SecretStore


_ALLOWED_STEP_TYPES = {"compose", "tool_call", "mcp_call", "skill_run", "skill_match"}
_ALLOWED_MODES = {
    TaskMode.WORKFLOW.value,
    TaskMode.AGENT.value,
    TaskMode.SUPERVISOR.value,
}
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "cookie",
    "private_key",
    "mnemonic",
    "api_key",
    "redacted_api_key",
    "redacted_token",
    "redacted_password",
    "redacted_private_key",
    "redacted_mnemonic",
    "redacted_local_path",
)
_DANGEROUS_COMMAND_MARKERS = (
    "rm -rf",
    "del /f",
    "format ",
    "shutdown",
    "mkfs",
    "diskpart",
    ":(){",
)
_SENSITIVE_PATH_RE = re.compile(
    r"([a-zA-Z]:\\(?:Users|Windows|ProgramData)\\|/(?:home|Users|etc|root|var)/)"
)


class ModelPlannerUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelPlannerAdapter(Protocol):
    name: str

    async def generate(self, request: ModelPlanRequest) -> dict[str, Any] | str: ...


class DisabledModelPlannerAdapter:
    name = "disabled"

    async def generate(self, request: ModelPlanRequest) -> dict[str, Any] | str:
        del request
        raise ModelPlannerUnavailable("model_planner_not_configured")


class BrainModelPlannerAdapter:
    name = "brain_openai_compatible"

    def __init__(
        self,
        *,
        brain_repo: BrainRepository,
        model_routing_service: ModelRoutingService,
        secret_store: SecretStore,
    ) -> None:
        self._brains = brain_repo
        self._routing = model_routing_service
        self._secrets = secret_store

    async def generate(self, request: ModelPlanRequest) -> dict[str, Any] | str:
        config = await self._routing.get_config()
        planner_config = _planner_config(config, request.planner_config)
        if planner_config["model_assist_mode"] == "disabled":
            raise ModelPlannerUnavailable("model_planner_disabled")
        brain = await self._select_brain(request, config)
        if brain is None:
            raise ModelPlannerUnavailable("no_routable_planner_brain")
        client = OpenAICompatibleClient(
            str(brain["endpoint"]),
            self._secrets.get_secret(brain.get("api_key_ref")),
        )
        model_request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=_planner_messages(request),
            temperature=0.1,
            max_output_tokens=min(
                int(brain.get("default_max_output_tokens") or 1024),
                int(planner_config.get("max_output_tokens") or 1200),
            ),
            top_p=0.9,
            timeout_seconds=min(
                int(brain.get("timeout_seconds") or 180),
                int(planner_config["timeout_seconds"]),
            ),
            stream=False,
            trace_id=request.trace_id or "trc_model_planner",
            turn_id=f"task:{request.task_id}",
            route_id=f"planner:{brain['brain_id']}",
            privacy_level=request.privacy_level,
            retry_count=0,
            metadata={"purpose": "model_planner_candidate", "brain_id": brain["brain_id"]},
        )
        result = await client.complete_chat(model_request, CancelToken())
        return {
            "candidate": result.text,
            "usage": result.usage,
            "finish_reason": result.finish_reason,
            "brain": {
                "brain_id": brain["brain_id"],
                "provider": brain.get("provider"),
                "model_name": brain.get("model_name"),
                "is_local": bool(brain.get("is_local")),
            },
        }

    async def _select_brain(
        self,
        request: ModelPlanRequest,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        brains = await self._brains.list_routable_brains()
        if not brains:
            return None
        privacy_level = request.privacy_level
        allow_cloud = _allow_cloud(config, privacy_level)
        candidates = [
            brain
            for brain in brains
            if bool(brain.get("is_local")) or (allow_cloud and bool(brain.get("allow_cloud")))
        ]
        if privacy_level == "high":
            candidates = [brain for brain in candidates if bool(brain.get("is_local"))]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda item: (not bool(item.get("is_local")), item["brain_id"]),
        )[0]


@dataclass(frozen=True)
class CandidateEvidence:
    candidate: PlanCandidate
    verification: PlanVerificationResult
    prunes: list[PlanPolicyPrune]
    final_steps: list[dict[str, Any]]
    quality_score: PlanQualityScore


@dataclass(frozen=True)
class PlanningEvidence:
    candidate: PlanCandidate
    verification: PlanVerificationResult
    prunes: list[PlanPolicyPrune]
    capability_candidates: list[PlannerCapabilityCandidate]
    final_steps: list[dict[str, Any]]
    generation: ModelPlanGenerationResult
    candidates: list[CandidateEvidence] = field(default_factory=list)


class ModelPlannerService:
    """Candidate-only model planner with rule fallback.

    Real model output is advisory. Every candidate, including model-generated
    candidates, is validated, pruned, scored, and only then considered for the
    final executable plan.
    """

    def __init__(self, adapter: ModelPlannerAdapter | None = None) -> None:
        self._adapter = adapter or DisabledModelPlannerAdapter()
        self._quality = PlanQualityScorer()

    def set_adapter(self, adapter: ModelPlannerAdapter | None) -> None:
        self._adapter = adapter or DisabledModelPlannerAdapter()

    async def build_evidence(
        self,
        *,
        task_id: str,
        request: TaskCreateRequest,
        plan: TaskPlan,
        trace_id: str | None,
    ) -> PlanningEvidence | None:
        if not _requires_candidate_evidence(request, plan):
            return None
        now = utc_now_iso()
        model_request = _model_plan_request(
            task_id=task_id,
            request=request,
            plan=plan,
            trace_id=trace_id,
            created_at=now,
        )
        generation_id = new_id("plangen")
        candidate_specs: list[tuple[PlanCandidate, str | None]] = [
            (
                self._rule_candidate(
                    task_id,
                    request,
                    plan,
                    trace_id=trace_id,
                    created_at=now,
                    generation_id=generation_id,
                ),
                None,
            )
        ]
        model_assist_attempted = False
        fallback_used = True
        fallback_reason: str | None = None
        latency_ms = 0
        model_call: dict[str, Any] = {
            "adapter": self._adapter.name,
            "status": "skipped",
            "privacy_level": model_request.privacy_level,
        }
        planner_config = model_request.planner_config
        if planner_config["model_assist_mode"] != "disabled":
            started = time.perf_counter()
            try:
                raw = await self._adapter.generate(model_request)
                latency_ms = int((time.perf_counter() - started) * 1000)
                model_assist_attempted = True
                model_candidate, parsed_model_call = _candidate_from_model_output(
                    task_id=task_id,
                    request=request,
                    plan=plan,
                    raw=raw,
                    trace_id=trace_id,
                    created_at=now,
                    generation_id=generation_id,
                    latency_ms=latency_ms,
                )
                candidate_specs.append((model_candidate, None))
                model_call = {
                    **parsed_model_call,
                    "adapter": self._adapter.name,
                    "status": "completed",
                    "latency_ms": latency_ms,
                }
                fallback_used = False
            except ModelPlannerUnavailable as exc:
                fallback_reason = exc.reason
                model_call = {
                    **model_call,
                    "status": "fallback",
                    "fallback_reason": fallback_reason,
                }
            except TimeoutError:
                latency_ms = int((time.perf_counter() - started) * 1000)
                model_assist_attempted = True
                fallback_reason = "model_timeout"
                candidate_specs.append(
                    (
                        _failed_model_candidate(
                            task_id=task_id,
                            request=request,
                            plan=plan,
                            trace_id=trace_id,
                            created_at=now,
                            generation_id=generation_id,
                            fallback_reason=fallback_reason,
                        ),
                        fallback_reason,
                    )
                )
                model_call = {
                    "adapter": self._adapter.name,
                    "status": "fallback",
                    "fallback_reason": fallback_reason,
                    "latency_ms": latency_ms,
                }
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
                latency_ms = int((time.perf_counter() - started) * 1000)
                model_assist_attempted = True
                fallback_reason = "schema_invalid"
                candidate_specs.append(
                    (
                        _failed_model_candidate(
                            task_id=task_id,
                            request=request,
                            plan=plan,
                            trace_id=trace_id,
                            created_at=now,
                            generation_id=generation_id,
                            fallback_reason=fallback_reason,
                        ),
                        fallback_reason,
                    )
                )
                model_call = {
                    "adapter": self._adapter.name,
                    "status": "fallback",
                    "fallback_reason": fallback_reason,
                    "latency_ms": latency_ms,
                }
            except ModelAdapterError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                model_assist_attempted = True
                fallback_reason = exc.code.value
                model_call = {
                    "adapter": self._adapter.name,
                    "status": "fallback",
                    "fallback_reason": fallback_reason,
                    "latency_ms": latency_ms,
                }

        evidence_items: list[CandidateEvidence] = []
        for candidate, candidate_error in candidate_specs:
            verification = PlanVerifier().verify(
                candidate,
                request=request,
                plan=plan,
                trace_id=trace_id,
                created_at=now,
            )
            prunes, final_steps = PolicyPruner().prune(
                candidate,
                request=request,
                plan=plan,
                trace_id=trace_id,
                created_at=now,
            )
            quality = self._quality.score(
                candidate=candidate,
                verification=verification,
                prunes=prunes,
                final_steps=final_steps,
                request=request,
                plan=plan,
                trace_id=trace_id,
                created_at=now,
                generation_id=generation_id,
                candidate_error=candidate_error,
            )
            candidate = candidate.model_copy(
                update={
                    "steps": _redact_sensitive_planner_payload(candidate.steps),
                    "risk_hints": _redact_sensitive_planner_payload(candidate.risk_hints),
                    "status": "selected" if False else candidate.status,
                    "model_assist": {
                        **candidate.model_assist,
                        "quality_score": quality.model_dump(mode="json"),
                    },
                }
            )
            evidence_items.append(
                CandidateEvidence(
                    candidate=candidate,
                    verification=verification,
                    prunes=prunes,
                    final_steps=final_steps,
                    quality_score=quality,
                )
            )
        selected = _select_candidate(evidence_items)
        selected_quality = selected.quality_score.model_copy(update={"selected": True})
        selected_candidate = selected.candidate.model_copy(
            update={
                "status": "selected",
                "model_assist": {
                    **selected.candidate.model_assist,
                    "selected": True,
                    "quality_score": selected_quality.model_dump(mode="json"),
                    "fallback_used": fallback_used or selected.candidate.source != "model_assist",
                    "fallback_reason": fallback_reason
                    if selected.candidate.source != "model_assist"
                    else None,
                },
            }
        )
        selected = CandidateEvidence(
            candidate=selected_candidate,
            verification=selected.verification,
            prunes=selected.prunes,
            final_steps=selected.final_steps,
            quality_score=selected_quality,
        )
        evidence_items = [
            selected if item.candidate.candidate_id == selected.candidate.candidate_id else item
            for item in evidence_items
        ]
        generation = ModelPlanGenerationResult(
            generation_id=generation_id,
            request_id=model_request.request_id,
            task_id=task_id,
            status="selected",
            model_assist_attempted=model_assist_attempted,
            fallback_used=selected.candidate.source != "model_assist" or fallback_used,
            fallback_reason=fallback_reason
            if selected.candidate.source != "model_assist"
            else None,
            latency_ms=latency_ms,
            model_call=redact(model_call),
            candidates=[item.candidate for item in evidence_items],
            quality_scores=[item.quality_score for item in evidence_items],
            selected_candidate_id=selected.candidate.candidate_id,
            trace_id=trace_id,
            created_at=now,
        )
        return PlanningEvidence(
            candidate=selected.candidate,
            verification=selected.verification,
            prunes=[prune for item in evidence_items for prune in item.prunes],
            capability_candidates=_capability_candidates(
                task_id=task_id,
                plan=plan,
                trace_id=trace_id,
                created_at=now,
            ),
            final_steps=selected.final_steps,
            generation=generation,
            candidates=evidence_items,
        )

    def _rule_candidate(
        self,
        task_id: str,
        request: TaskCreateRequest,
        plan: TaskPlan,
        *,
        trace_id: str | None,
        created_at: str,
        generation_id: str,
    ) -> PlanCandidate:
        high_risk_steps = [
            {
                "step_key": step.get("step_key"),
                "step_type": step.get("step_type"),
                "risk_level": step.get("risk_level", "R1"),
                "requires_approval": _risk_order(str(step.get("risk_level", "R1"))) >= 3,
            }
            for step in plan.steps
            if _risk_order(str(step.get("risk_level", "R1"))) >= 3
        ]
        missing_information = []
        if not request.resource_handle_ids and any(
            step.get("step_type") in {"tool_call", "skill_run", "mcp_call"}
            for step in plan.steps
        ):
            missing_information.append("asset_scope_if_required_by_runtime")
        return PlanCandidate(
            candidate_id=new_id("plancand"),
            organization_id="org_default",
            task_id=task_id,
            planner_type="model_planner_contract",
            source="deterministic_rule_surrogate",
            recommended_mode=plan.mode.value,
            steps=redact(plan.steps),
            success_criteria=plan.success_criteria,
            assumptions=[
                *plan.assumptions,
                "模型辅助规划不可用或未选中时，规则计划作为稳定 fallback。",
            ],
            missing_information=missing_information,
            risk_hints=redact(high_risk_steps),
            required_capabilities=plan.required_capabilities,
            required_assets=plan.required_assets,
            confidence=0.78 if plan.mode in {TaskMode.AGENT, TaskMode.SUPERVISOR} else 0.68,
            reasoning_summary="规则候选参与质量评分，作为模型候选失败时的安全 fallback。",
            status="candidate",
            model_assist={
                "enabled": False,
                "attempted": False,
                "generation_id": generation_id,
                "fallback": "rule_workflow_plan",
                "model_hint": request.planner_context.get("model_hint"),
            },
            trace_id=trace_id,
            created_at=created_at,
        )


class PlanVerifier:
    def verify(
        self,
        candidate: PlanCandidate,
        *,
        request: TaskCreateRequest,
        plan: TaskPlan,
        trace_id: str | None,
        created_at: str,
    ) -> PlanVerificationResult:
        issues: list[dict[str, Any]] = []
        step_types = {str(step.get("step_type") or "") for step in candidate.steps}
        step_type_allowed = bool(step_types) and step_types.issubset(_ALLOWED_STEP_TYPES)
        if not step_type_allowed:
            issues.append({"code": "step_type_not_allowed", "step_types": sorted(step_types)})

        no_direct_secret = not _contains_secret_or_sensitive_path(candidate.model_dump(mode="json"))
        if not no_direct_secret:
            issues.append({"code": "secret_or_sensitive_path_detected"})

        no_direct_shell = not any(_dangerous_terminal_step(step) for step in candidate.steps)
        if not no_direct_shell:
            issues.append({"code": "dangerous_shell_command_from_candidate"})

        capability_available = not any(
            code in plan.planner_reason_codes
            for code in {
                "skill_unavailable_removed_from_plan",
                "mcp_tool_unavailable_removed_from_plan",
            }
        )
        if not capability_available:
            issues.append({"code": "unavailable_capability_pruned"})

        high_risk_steps = [
            step for step in candidate.steps if _risk_order(str(step.get("risk_level", "R1"))) >= 3
        ]
        approval_strategy_present = not high_risk_steps or bool(
            plan.approval_strategy.get("required_before_execution")
            or plan.approval_strategy.get("high_risk_step_keys")
        )
        if not approval_strategy_present:
            issues.append({"code": "approval_strategy_missing"})

        budget = plan.budget
        budget_within_limit = len(candidate.steps) <= max(budget.max_steps, budget.max_loop_steps)
        if not budget_within_limit:
            issues.append({"code": "candidate_exceeds_budget", "step_count": len(candidate.steps)})

        checks = {
            "schema_valid": candidate.status != "invalid",
            "mode_allowed": candidate.recommended_mode in _ALLOWED_MODES,
            "step_type_allowed": step_type_allowed,
            "capability_available": capability_available,
            "asset_handle_allowed": set(candidate.required_assets).issubset(
                set(request.resource_handle_ids)
            ),
            "risk_level_acceptable": True,
            "approval_strategy_present": approval_strategy_present,
            "budget_within_limit": budget_within_limit,
            "no_direct_secret": no_direct_secret,
            "no_direct_shell_command_from_model": no_direct_shell,
        }
        if not checks["mode_allowed"]:
            issues.append({"code": "mode_not_executable", "mode": candidate.recommended_mode})
        if not checks["asset_handle_allowed"]:
            issues.append({"code": "undeclared_asset_handle"})
        if candidate.status == "invalid":
            issues.append({"code": "candidate_schema_invalid"})
        return PlanVerificationResult(
            verification_id=new_id("planver"),
            organization_id=candidate.organization_id,
            task_id=candidate.task_id,
            candidate_id=candidate.candidate_id,
            issues=redact(issues),
            status="passed" if all(checks.values()) else "failed",
            trace_id=trace_id,
            created_at=created_at,
            **checks,
        )


class PolicyPruner:
    def prune(
        self,
        candidate: PlanCandidate,
        *,
        request: TaskCreateRequest,
        plan: TaskPlan,
        trace_id: str | None,
        created_at: str,
    ) -> tuple[list[PlanPolicyPrune], list[dict[str, Any]]]:
        del request
        prunes: list[PlanPolicyPrune] = []
        final_steps: list[dict[str, Any]] = []
        for step in candidate.steps:
            if _dangerous_terminal_step(step):
                prunes.append(
                    _prune(
                        candidate,
                        "remove_dangerous_shell_command",
                        original=step,
                        pruned={},
                        reason_codes=["no_direct_shell_command_from_model", "terminal_policy_deny"],
                        trace_id=trace_id,
                        created_at=created_at,
                    )
                )
                continue
            if _contains_secret_or_sensitive_path(step):
                prunes.append(
                    _prune(
                        candidate,
                        "remove_sensitive_payload",
                        original=step,
                        pruned={},
                        reason_codes=[
                            "no_direct_secret",
                            "no_local_sensitive_path",
                            "candidate_payload_redacted",
                        ],
                        trace_id=trace_id,
                        created_at=created_at,
                    )
                )
                continue
            if step.get("step_type") == "skill_run" and not plan.preflight.get(
                "capability_snapshot", {}
            ).get("explicit_skill_available", False):
                prunes.append(
                    _prune(
                        candidate,
                        "remove_unavailable_skill",
                        original=step,
                        pruned={},
                        reason_codes=["skill_no_enabled_skill"],
                        trace_id=trace_id,
                        created_at=created_at,
                    )
                )
                continue
            if step.get("step_type") == "mcp_call" and not plan.preflight.get("mcp_tool_refs"):
                prunes.append(
                    _prune(
                        candidate,
                        "remove_unavailable_mcp_tool",
                        original=step,
                        pruned={},
                        reason_codes=["mcp_no_active_tool"],
                        trace_id=trace_id,
                        created_at=created_at,
                    )
                )
                continue
            final_steps.append(redact(step))
            if _risk_order(str(step.get("risk_level", "R1"))) >= 5:
                prunes.append(
                    _prune(
                        candidate,
                        "insert_approval_checkpoint",
                        original=step,
                        pruned={
                            "step_key": step.get("step_key"),
                            "approval_required": True,
                            "strategy": "plan_first_then_step_gate",
                        },
                        reason_codes=["high_risk_plan_first", "approval_checkpoint_required"],
                        trace_id=trace_id,
                        created_at=created_at,
                    )
                )
        if not final_steps:
            final_steps = [
                {
                    "step_key": "compose_report",
                    "step_type": "compose",
                    "title": "生成任务报告",
                    "risk_level": "R1",
                    "input": {"fallback_reason": "all_candidate_actions_pruned"},
                }
            ]
            prunes.append(
                _prune(
                    candidate,
                    "fallback_to_rule_plan",
                    original={"step_count": len(candidate.steps)},
                    pruned={"step_key": "compose_report"},
                    reason_codes=["candidate_actions_pruned", "safe_compose_fallback"],
                    trace_id=trace_id,
                    created_at=created_at,
                )
            )
        return prunes, final_steps


class PlanQualityScorer:
    def score(
        self,
        *,
        candidate: PlanCandidate,
        verification: PlanVerificationResult,
        prunes: list[PlanPolicyPrune],
        final_steps: list[dict[str, Any]],
        request: TaskCreateRequest,
        plan: TaskPlan,
        trace_id: str | None,
        created_at: str,
        generation_id: str,
        candidate_error: str | None,
    ) -> PlanQualityScore:
        del trace_id, generation_id
        goal_terms = set(_quality_terms(request.goal))
        step_text = json.dumps(final_steps, ensure_ascii=False).lower()
        goal_hits = sum(1 for term in goal_terms if term in step_text)
        goal_coverage = 0.55 if not goal_terms else min(1.0, goal_hits / max(1, len(goal_terms)))
        if any(step.get("step_key") == "compose_report" for step in final_steps):
            goal_coverage = max(goal_coverage, 0.5)
        step_coherence = (
            1.0 if final_steps and final_steps[-1].get("step_type") == "compose" else 0.72
        )
        requested_caps = set(plan.required_capabilities)
        final_caps = set(_required_capabilities_from_steps(final_steps))
        capability_fit = (
            1.0 if not requested_caps else len(requested_caps & final_caps) / len(requested_caps)
        )
        unsafe_prunes = [
            prune
            for prune in prunes
            if prune.prune_type
            in {
                "remove_dangerous_shell_command",
                "remove_sensitive_payload",
                "fallback_to_rule_plan",
            }
        ]
        safety_compliance = 1.0 if verification.status == "passed" else 0.35
        if unsafe_prunes:
            safety_compliance = min(safety_compliance, 0.55)
        budget_limit = max(1, max(plan.budget.max_steps, plan.budget.max_loop_steps))
        budget_efficiency = max(0.0, 1.0 - max(0, len(final_steps) - budget_limit) / budget_limit)
        missing_information_handling = 0.85 if candidate.missing_information else 0.72
        if candidate_error:
            missing_information_handling = 0.3
        recoverability = (
            0.85 if any(step.get("step_type") == "compose" for step in final_steps) else 0.62
        )
        artifact_clarity = (
            0.9
            if any(step.get("step_key") == "compose_report" for step in final_steps)
            else 0.68
        )
        total = round(
            (
                goal_coverage
                + step_coherence
                + capability_fit
                + safety_compliance
                + budget_efficiency
                + missing_information_handling
                + recoverability
                + artifact_clarity
            )
            / 8,
            4,
        )
        reason_codes = ["quality_scored", f"source_{candidate.source}"]
        if verification.status != "passed":
            reason_codes.append("verification_failed")
        if unsafe_prunes:
            reason_codes.append("unsafe_prunes_present")
        if candidate_error:
            reason_codes.append(f"model_candidate_error_{candidate_error}")
        return PlanQualityScore(
            score_id=new_id("planscore"),
            task_id=candidate.task_id,
            candidate_id=candidate.candidate_id,
            total_score=total,
            goal_coverage=round(goal_coverage, 4),
            step_coherence=round(step_coherence, 4),
            capability_fit=round(capability_fit, 4),
            safety_compliance=round(safety_compliance, 4),
            budget_efficiency=round(budget_efficiency, 4),
            missing_information_handling=round(missing_information_handling, 4),
            recoverability=round(recoverability, 4),
            artifact_clarity=round(artifact_clarity, 4),
            selected=False,
            reason_codes=reason_codes,
            created_at=created_at,
        )


class ObservationAwareReplanner:
    def suggest(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        loop_index: int,
        task_status: str,
        step_status: str | None,
        next_step_key: str | None,
        stop_reason: str | None,
        budget_snapshot: dict[str, Any],
        trace_id: str | None,
    ) -> PlanDeltaSuggestion:
        trigger = _replan_trigger(
            step=step,
            task_status=task_status,
            step_status=step_status,
            stop_reason=stop_reason,
            budget_snapshot=budget_snapshot,
        )
        action = "act"
        confidence = 0.74
        reason_codes = ["observation_aware_replanner", trigger]
        missing: list[str] = []
        if trigger == "budget_near_limit" or stop_reason == "budget_exhausted":
            action = "stop_budget"
            confidence = 0.9
        elif stop_reason == "approval_required" or task_status == "waiting_approval":
            action = "request_approval"
            confidence = 0.92
        elif trigger in {"safety_blocked", "permission_denied"}:
            action = "stop_blocked"
            confidence = 0.88
        elif trigger in {"tool_failed", "tool_output_invalid", "mcp_unready", "skill_disabled"}:
            action = "revise_plan"
            confidence = 0.78
            if trigger in {"mcp_unready", "skill_disabled"}:
                missing.append("enable_or_replace_required_capability")
        elif next_step_key is None:
            action = "stop_success"
            confidence = 0.82
        plan_delta = {
            "strategy": action,
            "trigger_reason": trigger,
            "next_step_key": next_step_key,
            "current_step_key": step.get("step_key") if step else None,
            "safe_pending_only": True,
            "loop_index": loop_index,
        }
        return PlanDeltaSuggestion(
            suggestion_id=new_id("plandel"),
            task_id=task["task_id"],
            trigger_reason=trigger,
            next_action_type=action,
            plan_delta=redact(plan_delta),
            new_missing_information=missing,
            revised_steps=[],
            stop_reason=stop_reason,
            confidence=confidence,
            reason_codes=reason_codes,
            model_assist={"enabled": False, "fallback": "rule_observation_replanner"},
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )


class AgentNextActionSelector:
    def select(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        iteration_id: str,
        loop_index: int,
        task_status: str,
        step_status: str | None,
        next_step_key: str | None,
        stop_reason: str | None,
        budget_snapshot: dict[str, Any],
        trace_id: str | None,
        plan_delta_suggestion: PlanDeltaSuggestion | None = None,
    ) -> AgentNextActionDecision:
        action_type = "act"
        reason_codes = ["next_pending_step"]
        needs_user_input = False
        needs_approval = False
        confidence = 0.76
        plan_delta: dict[str, Any] = {"next_step_key": next_step_key, "strategy": action_type}
        if plan_delta_suggestion is not None:
            action_type = plan_delta_suggestion.next_action_type
            reason_codes = plan_delta_suggestion.reason_codes
            confidence = plan_delta_suggestion.confidence
            plan_delta = plan_delta_suggestion.model_dump(mode="json")
        if stop_reason == "budget_exhausted":
            action_type = "stop_budget"
            reason_codes = ["budget_exhausted", "create_retry_plan", *reason_codes]
            confidence = max(confidence, 0.9)
        elif stop_reason == "approval_required" or task_status == "waiting_approval":
            action_type = "request_approval"
            needs_approval = True
            reason_codes = ["approval_required", "do_not_bypass_controls", *reason_codes]
            confidence = max(confidence, 0.92)
        elif stop_reason in {"blocked_by_safety", "failed"} or step_status == "failed":
            if action_type not in {"revise_plan", "ask_user"}:
                action_type = "stop_blocked"
            reason_codes = ["step_failed", "create_failure_recovery_plan", *reason_codes]
            confidence = max(confidence, 0.86)
        elif next_step_key is None:
            action_type = "stop_success"
            reason_codes = ["no_pending_steps", *reason_codes]
            confidence = max(confidence, 0.82)
        needs_user_input = action_type == "ask_user"
        return AgentNextActionDecision(
            decision_id=new_id("nact"),
            organization_id=task["organization_id"],
            task_id=task["task_id"],
            iteration_id=iteration_id,
            loop_index=loop_index,
            next_action_type=action_type,
            selected_step_id=step.get("step_id") if step else None,
            selected_step_key=step.get("step_key") if step else None,
            plan_delta=redact(plan_delta),
            needs_user_input=needs_user_input,
            needs_approval=needs_approval,
            stop_reason=stop_reason,
            confidence=confidence,
            reason_codes=_dedupe(reason_codes),
            budget_snapshot=budget_snapshot,
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )


class ToolFailureRecoveryPlanner:
    def plan(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        failure_reason: str,
        trace_id: str | None,
    ) -> ToolFailureRecoveryPlan:
        suggestion = ModelAssistedRecoveryPlanner().suggest(
            task=task,
            step=step,
            failure_reason=failure_reason,
            trace_id=trace_id,
        )
        return ToolFailureRecoveryPlan(
            recovery_plan_id=new_id("recovery"),
            organization_id=task["organization_id"],
            task_id=task["task_id"],
            step_id=step.get("step_id") if step else None,
            tool_call_id=step.get("tool_call_id") if step else None,
            failure_type=suggestion.failure_type,
            recovery_action=suggestion.recovery_action,
            suggested_actions=suggestion.suggested_actions,
            retry_allowed=suggestion.retry_allowed,
            bypass_controls=False,
            status="open",
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )


class ModelAssistedRecoveryPlanner:
    def suggest(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        failure_reason: str,
        trace_id: str | None,
    ) -> ModelRecoverySuggestion:
        failure_type = _failure_type(step, failure_reason)
        if failure_type in {"permission_denied", "safety_blocked", "approval_required"}:
            recovery_action = "pause_with_boundary"
            suggested_actions = ["等待审批或移除受阻动作", "调整任务范围后重试"]
            retry_allowed = False
            reason_codes = ["do_not_bypass_controls", failure_type]
        elif failure_type == "budget_exhausted":
            recovery_action = "pause_with_retry_plan"
            suggested_actions = ["提高预算后重试", "缩小任务范围后重试"]
            retry_allowed = True
            reason_codes = ["budget_exhausted", "retry_plan_required"]
        elif failure_type == "mcp_server_unready":
            recovery_action = "switch_to_read_only_or_pause"
            suggested_actions = ["等待 MCP server ready 后重试", "改用不依赖 MCP 的只读方案"]
            retry_allowed = False
            reason_codes = ["mcp_unready", "avoid_repeated_call"]
        elif failure_type == "skill_disabled":
            recovery_action = "ask_user_for_scope"
            suggested_actions = ["启用所需 Skill 后重试", "改用无 Skill 的保守方案"]
            retry_allowed = False
            reason_codes = ["skill_disabled", "policy_preserved"]
        elif failure_type == "timeout":
            recovery_action = "retry_with_modified_args"
            suggested_actions = ["缩小输入范围后重试一次", "改为分步骤完成"]
            retry_allowed = True
            reason_codes = ["timeout", "bounded_retry"]
        else:
            recovery_action = "complete_partial"
            suggested_actions = ["保留已完成结果", "请用户补充缺失范围后继续"]
            retry_allowed = failure_type == "invalid_output"
            reason_codes = [failure_type, "model_assist_recovery_fallback"]
        return ModelRecoverySuggestion(
            suggestion_id=new_id("recsug"),
            task_id=task["task_id"],
            step_id=step.get("step_id") if step else None,
            failure_type=failure_type,
            recovery_action=recovery_action,
            suggested_actions=suggested_actions,
            retry_allowed=retry_allowed,
            bypass_controls=False,
            confidence=0.78,
            reason_codes=reason_codes,
            model_assist={"enabled": False, "fallback": "rule_model_assist_contract"},
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )


def _requires_candidate_evidence(request: TaskCreateRequest, plan: TaskPlan) -> bool:
    reason_codes = set(plan.planner_reason_codes)
    return (
        plan.mode in {TaskMode.AGENT, TaskMode.SUPERVISOR}
        or _risk_order(plan.risk_level.value) >= 5
        or bool({"skill_considered", "mcp_considered"} & reason_codes)
        or bool(plan.preflight.get("blocked_actions"))
    )


def _model_plan_request(
    *,
    task_id: str,
    request: TaskCreateRequest,
    plan: TaskPlan,
    trace_id: str | None,
    created_at: str,
) -> ModelPlanRequest:
    planner_config = _planner_config({}, request.planner_context.get("planner", {}))
    return ModelPlanRequest(
        request_id=new_id("planreq"),
        task_id=task_id,
        goal=str(redact(request.goal)),
        dialogue_state_summary=redact(request.planner_context.get("dialogue_state", {})),
        intent_summary=redact(request.planner_context.get("intent", {})),
        mode_summary={"mode": plan.mode.value, "planner_type": plan.planner_type},
        context_summary=redact(request.planner_context.get("context", {})),
        available_tool_summaries=_tool_summaries(plan),
        skill_candidates=redact(plan.preflight.get("skill_match_refs", [])),
        mcp_candidates=redact(plan.preflight.get("mcp_tool_refs", [])),
        asset_handle_summaries=[
            {"handle_id": handle_id, "kind": "declared_resource_handle"}
            for handle_id in request.resource_handle_ids
        ],
        risk_policy_summary={
            "risk_level": plan.risk_level.value,
            "approval_strategy": redact(plan.approval_strategy),
            "forbidden_actions": _forbidden_actions(),
        },
        budget=plan.budget,
        success_criteria=plan.success_criteria,
        forbidden_actions=_forbidden_actions(),
        privacy_level=str(request.planner_context.get("privacy_level") or "medium"),
        planner_config=planner_config,
        trace_id=trace_id,
        created_at=created_at,
    )


def _candidate_from_model_output(
    *,
    task_id: str,
    request: TaskCreateRequest,
    plan: TaskPlan,
    raw: dict[str, Any] | str,
    trace_id: str | None,
    created_at: str,
    generation_id: str,
    latency_ms: int,
) -> tuple[PlanCandidate, dict[str, Any]]:
    payload = raw
    model_call: dict[str, Any] = {"latency_ms": latency_ms}
    if isinstance(raw, dict) and "candidate" in raw and isinstance(raw["candidate"], str):
        payload = raw["candidate"]
        model_call.update(
            {
                "usage": redact(raw.get("usage", {})),
                "finish_reason": raw.get("finish_reason"),
                "brain": redact(raw.get("brain", {})),
            }
        )
    parsed = _parse_model_output(payload)
    steps = parsed.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("model candidate must contain steps")
    return (
        PlanCandidate(
            candidate_id=new_id("plancand"),
            organization_id="org_default",
            task_id=task_id,
            planner_type="model_planner",
            source="model_assist",
            recommended_mode=str(parsed.get("recommended_mode") or plan.mode.value),
            steps=redact(steps),
            success_criteria=[
                str(item)
                for item in parsed.get("success_criteria", plan.success_criteria)
                if str(item).strip()
            ],
            assumptions=[str(item) for item in parsed.get("assumptions", []) if str(item).strip()],
            missing_information=[
                str(item) for item in parsed.get("missing_information", []) if str(item).strip()
            ],
            risk_hints=redact(parsed.get("risk_hints", [])),
            required_capabilities=[
                str(item)
                for item in parsed.get(
                    "required_capabilities",
                    _required_capabilities_from_steps(steps),
                )
                if str(item).strip()
            ],
            required_assets=[
                str(item)
                for item in parsed.get("required_assets", request.resource_handle_ids)
                if str(item).strip()
            ],
            confidence=float(parsed.get("confidence") or 0.0),
            reasoning_summary=str(
                redact(parsed.get("reasoning_summary") or "model assisted candidate")
            )[:500],
            status="candidate",
            model_assist={
                "enabled": True,
                "attempted": True,
                "generation_id": generation_id,
                "candidate_only": True,
                "latency_ms": latency_ms,
                "raw_payload_redacted": True,
            },
            trace_id=trace_id,
            created_at=created_at,
        ),
        model_call,
    )


def _failed_model_candidate(
    *,
    task_id: str,
    request: TaskCreateRequest,
    plan: TaskPlan,
    trace_id: str | None,
    created_at: str,
    generation_id: str,
    fallback_reason: str,
) -> PlanCandidate:
    del request
    return PlanCandidate(
        candidate_id=new_id("plancand"),
        organization_id="org_default",
        task_id=task_id,
        planner_type="model_planner",
        source="model_assist_failed",
        recommended_mode=plan.mode.value,
        steps=[],
        success_criteria=plan.success_criteria,
        assumptions=["模型候选生成失败，已回退到规则候选。"],
        missing_information=[fallback_reason],
        risk_hints=[],
        required_capabilities=[],
        required_assets=[],
        confidence=0.0,
        reasoning_summary=f"model planner fallback: {fallback_reason}",
        status="invalid",
        model_assist={
            "enabled": True,
            "attempted": True,
            "generation_id": generation_id,
            "fallback_used": True,
            "fallback_reason": fallback_reason,
        },
        trace_id=trace_id,
        created_at=created_at,
    )


def _select_candidate(items: list[CandidateEvidence]) -> CandidateEvidence:
    selectable = [
        item
        for item in items
        if item.verification.status == "passed"
        and item.final_steps
        and item.candidate.status != "invalid"
    ]
    pool = selectable or items
    return sorted(
        pool,
        key=lambda item: (
            item.quality_score.total_score,
            1 if item.candidate.source == "model_assist" else 0,
        ),
        reverse=True,
    )[0]


def _capability_candidates(
    *,
    task_id: str,
    plan: TaskPlan,
    trace_id: str | None,
    created_at: str,
) -> list[PlannerCapabilityCandidate]:
    snapshot = plan.preflight.get("capability_snapshot", {})
    candidates: list[PlannerCapabilityCandidate] = []
    for item in snapshot.get("skill_match_refs", []):
        metadata = {
            **redact(item),
            "ranking_factors": {
                "goal_match": float(item.get("confidence") or 0.0),
                "declared_permission_fit": True,
                "required_asset_fit": True,
                "eval_status": item.get("eval_status", "unknown"),
                "risk_level": "R2",
                "member_scope": "owner_member",
            },
        }
        candidates.append(
            PlannerCapabilityCandidate(
                capability_candidate_id=new_id("capcand"),
                organization_id="org_default",
                task_id=task_id,
                capability_type="skill",
                capability_id=item.get("skill_id"),
                name=item.get("skill_id"),
                match_score=float(item.get("confidence") or 0.0),
                risk_level=RiskLevel.R2,
                policy_status="available",
                reason_codes=[str(item.get("reason") or "skill_match"), "phase25_ranked"],
                metadata=metadata,
                trace_id=trace_id,
                created_at=created_at,
            )
        )
    for item in snapshot.get("mcp_tool_refs", []):
        metadata = {
            **redact(item),
            "untrusted_content_policy": "mark_untrusted",
            "ranking_factors": {
                "goal_match": 0.7,
                "server_ready": item.get("status") in {None, "active", "available"},
                "untrusted_content_risk": "template_only",
                "risk_level": "R2",
            },
        }
        candidates.append(
            PlannerCapabilityCandidate(
                capability_candidate_id=new_id("capcand"),
                organization_id="org_default",
                task_id=task_id,
                capability_type="mcp_tool",
                capability_id=item.get("mcp_tool_id"),
                name=item.get("tool_name"),
                match_score=0.7,
                risk_level=RiskLevel.R2,
                policy_status=str(item.get("status") or "available"),
                reason_codes=["mcp_ready_tool_candidate", "phase25_ranked"],
                metadata=metadata,
                trace_id=trace_id,
                created_at=created_at,
            )
        )
    for blocked in plan.preflight.get("blocked_actions", []):
        candidates.append(
            PlannerCapabilityCandidate(
                capability_candidate_id=new_id("capcand"),
                organization_id="org_default",
                task_id=task_id,
                capability_type=str(blocked.get("type") or "capability"),
                capability_id=blocked.get("skill_id") or blocked.get("tool_name"),
                name=blocked.get("skill_id") or blocked.get("tool_name"),
                match_score=0.0,
                risk_level=RiskLevel.R2,
                policy_status="unavailable",
                reason_codes=[
                    str(blocked.get("reason") or "capability_unavailable"),
                    "phase25_policy_preview_rejected",
                ],
                metadata={
                    **redact(blocked),
                    "ranking_factors": {
                        "policy_status": "unavailable",
                        "model_ranking_cannot_override_policy": True,
                    },
                },
                trace_id=trace_id,
                created_at=created_at,
            )
        )
    return sorted(candidates, key=lambda item: item.match_score, reverse=True)


def _prune(
    candidate: PlanCandidate,
    prune_type: str,
    *,
    original: dict[str, Any],
    pruned: dict[str, Any],
    reason_codes: list[str],
    trace_id: str | None,
    created_at: str,
) -> PlanPolicyPrune:
    return PlanPolicyPrune(
        prune_id=new_id("prune"),
        organization_id=candidate.organization_id,
        task_id=candidate.task_id,
        candidate_id=candidate.candidate_id,
        prune_type=prune_type,
        original_step=_redact_sensitive_planner_payload(original),
        pruned_step=_redact_sensitive_planner_payload(pruned),
        reason_codes=reason_codes,
        status="applied",
        trace_id=trace_id,
        created_at=created_at,
    )


def _redact_sensitive_planner_payload(value: Any) -> Any:
    redacted = redact(value)
    if isinstance(redacted, dict):
        cleaned = {
            key: _redact_sensitive_planner_payload(item) for key, item in redacted.items()
        }
        if cleaned.get("tool_name") == "terminal.run":
            args = cleaned.get("args")
            if isinstance(args, dict) and _dangerous_command(str(args.get("command") or "")):
                args["command"] = "[REDACTED_DANGEROUS_COMMAND]"
        step_input = cleaned.get("input")
        if isinstance(step_input, dict) and step_input.get("tool_name") == "terminal.run":
            args = step_input.get("args")
            if isinstance(args, dict) and _dangerous_command(str(args.get("command") or "")):
                args["command"] = "[REDACTED_DANGEROUS_COMMAND]"
        return cleaned
    if isinstance(redacted, list):
        return [_redact_sensitive_planner_payload(item) for item in redacted]
    if isinstance(redacted, str):
        lowered = redacted.lower()
        if any(marker in lowered for marker in _DANGEROUS_COMMAND_MARKERS):
            return "[REDACTED_DANGEROUS_COMMAND]"
    return redacted


def _contains_secret_or_sensitive_path(value: Any) -> bool:
    text = json.dumps(redact(value), ensure_ascii=False, default=str).lower()
    return any(marker in text for marker in _SECRET_MARKERS) or bool(
        _SENSITIVE_PATH_RE.search(text)
    )


def _dangerous_terminal_step(step: dict[str, Any]) -> bool:
    if step.get("step_type") != "tool_call":
        return False
    step_input = step.get("input", {})
    if step_input.get("tool_name") != "terminal.run":
        return False
    command = str(step_input.get("args", {}).get("command") or "").lower()
    return _dangerous_command(command)


def _dangerous_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _DANGEROUS_COMMAND_MARKERS)


def _failure_type(step: dict[str, Any] | None, failure_reason: str) -> str:
    code = str((step or {}).get("error_code") or failure_reason).lower()
    if "approval" in code:
        return "approval_required"
    if "safety" in code:
        return "safety_blocked"
    if "permission" in code or "capability" in code:
        return "permission_denied"
    if "mcp" in code:
        return "mcp_server_unready"
    if "skill" in code:
        return "skill_disabled"
    if "not_found" in code or "unavailable" in code:
        return "tool_unavailable"
    if "timeout" in code:
        return "timeout"
    if "budget" in code:
        return "budget_exhausted"
    return "invalid_output" if "invalid" in code else "tool_unavailable"


def _replan_trigger(
    *,
    step: dict[str, Any] | None,
    task_status: str,
    step_status: str | None,
    stop_reason: str | None,
    budget_snapshot: dict[str, Any],
) -> str:
    if stop_reason == "approval_required" or task_status == "waiting_approval":
        return "approval_required"
    if stop_reason == "blocked_by_safety":
        return "safety_blocked"
    if stop_reason == "budget_exhausted":
        return "budget_near_limit"
    if step is not None:
        failure = _failure_type(step, str(step.get("error_code") or stop_reason or ""))
        if failure == "mcp_server_unready":
            return "mcp_unready"
        if failure == "skill_disabled":
            return "skill_disabled"
        if failure in {"permission_denied", "safety_blocked"}:
            return failure
    try:
        if int(budget_snapshot.get("loop_steps") or 0) + 1 >= int(
            budget_snapshot.get("max_loop_steps") or 0
        ):
            return "budget_near_limit"
    except (TypeError, ValueError):
        pass
    if step_status == "failed":
        return "tool_failed"
    return "next_step"


def _risk_order(value: str) -> int:
    try:
        return int(str(value).removeprefix("R"))
    except ValueError:
        return 1


def _planner_config(
    routing_config: dict[str, Any],
    request_config: dict[str, Any] | None,
) -> dict[str, Any]:
    configured = {}
    try:
        configured = dict(routing_config.get("planner") or {})
    except AttributeError:
        configured = {}
    merged: dict[str, Any] = {
        "model_assist_mode": "auto",
        "max_model_calls_per_task": 2,
        "timeout_seconds": 30,
        "fallback": "rule_workflow_plan",
        "max_output_tokens": 1200,
    }
    merged.update(configured)
    if isinstance(request_config, dict):
        merged.update(request_config)
    try:
        merged["max_model_calls_per_task"] = int(merged["max_model_calls_per_task"])
        merged["timeout_seconds"] = int(merged["timeout_seconds"])
        merged["max_output_tokens"] = int(merged["max_output_tokens"])
    except (TypeError, ValueError):
        merged["max_model_calls_per_task"] = 2
        merged["timeout_seconds"] = 30
        merged["max_output_tokens"] = 1200
    if merged["model_assist_mode"] not in {"auto", "disabled"}:
        merged["model_assist_mode"] = "auto"
    return merged


def _allow_cloud(config: dict[str, Any], privacy_level: str) -> bool:
    if privacy_level == "high":
        return False
    try:
        return bool(config["routing"]["privacy"][privacy_level].get("allow_cloud", False))
    except (KeyError, AttributeError):
        return privacy_level != "high"


def _planner_messages(request: ModelPlanRequest) -> list[dict[str, str]]:
    payload = request.model_dump(mode="json")
    return [
        {
            "role": "system",
            "content": (
                "Return only compact JSON for a candidate task plan. Do not execute tools. "
                "Allowed step_type values: compose, tool_call, mcp_call, skill_run, skill_match. "
                "Do not include secrets, tokens, private keys, cookies, wallet seeds, "
                "or real local paths."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(redact(payload), ensure_ascii=False),
        },
    ]


def _parse_model_output(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = raw.get("candidate", raw)
        if isinstance(payload, str):
            return _parse_model_output(payload)
        if not isinstance(payload, dict):
            raise TypeError("model planner output must be an object")
        return payload
    text = _strip_reasoning_tags(raw)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TypeError("model planner output must be an object")
    return parsed.get("candidate", parsed)


def _strip_reasoning_tags(text: str) -> str:
    cleaned = text
    for start, end in [("<think>", "</think>"), ("<reasoning>", "</reasoning>")]:
        while start in cleaned and end in cleaned:
            before, rest = cleaned.split(start, 1)
            _, after = rest.split(end, 1)
            cleaned = before + after
    return cleaned.strip()


def _tool_summaries(plan: TaskPlan) -> list[dict[str, Any]]:
    summaries = []
    for step in plan.steps:
        if step.get("step_type") in {"tool_call", "mcp_call"}:
            step_input = step.get("input", {})
            summaries.append(
                {
                    "tool_name": step_input.get("tool_name"),
                    "step_type": step.get("step_type"),
                    "risk_level": step.get("risk_level", "R1"),
                    "args_summary": sorted((step_input.get("args") or {}).keys()),
                }
            )
    return redact(summaries)


def _forbidden_actions() -> list[str]:
    return [
        "direct_tool_execution",
        "direct_shell_execution",
        "secret_or_token_access",
        "approval_bypass",
        "policy_mutation",
        "raw_local_sensitive_path",
    ]


def _required_capabilities_from_steps(steps: list[dict[str, Any]]) -> list[str]:
    capabilities: list[str] = []
    for step in steps:
        step_type = step.get("step_type")
        if step_type == "tool_call":
            tool_name = step.get("input", {}).get("tool_name")
            capabilities.append(f"tool:{tool_name}" if tool_name else "tool")
        elif step_type == "mcp_call":
            tool_name = step.get("input", {}).get("tool_name")
            capabilities.append(f"mcp:{tool_name}" if tool_name else "mcp")
        elif step_type == "skill_run":
            skill_id = step.get("input", {}).get("skill_id")
            capabilities.append(f"skill:{skill_id}" if skill_id else "skill")
        elif step_type == "skill_match":
            capabilities.append("skill_match")
    return sorted(set(capabilities))


def _quality_terms(text: str) -> list[str]:
    lowered = text.lower()
    return [
        term
        for term in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
        if term.strip() and len(term.strip()) <= 24
    ][:16]


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
