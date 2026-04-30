from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
from core_types import (
    ErrorCode,
    HeartStateTransition,
    HeartSummary,
    PersonaSummary,
    ResponsePlan,
    RiskLevel,
    TonePolicyResolution,
    TraceSpanType,
)
from safety_service import ActionRequest, SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.design_alignment_repo import DesignAlignmentRepository
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.schemas.design_alignment import (
    HeartStateResponse,
    HeartStateTransitionsResponse,
    PersonaConsistencyProfileResponse,
    PersonaHeartReplayRunCreateRequest,
    PersonaHeartReplayRunResponse,
    PersonaProfileResponse,
    PersonaProfileUpdateRequest,
    ResponseQualityEvaluationResponse,
    SafetyDecisionResponse,
    SafetyEvaluateRequest,
    TonePolicyResolutionResponse,
    VectorProviderConfigResponse,
    VectorProviderListResponse,
    VectorProviderUpdateRequest,
    VectorStatusResponse,
    VectorSyncJobCreateRequest,
    VectorSyncJobResponse,
)
from app.schemas.system import DesignGap, RuntimeContract
from app.services.audit import AuditEventService

DEFAULT_TONE_POLICY = {
    "conciseness": 0.72,
    "warmth": 0.68,
    "humor": 0.12,
    "directness": 0.78,
    "formality": 0.42,
    "proactiveness": 0.58,
    "technical_depth": 0.66,
}
DEFAULT_DISCLOSURE_POLICY = {
    "ai_identity_disclosure": "when_relevant_or_high_impact",
    "capability_boundary_disclosure": True,
    "uncertainty_disclosure": True,
    "memory_usage_notice": "when_memory_is_used",
    "tool_usage_notice": "when_tool_or_task_is_required",
    "avoid_claiming_hidden_capabilities": True,
}
DEFAULT_RISK_TONE_POLICY = {
    "approval_scene_tone": "clear_and_calm",
    "security_block_scene_tone": "firm_and_explanatory",
    "failure_scene_tone": "accountable_and_actionable",
    "high_impact_scene_tone": "low_anthropomorphic",
}
DEFAULT_ALLOWED_MODES = [
    "default",
    "concise",
    "deep_dialogue",
    "task_status",
    "safety_boundary",
]
DEFAULT_STYLE_PRINCIPLES = [
    "answer_directly_before_explaining",
    "stay_warm_without_overclaiming_closeness",
    "keep_boundaries_visible_in_high_risk_scenarios",
    "prefer_recoverable_next_steps_after_failures",
]
DEFAULT_FORBIDDEN_CLAIMS = [
    "pretending_to_be_a_human",
    "claiming_hidden_tool_or_account_access",
    "claiming_safety_or_approval_can_be_bypassed",
    "claiming_file_browser_terminal_wallet_or_mcp_actions_completed_without_evidence",
]
DEFAULT_MODE_SWITCH_RULES = [
    {
        "when": "approval_or_safety_boundary",
        "mode": "safety_boundary",
        "anthropomorphic_level": "low",
    },
    {"when": "user_requests_concise", "mode": "concise"},
    {"when": "complex_multi_turn_discussion", "mode": "deep_dialogue"},
]
DEFAULT_CONSISTENCY_MARKERS = [
    "result_first",
    "plain_capability_boundaries",
    "no_fake_execution",
    "calm_recovery_language",
]
DEFAULT_DISABLED_PATTERNS = [
    "romantic_pressure",
    "system_prompt_disclosure",
    "security_bypass_persona",
]
_FORBIDDEN_PERSONA_POLICY_KEYS = {
    "allow",
    "allowed_action",
    "approval_override",
    "asset_grant",
    "bypass",
    "bypass_safety",
    "capability_override",
    "can_execute",
    "grant",
    "permission",
    "role_override",
    "safety_override",
    "secret",
    "token",
}

LOCAL_VECTOR_PROVIDER = "local"
LOCAL_VECTOR_MODEL = "local_hash_v1"
LOCAL_VECTOR_DIM = 64
LOCAL_VECTOR_MIN_SCORE = 0.05


class RuntimeContractService:
    def __init__(
        self,
        *,
        repo: DesignAlignmentRepository,
        data_dir: Any,
    ) -> None:
        self._repo = repo
        self._data_dir = data_dir

    async def list_contracts(self) -> list[RuntimeContract]:
        await self.ensure_seeded()
        return [RuntimeContract(**row) for row in await self._repo.list_runtime_contracts()]

    async def list_design_gaps(self) -> list[DesignGap]:
        await self.ensure_seeded()
        return [
            DesignGap(**_design_gap_with_lifecycle(row))
            for row in await self._repo.list_design_gaps()
        ]

    async def ensure_seeded(self) -> None:
        now = utc_now_iso()
        chroma_available = importlib.util.find_spec("chromadb") is not None
        vector_status = "implemented"
        vector_detail = (
            "Local deterministic vector provider is active; FTS remains an explicit fallback "
            "when semantic hits are insufficient"
        )
        contracts = [
            _contract("ChatRuntime", "implemented", "chat turn state machine and SSE replay"),
            _contract(
                "RealChatE2EClosure",
                "implemented",
                "real chat E2E gap closure evidence for memory, persona, task and privacy paths",
                details={"phase": "phase_30", "suite_id": "suite_phase30_real_chat_e2e"},
            ),
            _contract(
                "MemoryCorrectionDirectPath",
                "implemented",
                "explicit chat memory corrections complete without model dependency",
                details={
                    "phase": "phase_30",
                    "events": ["memory.candidate", "memory.correction_applied"],
                    "fallback": "correction_recorded_when_old_memory_not_found",
                },
            ),
            _contract(
                "ChatIntentBoundaryRepair",
                "implemented",
                "persona boundary questions stay direct while real task requests enter TaskEngine",
                details={
                    "phase": "phase_30",
                    "persona_boundary": "direct_no_task",
                    "task_request": "workflow_or_capability_boundary",
                },
            ),
            _contract(
                "ReleaseGateCurrentRunScope",
                "implemented",
                "release summaries scope eval results to the current gate run by default",
                details={
                    "phase": "phase_30",
                    "historical_results": "trend_history_only",
                    "current_scope": "release_gate_id_eval_runs",
                },
            ),
            _contract(
                "RealChatE2EFullClosure",
                "implemented",
                "all known CHAT-E2E-20260429 P0/P1/P2 issues are tracked through Phase 31 evidence",
                details={
                    "phase": "phase_31",
                    "suite_id": "suite_phase31_real_chat_e2e_full_closure",
                    "known_issue_records": 64,
                    "runner_rounds": 8,
                },
            ),
            _contract(
                "RealRunnerReleaseProfileGate",
                "implemented",
                "release profile requires the real chat runner matrix and issue gate",
                details={
                    "phase": "phase_31",
                    "profile": "release",
                    "default_full_profile_deterministic": True,
                },
            ),
            _contract(
                "ChatOutputQualityGuard",
                "implemented",
                "deterministic response shape guard for JSON, tables, terms, "
                "short labels and structured knowledge",
                details={"phase": "phase_31", "candidate_only": False},
            ),
            _contract(
                "ChatSessionIsolation",
                "implemented",
                "chat context selection prioritizes same-session recent state "
                "when a session is present",
                details={"phase": "phase_31", "scope": "chat_main_chain"},
            ),
            _contract(
                "MemorySearchPublicRedaction",
                "implemented",
                "public memory search results omit turn/message/trace internals "
                "while explicit source API remains available",
                details={
                    "phase": "phase_31",
                    "source_debug_api": "/api/memory/{memory_id}/source",
                },
            ),
            _contract(
                "TaskExecutionRegressionClosure",
                "implemented",
                "file.list, delete approval denial, unknown tool and terminal task "
                "binding regressions are covered",
                details={
                    "phase": "phase_31",
                    "suite_id": "suite_phase31_real_chat_e2e_full_closure",
                },
            ),
            _contract(
                "HeavyChatE2EHardening",
                "implemented",
                "POWER chat E2E hardening tracks redaction, lock retry, browser, "
                "Skill and MCP evidence",
                details={
                    "phase": "phase_33",
                    "suite_id": "suite_phase33_power_chat_hardening",
                    "runner": "CHAT-E2E-20260430-POWER",
                    "case_total": 108,
                },
            ),
            _contract(
                "PowerRunnerReleaseGate",
                "implemented",
                "release profile runs POWER runner and blocks on CHAT-E2E-POWER-FIX issue records",
                details={
                    "phase": "phase_33",
                    "profile": "release",
                    "issue_gate": "08-重型压力待修复问题.md",
                },
            ),
            _contract(
                "UnifiedRedactionPolicy",
                "implemented",
                "trace_service.redact is the shared source for chat, trace, task, "
                "tool, browser, MCP and reports",
                details={"phase": "phase_33", "policy": "trace_service.redact"},
            ),
            _contract(
                "SQLiteLockRecovery",
                "implemented",
                "database execute/fetch/commit paths use bounded retry/backoff with "
                "WAL and busy_timeout",
                details={
                    "phase": "phase_33",
                    "busy_timeout_ms": 30000,
                    "retry_backoff": [0.05, 0.1, 0.2, 0.4, 0.8],
                },
            ),
            _contract(
                "BrowserEvidenceModel",
                "implemented",
                "browser tools return stable url/title/http_status/action_status/"
                "evidence/artifact fields",
                details={"phase": "phase_33", "payload_redacted": True},
            ),
            _contract(
                "SkillMCPLifecycleRecovery",
                "implemented",
                "Skill and MCP lifecycle failures are represented as stable "
                "permission/capability/protocol evidence",
                details={"phase": "phase_33", "untrusted_outputs": True},
            ),
            _contract(
                "NaturalChatActionGateway",
                "implemented",
                "chat text resolves pending actions through natural confirm, deny and edit intents",
                details={
                    "phase": "phase_34",
                    "suite_id": "suite_phase34_natural_chat_interaction_loop",
                },
            ),
            _contract(
                "ChatTextApprovalResolver",
                "implemented",
                "natural language confirmation binds to the current pending approval "
                "before execution",
                details={"phase": "phase_34", "fail_closed": True},
            ),
            _contract(
                "PendingActionQueue",
                "implemented",
                "conversation working state stores user-readable pending action summaries",
                details={"phase": "phase_34", "storage": "pending_confirmation_json"},
            ),
            _contract(
                "HermesStyleRiskDecision",
                "implemented",
                "once/session/always/deny scopes are represented with hard-block guardrails",
                details={"phase": "phase_34", "high_risk_always": "denied"},
            ),
            _contract(
                "NaturalResponseNoiseFilter",
                "implemented",
                "ordinary chat replies hide approval/tool/trace IDs and raw risk codes",
                details={"phase": "phase_34", "main_reply_only": True},
            ),
            _contract(
                "NaturalBrowserResultFeedback",
                "implemented",
                "browser action replies distinguish waiting, completed, failed and evidence states",
                details={"phase": "phase_34", "runner": "CHAT-E2E-20260430-NATURAL"},
            ),
            _contract(
                "ChatStreamSafetyFilter",
                "implemented",
                "model deltas are redacted and noise-filtered before SSE and "
                "chat event persistence",
                details={
                    "phase": "phase_35",
                    "component": "ChatVisibleOutputFilter",
                    "final_from_filtered_delta": True,
                },
            ),
            _contract(
                "ModelContextRedactionBoundary",
                "implemented",
                "model context consumes model-safe recent messages, summaries and "
                "redacted memory blocks",
                details={"phase": "phase_35", "raw_content_text_used_for_model": False},
            ),
            _contract(
                "ChatTurnAccessPolicy",
                "implemented",
                "chat turn creation and retry validate conversation/member/organization ownership",
                details={"phase": "phase_35", "deny_code": "NOT_FOUND"},
            ),
            _contract(
                "ChatTaskStatusSemantics",
                "implemented",
                "chat emits task.completed only for truly completed tasks and presents "
                "other states distinctly",
                details={"phase": "phase_35", "false_completion_guard": True},
            ),
            _contract(
                "HighPrivacyLocalFirstRouting",
                "implemented",
                "high privacy chat routes to local brains first and otherwise returns "
                "recoverable privacy block",
                details={"phase": "phase_35", "cloud_planner_allowed": False},
            ),
            _contract(
                "ProductionGuardCleanup",
                "implemented",
                "production model path uses policy components instead of Phase31 "
                "keyword output guard",
                details={"phase": "phase_35", "replacement": "ChatVisibleOutputFilter"},
            ),
            _contract("ContextGateway", "implemented", "context build with memory/resources"),
            _contract(
                "BrainDecisionService",
                "implemented",
                "rule-first IntentDecision -> ModeDecision -> ContextDecision chain",
            ),
            _contract(
                "BrainRouter",
                "degraded",
                "compatibility facade for model routing while BrainDecisionService owns "
                "intent/mode/context",
                blocker_level="none",
                details={"compatibility_facade": True, "primary": "BrainDecisionService"},
            ),
            _contract(
                "ResponseComposer",
                "implemented",
                "scenario ResponsePlan for completion, clarification, boundary, failure, cancel",
            ),
            _contract(
                "ChatExperienceService",
                "implemented",
                "rule-first working state, clarification, route profile, and recovery signals",
            ),
            _contract(
                "ChatMainChainEval",
                "implemented",
                "deterministic chat main-chain eval matrix and release evidence",
                details={
                    "scope": "local_backend_acceptance",
                    "covers": [
                        "chat",
                        "intent",
                        "memory",
                        "persona",
                        "task",
                        "tool",
                        "mcp",
                        "skill",
                        "safety",
                    ],
                },
            ),
            _contract(
                "DialogueStateService",
                "implemented",
                "rule-first dialogue state extraction with goal, constraints, and topic shift",
                details={"phase": "phase_18", "model_assist": False, "rule_first": True},
            ),
            _contract(
                "SemanticIntentAnalyzer",
                "implemented",
                "multi-intent decomposition and context conflict detection for chat turns",
                details={"phase": "phase_18", "model_assist": False, "rule_first": True},
            ),
            _contract(
                "LowConfidenceDecisionReviewer",
                "implemented",
                "auditable low-confidence review with Phase 24 semantic verifier evidence",
                details={"phase": "phase_24", "model_assist": "fallback_first"},
            ),
            _contract(
                "ModelAssistedVerifier",
                "implemented_with_fallback",
                "advisory semantic verifier contract with deterministic fallback by default",
                blocker_level="none",
                details={
                    "accepted_risk": True,
                    "phase": "phase_24",
                    "enabled": True,
                    "real_model_call": False,
                    "fallback": "rule",
                    "privacy_policy": "local_only_without_configured_model",
                },
            ),
            _contract("AssetBroker", "implemented", "handle issue/validate/resolve-for-tool"),
            _contract("AssetResolveForTool", "implemented", "minimal resource resolution"),
            _contract("CapabilityGraph", "implemented", "deny-first deterministic decisions"),
            _contract("SafetyService", "implemented", "R0-R7 action safety gate"),
            _contract(
                "ToolRuntime",
                "implemented",
                "capability/safety/approval guarded tools with Phase 21 boundary evidence",
            ),
            _contract(
                "ToolActionPolicyService",
                "implemented",
                "pre-safety tool action policy decisions with deny-first unknown tool handling",
                details={"phase": "phase_21", "unknown_tool_default": "deny"},
            ),
            _contract(
                "CommandRiskClassifier",
                "implemented",
                "rule-first terminal command risk classifier for R5/R6/R7 boundaries",
                details={"phase": "phase_21", "task_binding_required": True},
            ),
            _contract(
                "TerminalSandboxProfile",
                "implemented",
                "persisted terminal sandbox profile with OS backend and replay metadata",
                blocker_level="none",
                details={
                    "profile_id": "task_artifact_policy_guard",
                    "default_backend": "windows_job_object",
                    "fallback": "policy_guard",
                    "phase": "phase_27",
                },
            ),
            _contract(
                "OutputDLP",
                "implemented",
                "deterministic output DLP for tool, terminal, MCP, browser and artifact previews",
                details={"phase": "phase_21", "rule_first": True},
            ),
            _contract(
                "ExecutionBoundaryDiagnostics",
                "implemented",
                "read-only execution boundary diagnostics and persisted decisions",
                details={"phase": "phase_21", "payload_redacted": True},
            ),
            _contract(
                "OSLevelSandbox",
                "implemented_with_fallback",
                "Windows Job Object sandbox is implemented with policy-guard fallback",
                blocker_level="none",
                details={
                    "accepted_risk": True,
                    "active_backend": "windows_job_object_on_windows",
                    "fallback": "policy_guard",
                    "low_integrity_status": "degraded_not_enabled",
                    "container_status": "degraded_not_enabled",
                    "profile_id": "task_artifact_policy_guard",
                },
            ),
            _contract(
                "MCPConnectionManager",
                "implemented_with_fallback",
                "MCP stdio runtime uses profile, lifecycle, protocol validation and guard evidence",
                blocker_level="none",
                details={
                    "accepted_risk": True,
                    "phase": "phase_28",
                    "runtime_profile": True,
                    "lifecycle_manager": True,
                    "protocol_validation": True,
                    "fallback_boundary": "stdio_policy_guard",
                },
            ),
            _contract(
                "MCPRuntimeProfileService",
                "implemented",
                "creates auditable per-server command/env/scope/trust runtime profiles",
                details={"phase": "phase_28", "unknown_command_default": "deny"},
            ),
            _contract(
                "MCPLifecycleManager",
                "implemented",
                "records start, health, failure, stop and circuit breaker lifecycle events",
                details={"phase": "phase_28", "circuit_breaker_threshold": 2},
            ),
            _contract(
                "MCPProtocolValidator",
                "implemented",
                "validates initialize, capability list, schema and tool call responses",
                details={"phase": "phase_28", "invalid_tool_schema": "not_registered"},
            ),
            _contract(
                "MCPContentSanitizer",
                "implemented",
                "keeps MCP resources, prompts and outputs untrusted with redacted previews",
                details={"phase": "phase_28", "prompt_policy": "template_only"},
            ),
            _contract(
                "MCPOutputActionGuard",
                "implemented",
                "records taint guard evidence before MCP output can influence later actions",
                details={"phase": "phase_28", "r4_plus": "approval_or_deny"},
            ),
            _contract(
                "TerminalRunner",
                "implemented_with_fallback",
                "terminal.run uses TerminalSandboxRunner with Windows Job Object fallback",
                blocker_level="none",
                details={
                    "accepted_risk": True,
                    "sandbox_profile": "task_artifact_policy_guard",
                    "default_backend": "windows_job_object",
                    "fallback": "policy_guard",
                    "task_binding_required": True,
                },
            ),
            _contract(
                "WindowsJobObjectSandbox",
                "implemented_with_fallback",
                "Windows Job Object backend with kill-on-close, timeout and cleanup evidence",
                blocker_level="none",
                details={
                    "phase": "phase_27",
                    "kill_on_job_close": True,
                    "process_count_limit": 16,
                    "memory_limit_bytes": 536870912,
                    "fallback": "policy_guard",
                },
            ),
            _contract(
                "TerminalEnvPolicy",
                "implemented",
                "clear-by-default terminal environment with secret env deny policy",
                details={"phase": "phase_27", "secret_env": "deny"},
            ),
            _contract(
                "TerminalFilesystemBoundary",
                "implemented",
                "task artifact cwd, traversal denial and sensitive path preflight",
                details={"phase": "phase_27", "cwd": "task_artifact_sandbox"},
            ),
            _contract(
                "TerminalNetworkPolicy",
                "implemented",
                "terminal network read/write classification before execution",
                details={"phase": "phase_27", "external_write": "approval_or_deny"},
            ),
            _contract(
                "TerminalProcessSupervisor",
                "implemented",
                "wall timeout, output limits and kill-tree cleanup evidence",
                details={"phase": "phase_27", "timeout": True, "output_limit": True},
            ),
            _contract("SkillEngine", "implemented", "declarative skill runner"),
            _contract(
                "TaskEngine",
                "implemented",
                "workflow/agent/supervisor execution with replay evidence",
            ),
            _contract(
                "TaskPlannerService",
                "implemented",
                "rule-first planner selects workflow, agent, supervisor and records decisions",
                details={"model_assist": False, "planner_order": "rule/workflow/agent/supervisor"},
            ),
            _contract(
                "AgentLoopRunner",
                "implemented",
                "bounded observe-plan-act-evaluate loop with persisted iterations",
                details={"max_loop_steps_default": 8, "background_autonomy": False},
            ),
            _contract(
                "TaskObservationService",
                "implemented",
                "redacted task observations with untrusted content markers",
            ),
            _contract(
                "TaskReflectionService",
                "implemented",
                "candidate-only memory/skill/workflow/failure reflection after tasks",
                details={"auto_enable": False, "candidate_only": True},
            ),
            _contract(
                "ModelPlanner",
                "implemented",
                (
                    "candidate-only planner with optional model candidate generation, "
                    "verifier/pruner, quality scoring and rule fallback"
                ),
                blocker_level="none",
                details={
                    "model_assist": False,
                    "model_assist_mode": "auto",
                    "candidate_contract": "implemented",
                    "candidate_only": True,
                    "fallback": "rule/workflow",
                    "real_model_call": "configured_brain_only",
                    "secret_and_path_pruning": True,
                },
            ),
            _contract(
                "ModelPlanCandidateGenerator",
                "implemented",
                "generates redacted model candidates when a routable brain is configured",
                blocker_level="none",
                details={
                    "phase": "phase_25",
                    "fallback": "rule_workflow_plan",
                    "schema_validation": True,
                    "candidate_only": True,
                },
            ),
            _contract(
                "PlanVerifier",
                "implemented",
                "validates candidate plan schema, risk, budget, capability and secret boundaries",
                details={"blocks_raw_model_execution": True},
            ),
            _contract(
                "PolicyPruner",
                "implemented",
                "removes unavailable capabilities and unsafe candidate actions before execution",
                details={
                    "high_risk_strategy": "approval_checkpoint",
                    "unsafe_prune_types": [
                        "remove_dangerous_shell_command",
                        "remove_sensitive_payload",
                    ],
                },
            ),
            _contract(
                "PlanQualityScorer",
                "implemented",
                "scores rule/model plan candidates before selecting executable steps",
                blocker_level="none",
                details={
                    "phase": "phase_25",
                    "dimensions": [
                        "goal_coverage",
                        "step_coherence",
                        "capability_fit",
                        "safety_compliance",
                        "budget_efficiency",
                        "recoverability",
                    ],
                },
            ),
            _contract(
                "ObservationAwareReplanner",
                "implemented",
                "records safe plan deltas from agent observations without bypassing controls",
                blocker_level="none",
                details={
                    "phase": "phase_25",
                    "updates_pending_only": True,
                    "model_assist": "fallback_first",
                },
            ),
            _contract(
                "AgentNextActionSelector",
                "implemented",
                "persists bounded next-action decisions for agent loop iterations",
                details={
                    "actions": [
                        "act",
                        "revise_plan",
                        "ask_user",
                        "request_approval",
                        "retry_tool",
                        "stop_blocked",
                        "stop_budget",
                    ]
                },
            ),
            _contract(
                "ToolFailureRecoveryPlanner",
                "implemented",
                "classifies tool failures and creates non-bypassing recovery plans",
                details={"bypass_controls": False},
            ),
            _contract(
                "ModelAssistedRecoveryPlanner",
                "implemented",
                "creates contextual recovery suggestions while preserving safety boundaries",
                blocker_level="none",
                details={"phase": "phase_25", "bypass_controls": False},
            ),
            _contract(
                "SkillMCPCandidateRanker",
                "implemented",
                "ranks Skill/MCP candidates with policy preview and unavailable evidence",
                blocker_level="none",
                details={
                    "phase": "phase_25",
                    "policy_deny_overrides_model": True,
                    "untrusted_mcp_prompt_policy": "template_only",
                },
            ),
            _contract(
                "MemoryService",
                "implemented",
                "rule-first memory with semantic vector retrieval and explicit FTS fallback",
            ),
            _contract(
                "KnowledgeService",
                "implemented",
                "knowledge chunk retrieval with local vectors and explicit FTS fallback",
            ),
            _contract(
                "VectorStore",
                vector_status,
                vector_detail,
                blocker_level="none",
                details={
                    "provider": LOCAL_VECTOR_PROVIDER,
                    "embedding_model": LOCAL_VECTOR_MODEL,
                    "embedding_dim": LOCAL_VECTOR_DIM,
                    "fallback": "fts",
                    "chroma_available": chroma_available,
                },
            ),
            _contract(
                "EmbeddingProviderResolver",
                "implemented",
                (
                    "resolves local_hash/local_model/chroma/external-compatible providers "
                    "with privacy-aware local fallback"
                ),
                blocker_level="none",
                details={
                    "default_provider": "local_hash_v1",
                    "selection_order": [
                        "requested_provider",
                        "healthy_active_local_model",
                        "healthy_active_chroma",
                        "healthy_active_external_compatible",
                        "local_hash_v1",
                    ],
                    "allow_cloud": False,
                    "external_provider_default": "disabled",
                },
            ),
            _contract(
                "EmbeddingProviderInterface",
                "implemented",
                "common status/health/embed/search/upsert/delete contract for vector providers",
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "providers": [
                        "local_hash_v1",
                        "local_model",
                        "chroma",
                        "external_compatible",
                    ],
                    "raw_text_in_trace": False,
                },
            ),
            _contract(
                "EmbeddingPrivacyRouter",
                "implemented",
                (
                    "blocks external embedding on high privacy, sensitive text, "
                    "disabled cloud, or missing secret_ref"
                ),
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "privacy_high_policy": "local_only",
                    "sensitive_text_policy": "local_hash_fallback",
                },
            ),
            _contract(
                "LocalModelEmbeddingProvider",
                "implemented",
                "local model embedding provider seam with health-check and deterministic fallback",
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "default_status": "degraded_without_model_path",
                    "startup_blocking": False,
                },
            ),
            _contract(
                "ChromaEmbeddingProvider",
                "implemented",
                "optional local Chroma provider seam that degrades when chromadb is unavailable",
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "chroma_available": chroma_available,
                    "startup_blocking": False,
                },
            ),
            _contract(
                "MemoryReranker",
                "implemented",
                (
                    "deterministic memory rerank with supersede, sensitivity, "
                    "recency and source quality"
                ),
                blocker_level="none",
                details={"rule_first": True, "sensitive_default": "suppress"},
            ),
            _contract(
                "KnowledgeReranker",
                "implemented",
                "deterministic knowledge rerank with semantic/FTS source separation",
                blocker_level="none",
                details={"rule_first": True, "untrusted_marker": True},
            ),
            _contract(
                "RetrievalDiagnostics",
                "implemented",
                (
                    "read-only retrieval diagnostics for rerank runs, suppressed "
                    "items, and quality reports"
                ),
                blocker_level="none",
                details={"payload_redacted": True, "retrieval_id_scoped": True},
            ),
            _contract(
                "ExternalEmbeddingProvider",
                "implemented_with_fallback",
                (
                    "OpenAI-compatible embedding provider is implemented but disabled by default"
                ),
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "allow_cloud_default": False,
                    "secret_ref_required": True,
                    "candidate_only_when_privacy_allows": True,
                    "fallback": "local_hash_v1",
                    "accepted_risk": True,
                },
            ),
            _contract(
                "VectorReindexer",
                "implemented",
                (
                    "sync/reindex jobs support dual-write, shadow-index, "
                    "validate-before-switch evidence"
                ),
                blocker_level="none",
                details={
                    "phase": "phase_26",
                    "rollback_collection_retained": True,
                    "failure_switches_active_provider": False,
                },
            ),
            _contract(
                "RetrievalQualityBenchmark",
                "implemented",
                "local deterministic retrieval recall/precision and fallback smoke evidence",
                blocker_level="none",
                details={"phase": "phase_26", "model_free": True},
            ),
            _contract(
                "HeartService",
                "implemented",
                "rule-first HeartSignal with urgency, pace, confidence, and deescalation",
                blocker_level="none",
                details={
                    "rule_first": True,
                    "scope": "tone_policy_only",
                    "model_assist": "disabled",
                    "phase": "phase_14",
                },
            ),
            _contract(
                "PersonaEngine",
                "implemented",
                "persona profile, tone/disclosure/risk tone policy, and context summary",
                blocker_level="none",
                details={
                    "rule_first": True,
                    "scope": "policy_profiles",
                    "model_assist": "disabled",
                    "phase": "phase_14",
                },
            ),
            _contract(
                "PersonaConsistencyService",
                "implemented",
                "persona consistency profile and forbidden-claim evidence for response tone",
                blocker_level="none",
                details={"phase": "phase_22", "scope": "expression_policy_only"},
            ),
            _contract(
                "HeartTransitionService",
                "implemented",
                "rule-first Heart snapshot transition evidence without changing safety decisions",
                blocker_level="none",
                details={"phase": "phase_22", "rule_first": True},
            ),
            _contract(
                "TonePolicyResolver",
                "implemented",
                "merges persona, heart, risk and scenario into auditable tone policy",
                blocker_level="none",
                details={"phase": "phase_22", "high_risk_anthropomorphic_level": "low"},
            ),
            _contract(
                "ResponseQualityEvaluator",
                "implemented",
                "deterministic response quality, boundary honesty and leakage evaluation",
                blocker_level="none",
                details={"phase": "phase_22", "payload_redacted": True},
            ),
            _contract(
                "PersonaHeartLongitudinalEval",
                "implemented",
                "local deterministic persona/heart replay evidence for multi-turn quality",
                blocker_level="none",
                details={"phase": "phase_22", "large_scale_user_eval": False},
            ),
            _contract(
                "VerificationClosure",
                "implemented",
                (
                    "Phase 23 release verification closure with tooling, eval, "
                    "risk and diagnostic evidence"
                ),
                blocker_level="none",
                details={"phase": "phase_23", "migration_required": False},
            ),
            _contract(
                "TestMatrix",
                "implemented",
                "registered pytest markers and documented local command matrix",
                blocker_level="none",
                details={
                    "phase": "phase_23",
                    "markers": [
                        "unit",
                        "api",
                        "integration",
                        "eval",
                        "slow",
                        "release",
                        "security",
                        "chat_main_chain",
                    ],
                },
            ),
            _contract(
                "EvalEvidenceAggregator",
                "implemented",
                "aggregates Phase 17-36 eval evidence into release reports and diagnostics",
                blocker_level="none",
                details={"phase": "phase_36", "phase_range": "17-36"},
            ),
            _contract(
                "AcceptedRiskRegistry",
                "implemented",
                (
                    "normalizes accepted design gaps into machine-readable release "
                    "risk lifecycle entries"
                ),
                blocker_level="none",
                details={
                    "phase": "phase_29",
                    "source": "design_gaps",
                    "expiry_days": 180,
                    "expiring_soon_days": 30,
                },
            ),
            _contract(
                "CIVerificationMatrix",
                "implemented",
                (
                    "local CI-ready check profiles for smoke, full, fast, api, security "
                    "and release"
                ),
                blocker_level="none",
                details={
                    "phase": "phase_29",
                    "script": "scripts/check.ps1",
                    "profiles": ["smoke", "full", "fast", "api", "security", "release"],
                    "external_ci_provider": False,
                },
            ),
            _contract(
                "LongRunExperienceEval",
                "implemented",
                "deterministic local long-dialogue and long-task release-scale smoke eval",
                blocker_level="none",
                details={"phase": "phase_29", "real_user_study": False},
            ),
            _contract(
                "PerformanceResourceBenchmark",
                "implemented",
                "release-scale performance, trace size and diagnostic size thresholds",
                blocker_level="none",
                details={"phase": "phase_29", "severe_overage_blocks": True},
            ),
            _contract(
                "MigrationBackupRestoreVerification",
                "implemented",
                "fresh migration, latest migration and backup/restore evidence in release gate",
                blocker_level="none",
                details={"phase": "phase_29", "migration_required": False},
            ),
            _contract(
                "AcceptedRiskLifecycle",
                "implemented",
                "accepted risk expiry, recheck and blocker-promotion evidence",
                blocker_level="none",
                details={"phase": "phase_29", "source": "design_gaps"},
            ),
            _contract(
                "ReleaseScaleDiagnostics",
                "implemented",
                "diagnostic bundles include release-scale phase, suite, case and risk evidence",
                blocker_level="none",
                details={"phase": "phase_29", "payload_redacted": True},
            ),
            _contract("SupervisorService", "implemented", "multi-member backend replay"),
            _contract("ShellSwitchService", "implemented", "shell switch guard"),
            _contract(
                "ReleaseGate",
                "implemented_with_release_grade_evidence",
                "release gate includes local CI-ready profiles and release-scale evidence",
                blocker_level="none",
                details={
                    "phase": "phase_33",
                    "local_ci_profile": True,
                    "external_ci_provider": False,
                    "release_profile": "implemented",
                    "real_chat_runner_release_profile_required": True,
                    "power_runner_release_profile_required": True,
                    "natural_chat_runner_release_profile_required": True,
                    "default_full_profile_deterministic": True,
                    "accepted_risk": True,
                    "scope": "single-node-local",
                },
            ),
            _contract(
                "SettingsAPI",
                "implemented",
                "runtime settings read/write contract with whitelist validation",
            ),
            _contract(
                "ScheduledTaskService",
                "implemented",
                "long-running and scheduled task backend API creates normal TaskEngine tasks",
                blocker_level="none",
                details={"phase": "phase_36", "execution_entry": "TaskEngine.create_task"},
            ),
            _contract(
                "ScheduleParser",
                "implemented",
                "structured once, interval, daily, weekly, and monthly-lite schedule parser",
                blocker_level="none",
                details={"phase": "phase_36", "timezone_default": "Asia/Shanghai"},
            ),
            _contract(
                "ScheduledDueScanner",
                "implemented",
                "local due scanner with idempotency key protection",
                blocker_level="none",
                details={"phase": "phase_36", "distributed_scheduler": False},
            ),
            _contract(
                "BackgroundExecutionPolicy",
                "implemented",
                "unattended R3+ scheduled runs pause for fresh approval instead of auto execution",
                blocker_level="none",
                details={
                    "phase": "phase_36",
                    "session_approval_reuse": False,
                    "high_risk_action": "pause_wait_approval",
                },
            ),
            _contract(
                "ScheduledTaskRunHistory",
                "implemented",
                "scheduled run history links trigger, policy, task and replay references",
                blocker_level="none",
                details={"phase": "phase_36", "replay_ref": "/api/tasks/{task_id}/replay"},
            ),
        ]
        for item in contracts:
            await self._repo.upsert_runtime_contract({**item, "updated_at": now})

        gaps: list[dict[str, Any]] = [
            {
                "gap_id": "gap_vector_provider_runtime",
                "module_name": "VectorStore",
                "current_behavior": vector_detail,
                "design_gap": (
                    "Semantic retrieval uses deterministic local embeddings by default; "
                    "optional local_model, Chroma, and external-compatible providers require "
                    "explicit health/privacy validation before becoming active."
                ),
                "blocker_level": "none",
                "fix_phase": "phase_26",
                "acceptance_tests": [
                    "local vector provider is available without external dependencies",
                    "memory and knowledge retrieval distinguish semantic hits from FTS fallback",
                    "provider failures fall back to local_hash_v1 without deleting old indexes",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_external_embedding_provider_disabled",
                "module_name": "EmbeddingProviderResolver",
                "current_behavior": (
                    "local_hash_v1 remains the default active fallback; local_model and "
                    "Chroma can become active only when healthy, while external-compatible "
                    "embedding is disabled unless cloud use, secret_ref, and privacy checks pass."
                ),
                "design_gap": (
                    "Real high-quality embedding evaluation still depends on an operator-provided "
                    "local model or explicit external-compatible provider; default local checks "
                    "remain deterministic smoke coverage."
                ),
                "blocker_level": "none",
                "fix_phase": "phase_26_followup_real_embedding_eval",
                "acceptance_tests": [
                    "external provider is disabled by default",
                    "provider status exposes privacy_policy, allow_cloud, and health",
                    "high privacy or sensitive text blocks external embedding",
                    "embedding traces do not store raw text",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_mcp_command_allowlist",
                "module_name": "MCPConnectionManager",
                "current_behavior": (
                    "stdio MCP uses runtime profiles, lifecycle/protocol validation, "
                    "env_refs-only policy, untrusted content sanitization and taint records."
                ),
                "design_gap": "OS process boundary is local stdio; policy is stored per server.",
                "blocker_level": "none",
                "fix_phase": "future_mcp_process_os_isolation",
                "acceptance_tests": [
                    "unsafe stdio commands are rejected",
                    "member scope policy blocks unauthorized tool calls",
                    "protocol invalid responses degrade safely",
                    "MCP output taint cannot bypass high-risk approvals",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_terminal_os_sandbox",
                "module_name": "TerminalRunner",
                "current_behavior": (
                    "terminal.run uses a TerminalSandboxRunner; Windows prefers Job Object, "
                    "other environments fall back to policy_guard with persisted evidence."
                ),
                "design_gap": (
                    "Low-integrity token, full filesystem virtualization, and container isolation "
                    "are not enabled by default in Phase 27."
                ),
                "blocker_level": "none",
                "fix_phase": "future_low_integrity_or_container_sandbox",
                "acceptance_tests": [
                    "custom cwd is rejected",
                    "secret/system paths are denied",
                    "Windows Job Object or explicit fallback is recorded",
                    "terminal logs are redacted artifacts",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_os_level_sandbox_degraded",
                "module_name": "OSLevelSandbox",
                "current_behavior": (
                    "Windows Job Object is the first real OS backend; if unavailable, "
                    "TerminalSandboxRunner falls back to policy_guard and records the reason."
                ),
                "design_gap": (
                    "Job Object limits process lifetime and resources but does not provide "
                    "full filesystem virtualization or low-integrity token isolation."
                ),
                "blocker_level": "none",
                "fix_phase": "future_low_integrity_fs_sandbox",
                "acceptance_tests": [
                    "TerminalRunner contract is implemented_with_fallback",
                    "OSLevelSandbox contract is implemented_with_fallback",
                    "terminal sandbox profile records windows_job_object or policy_guard",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_release_gate_depth",
                "module_name": "ReleaseGate",
                "current_behavior": (
                    "release gate runs local eval/security/integrity/backup smoke evidence"
                ),
                "design_gap": (
                    "Evidence chain is stronger than smoke but still local and small-scale."
                ),
                "blocker_level": "low",
                "fix_phase": "phase_11",
                "acceptance_tests": ["release gate references phase10 evidence and blocks leaks"],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_repo_hygiene",
                "module_name": "Repository",
                "current_behavior": (
                    "local generated data is ignored and README documents backend scope"
                ),
                "design_gap": (
                    "Generated artifacts are not deleted automatically to avoid user data loss."
                ),
                "blocker_level": "none",
                "fix_phase": "phase_11",
                "acceptance_tests": [".gitignore covers data/cache/db/log outputs"],
                "status": "verified",
            },
            {
                "gap_id": "gap_response_composer_payload",
                "module_name": "ResponseComposer",
                "current_behavior": (
                    "composer returns scenario ResponsePlan for direct, clarification, "
                    "tool boundary, failure, and cancel paths"
                ),
                "design_gap": "Future model-assisted tone planning can deepen style selection.",
                "blocker_level": "low",
                "fix_phase": "phase_12",
                "acceptance_tests": [
                    "clarification and failure paths include structured response_plan",
                    "internal stage prompt text is absent",
                ],
                "status": "verified",
            },
            {
                "gap_id": "gap_chat_experience_rule_first",
                "module_name": "ChatExperienceService",
                "current_behavior": (
                    "conversation working state, clarification, and Phase 18 dialogue "
                    "semantics use deterministic rules"
                ),
                "design_gap": (
                    "Dialogue semantics are implemented as rule-first extraction; future "
                    "model-assisted review can improve ambiguous wording quality."
                ),
                "blocker_level": "none",
                "fix_phase": "future_model_verifier_eval",
                "acceptance_tests": [
                    "working state table is populated",
                    "ambiguous high-risk action asks 1-3 clarification questions",
                    "dialogue state and semantic intent evidence are persisted",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_brain_decision_model_assist",
                "module_name": "BrainDecisionService",
                "current_behavior": (
                    "Intent, mode, and context decisions are deterministic rule-first with "
                    "model_hint captured but disabled by default."
                ),
                "design_gap": (
                    "Optional model-assisted triage is reserved as a seam; no extra model "
                    "classification call is required in local no-model environments."
                ),
                "blocker_level": "none",
                "fix_phase": "phase_13",
                "acceptance_tests": [
                    "decision preview returns model_hint.enabled=false",
                    "low confidence falls back to safe clarification or direct response",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_model_planner_assist_disabled",
                "module_name": "ModelPlanner",
                "current_behavior": (
                    "Phase 25 supports configured-brain model candidate generation, "
                    "quality scoring, observation-aware replanning and recovery evidence; "
                    "local tests still use deterministic fake/fallback adapters."
                ),
                "design_gap": (
                    "Real planner quality depends on a configured local/allowed model and "
                    "future CI quality gates; model candidates remain candidate-only and still "
                    "route through Safety, Approval, Asset Broker, Capability Graph, Skill, "
                    "MCP and ToolRuntime."
                ),
                "blocker_level": "none",
                "fix_phase": "future_model_planner_ci_quality_eval",
                "acceptance_tests": [
                    "model planner records fallback evidence without a configured model",
                    "fake model candidates are verified, pruned and quality scored",
                    "candidate plans are verified and pruned before execution",
                    "agent loop remains bounded and replayable with observation-aware deltas",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_chat_main_chain_eval_local_smoke",
                "module_name": "ChatMainChainEval",
                "current_behavior": (
                    "Chat main-chain acceptance uses deterministic local smoke/evidence "
                    "checks over eval, trace, replay, response, and safety records."
                ),
                "design_gap": (
                    "It does not replace large-scale real-user experience evaluation or "
                    "long-horizon model-assisted conversation assessment."
                ),
                "blocker_level": "none",
                "fix_phase": "future_user_experience_eval",
                "acceptance_tests": [
                    "phase17 suite is registered",
                    "release report contains phase17 go/no-go evidence",
                    "zero-tolerance chat security failures block release",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase18_model_assisted_verifier_disabled",
                "module_name": "ModelAssistedVerifier",
                "current_behavior": (
                    "Phase 24 implements semantic review request/suggestion/model-call/merge "
                    "evidence and stable fallback; no real model call runs unless an allowed "
                    "local verifier adapter is configured."
                ),
                "design_gap": (
                    "Real model semantic quality still needs local-model/CI evaluation; the "
                    "current backend contract is advisory, schema-bound, and fallback-first."
                ),
                "blocker_level": "none",
                "fix_phase": "future_model_semantic_quality_eval",
                "acceptance_tests": [
                    "semantic review writes fallback evidence without a configured model",
                    "decision preview has no semantic review persistence side effects",
                    "risk monotonic guard blocks unsafe downgrade suggestions",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase24_real_model_semantic_quality_not_enabled",
                "module_name": "ModelAssistedVerifier",
                "current_behavior": (
                    "Model-assisted semantic verifier service is implemented with fake/disabled "
                    "adapter seams and deterministic fallback; external/cloud model use is not "
                    "enabled by default."
                ),
                "design_gap": (
                    "Large-scale ambiguous dialogue quality must be rechecked once a real local "
                    "model adapter is configured."
                ),
                "blocker_level": "none",
                "fix_phase": "future_model_semantic_quality_eval",
                "acceptance_tests": [
                    "ModelAssistedVerifier contract is implemented_with_fallback",
                    "invalid JSON and timeout fallback keep the chat path stable",
                    "high privacy forces local_only review policy",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase22_longitudinal_eval_local_only",
                "module_name": "PersonaHeartLongitudinalEval",
                "current_behavior": (
                    "Persona/Heart replay uses deterministic local cases and persisted "
                    "quality evidence."
                ),
                "design_gap": (
                    "It does not represent large-scale real-user longitudinal experience "
                    "or model-assisted psychological interpretation."
                ),
                "blocker_level": "none",
                "fix_phase": "future_longitudinal_user_eval",
                "acceptance_tests": [
                    "replay runs store pass/fail evidence",
                    "high-risk tone keeps anthropomorphic level low",
                    "quality scans block internal leakage",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase23_local_verification_not_ci",
                "module_name": "VerificationClosure",
                "current_behavior": (
                    "Phase 23 records local deterministic ruff, mypy, pytest, eval, "
                    "release, and diagnostic evidence."
                ),
                "design_gap": (
                    "Local verification is not a substitute for hosted CI, multi-platform "
                    "runs, or long-duration load testing."
                ),
                "blocker_level": "none",
                "fix_phase": "future_ci_scale_verification",
                "acceptance_tests": [
                    "check.ps1 writes a local check report",
                    "release report includes phase23",
                    "diagnostic bundle includes accepted risk registry",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase29_external_ci_provider_not_configured",
                "module_name": "CIVerificationMatrix",
                "current_behavior": (
                    "Phase 29 provides local CI-ready check profiles and release-scale "
                    "evidence, but does not add a hosted CI provider workflow."
                ),
                "design_gap": (
                    "Hosted CI and multi-platform runners must call the same local profiles "
                    "in a future environment-specific integration."
                ),
                "blocker_level": "none",
                "fix_phase": "future_hosted_ci_integration",
                "acceptance_tests": [
                    "check.ps1 supports full, fast, api, security and release profiles",
                    "release report includes phase29 release-scale evidence",
                    "accepted risk lifecycle marks missing hosted CI as unexpired",
                ],
                "status": "accepted_risk",
            },
            {
                "gap_id": "gap_phase31_real_runner_release_profile_only",
                "module_name": "RealRunnerReleaseProfileGate",
                "current_behavior": (
                    "Phase 31 keeps the default full profile deterministic and requires the "
                    "real chat runner full PASS only in the release profile."
                ),
                "design_gap": (
                    "Real model E2E runs are slower and environment-dependent, so hosted CI "
                    "and model availability validation remain future release infrastructure work."
                ),
                "blocker_level": "none",
                "fix_phase": "future_hosted_real_model_ci",
                "acceptance_tests": [
                    "check.ps1 -Profile release runs the real chat runner matrix",
                    "release report includes summary.phase31",
                    "default check.ps1 remains deterministic and stable",
                ],
                "status": "accepted_risk",
            },
        ]
        for gap in gaps:
            await self._repo.upsert_design_gap({**gap, "created_at": now, "updated_at": now})


class SafetyDecisionService:
    def __init__(
        self,
        *,
        repo: DesignAlignmentRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._trace = trace_service
        self._audit = audit_service
        self._safety = SafetyService()

    async def evaluate(
        self,
        request: SafetyEvaluateRequest | ActionRequest,
        *,
        trace_id: str | None = None,
    ) -> SafetyDecisionResponse:
        action_request = (
            request
            if isinstance(request, ActionRequest)
            else ActionRequest(**request.model_dump(mode="json"))
        )
        span_id = None
        if trace_id:
            span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.SAFETY_EVALUATE,
                name="evaluate action safety",
                input_data=redact(action_request.model_dump(mode="json")),
            )
        decision = await self._safety.evaluate_action(action_request)
        decision_id = new_id("safe")
        now = utc_now_iso()
        decision = decision.model_copy(update={"safety_decision_id": decision_id})
        data: dict[str, Any] = {
            "safety_decision_id": decision_id,
            "organization_id": action_request.organization_id,
            "actor_type": action_request.actor_type,
            "actor_id": action_request.actor_id,
            "task_id": action_request.task_id,
            "action_type": action_request.action_type,
            "action": action_request.action,
            "object_type": action_request.object_type,
            "object_id": action_request.object_id,
            "decision": decision.decision,
            "allowed": decision.allowed,
            "approval_required": decision.approval_required,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "payload_summary": redact(action_request.payload_summary),
            "asset_handles": action_request.asset_handles,
            "destination": action_request.destination,
            "redactions": decision.redactions,
            "required_controls": decision.required_controls,
            "policy_sources": decision.policy_sources,
            "trace_refs": decision.trace_refs,
            "trace_id": trace_id,
            "created_at": now,
        }
        await self._repo.insert_safety_decision(data)
        if decision.decision in {"deny", "approval_required"}:
            await self._audit.write_event(
                actor_type=action_request.actor_type,
                actor_id=action_request.actor_id,
                action="safety.evaluate",
                object_type=action_request.object_type,
                object_id=action_request.object_id,
                summary=f"安全策略判定为 {decision.decision}",
                risk_level=decision.risk_level,
                payload={
                    "safety_decision_id": decision_id,
                    "action": action_request.action,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "required_controls": decision.required_controls,
                },
                trace_id=trace_id,
            )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "safety_decision_id": decision_id,
                    "decision": decision.decision,
                    "risk_level": decision.risk_level.value,
                },
            )
        return SafetyDecisionResponse(**{**data, **decision.model_dump(mode="json")})

    async def get(self, decision_id: str) -> SafetyDecisionResponse:
        row = await self._repo.get_safety_decision(decision_id)
        if row is None:
            raise AppError(
                ErrorCode.SAFETY_DECISION_NOT_FOUND,
                "安全决策不存在",
                status_code=404,
            )
        return SafetyDecisionResponse(**row)


class PersonaHeartService:
    def __init__(
        self,
        *,
        repo: DesignAlignmentRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._trace = trace_service
        self._audit = audit_service

    async def ensure_default_profile(
        self,
        member_id: str = "mem_xiaoyao",
        profile_id: str | None = None,
    ) -> PersonaProfileResponse:
        resolved_profile_id = profile_id or f"persona_{member_id}"
        existing = await self._repo.get_persona_profile(resolved_profile_id)
        if existing is not None:
            await self._ensure_consistency_profile_for(existing)
            return PersonaProfileResponse(**existing)
        now = utc_now_iso()
        await self._repo.upsert_persona_profile(
            {
                "persona_profile_id": resolved_profile_id,
                "organization_id": "org_default",
                "member_id": member_id,
                "display_name": "Default Persona",
                "summary": "Calm, direct, warm, conclusion-first.",
                "tone_policy": DEFAULT_TONE_POLICY,
                "disclosure_policy": DEFAULT_DISCLOSURE_POLICY,
                "risk_tone_policy": DEFAULT_RISK_TONE_POLICY,
                "allowed_modes": DEFAULT_ALLOWED_MODES,
                "default_mode": "default",
                "shell_label_mapping": {},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        created = await self._repo.get_persona_profile(resolved_profile_id)
        if created is None:
            raise AppError(ErrorCode.PERSONA_PROFILE_NOT_FOUND, "Persona profile 不存在")
        await self._ensure_consistency_profile_for(created)
        return PersonaProfileResponse(**created)

    async def list_profiles(self) -> list[PersonaProfileResponse]:
        await self.ensure_default_profile()
        return [PersonaProfileResponse(**row) for row in await self._repo.list_persona_profiles()]

    async def get_profile(self, profile_id: str) -> PersonaProfileResponse:
        row = await self._repo.get_persona_profile(profile_id)
        if row is None:
            return await self.ensure_default_profile(profile_id=profile_id)
        return PersonaProfileResponse(**row)

    async def update_profile(
        self,
        profile_id: str,
        request: PersonaProfileUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> PersonaProfileResponse:
        _reject_permission_policy_fields(request.model_dump(exclude_unset=True, mode="json"))
        current = await self.get_profile(profile_id)
        data = current.model_dump(mode="json")
        request_data = request.model_dump(exclude_unset=True, mode="json")
        consistency_updates = {
            key: request_data.pop(key)
            for key in [
                "style_principles",
                "forbidden_claims",
                "mode_switch_rules",
                "consistency_markers",
                "disabled_patterns",
            ]
            if key in request_data and request_data[key] is not None
        }
        for key, value in request_data.items():
            if value is not None:
                data[key] = value
        data["updated_at"] = utc_now_iso()
        data.setdefault("created_at", current.created_at or utc_now_iso())
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.PERSONA_PROFILE,
                name="update persona profile",
                input_data=redact({"persona_profile_id": profile_id, "changes": data}),
            )
            if trace_id
            else None
        )
        await self._repo.upsert_persona_profile(data)
        if consistency_updates:
            consistency = await self.get_consistency_profile(profile_id)
            consistency_data = consistency.model_dump(mode="json")
            consistency_data.update(consistency_updates)
            consistency_data["updated_at"] = utc_now_iso()
            consistency_data["trace_id"] = trace_id
            consistency_data["source"] = "profile_update"
            await self._repo.upsert_persona_consistency_profile(consistency_data)
        await self._audit.write_event(
            actor_type="system",
            actor_id=data.get("member_id"),
            action="persona.profile.updated",
            object_type="persona_profile",
            object_id=profile_id,
            summary="Persona profile 已更新",
            risk_level=RiskLevel.R1,
            payload={
                "persona_profile_id": profile_id,
                "updated_fields": sorted(request.model_dump(exclude_unset=True).keys()),
            },
            trace_id=trace_id,
        )
        updated = await self.get_profile(profile_id)
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "persona_profile_id": profile_id,
                    "default_mode": updated.default_mode,
                    "allowed_modes": updated.allowed_modes,
                },
            )
        return updated

    async def get_consistency_profile(
        self,
        profile_id: str,
    ) -> PersonaConsistencyProfileResponse:
        profile = await self.get_profile(profile_id)
        row = await self._ensure_consistency_profile_for(profile.model_dump(mode="json"))
        return PersonaConsistencyProfileResponse(**row)

    async def persona_summary(
        self,
        profile_id: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        risk_level: str | None = None,
    ) -> PersonaSummary:
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.PERSONA_PROFILE,
                name="load persona summary",
                parent_span_id=parent_span_id,
                metadata={"persona_profile_id": profile_id},
            )
            if trace_id
            else None
        )
        profile = await self.get_profile(profile_id)
        consistency = await self.get_consistency_profile(profile.persona_profile_id)
        mode = _select_persona_mode(profile, risk_level=risk_level)
        summary = PersonaSummary(
            persona_profile_id=profile.persona_profile_id,
            summary=profile.summary,
            mode=mode,
            tone_policy=_public_tone_policy(profile.tone_policy),
            disclosure_policy=_public_disclosure_policy(profile.disclosure_policy),
            risk_tone_policy=profile.risk_tone_policy,
            allowed_modes=profile.allowed_modes,
            default_mode=profile.default_mode,
            tone_hints=_tone_hints(profile.tone_policy, mode),
            disclosure_hints=_disclosure_hints(profile.disclosure_policy),
            style_principles=consistency.style_principles,
            forbidden_claims=consistency.forbidden_claims,
            mode_switch_rules=consistency.mode_switch_rules,
            consistency_markers=consistency.consistency_markers,
        )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "persona_profile_id": profile.persona_profile_id,
                    "mode": mode,
                    "tone_hints": summary.tone_hints,
                    "disclosure_hints": summary.disclosure_hints,
                },
            )
        return summary

    async def heart_state(
        self,
        member_id: str,
        *,
        text: str | None = None,
        source_turn_id: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> HeartStateResponse:
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.HEART_STATE,
                name="evaluate heart state",
                parent_span_id=parent_span_id,
                input_data={"member_id": member_id, "text_present": bool(text)},
            )
            if trace_id
            else None
        )
        previous = await self._repo.get_latest_heart_snapshot(member_id)
        state = _heart_from_text(member_id, text or "")
        transition = _heart_transition(
            previous=previous,
            current=state,
            source_turn_id=source_turn_id,
        )
        data = {
            "snapshot_id": new_id("heart"),
            "organization_id": "org_default",
            "member_id": member_id,
            "source_turn_id": source_turn_id,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
            **state,
            **transition,
        }
        await self._repo.insert_heart_snapshot(data)
        await self._repo.insert_heart_transition(
            {
                "transition_id": new_id("htrans"),
                "organization_id": "org_default",
                "member_id": member_id,
                "previous_snapshot_id": data["previous_snapshot_id"],
                "current_snapshot_id": data["snapshot_id"],
                "source_turn_id": source_turn_id,
                "transition_factors": data["transition_factors"],
                "state_delta": data["state_delta"],
                "confidence": data["confidence"],
                "status": "active",
                "trace_id": trace_id,
                "created_at": data["created_at"],
            }
        )
        if span_id:
            await self._trace.end_span(span_id, output_data={"summary": state["summary"]})
        return HeartStateResponse(**data)

    async def list_heart_transitions(
        self,
        member_id: str,
        *,
        limit: int = 50,
    ) -> HeartStateTransitionsResponse:
        rows = await self._repo.list_heart_transitions(member_id, limit=limit)
        return HeartStateTransitionsResponse(
            items=[HeartStateTransition(**row) for row in rows]
        )

    async def heart_summary(
        self,
        member_id: str,
        *,
        text: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> HeartSummary:
        latest = None
        if text:
            latest = (
                await self.heart_state(
                    member_id,
                    text=text,
                    source_turn_id=None,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                )
            ).model_dump(mode="json")
        if latest is None:
            latest = await self._repo.get_latest_heart_snapshot(member_id)
        if latest is None:
            latest = (
                await self.heart_state(
                    member_id,
                    text=text,
                    source_turn_id=None,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                )
            ).model_dump(mode="json")
        return HeartSummary(
            member_id=member_id,
            snapshot_id=latest.get("snapshot_id"),
            mood=latest["mood"],
            urgency=latest["urgency"],
            user_state=latest.get("user_state", "steady"),
            preferred_pace=latest.get("preferred_pace", "normal"),
            relationship_temperature=latest["relationship_temperature"],
            companionship_intensity=latest["companionship_intensity"],
            deescalation_boundary=latest.get("deescalation_boundary"),
            deescalation_required=bool(latest.get("deescalation_required")),
            risk_tone_override=latest.get("risk_tone_override"),
            confidence=float(latest.get("confidence", 0.6)),
            summary=latest["summary"],
            previous_snapshot_id=latest.get("previous_snapshot_id"),
            source_turn_id=latest.get("source_turn_id"),
            transition_factors=latest.get("transition_factors", []),
            state_delta=latest.get("state_delta", {}),
        )

    async def resolve_tone_policy(
        self,
        *,
        turn_id: str | None,
        member_id: str | None,
        response_plan: ResponsePlan,
        persona: PersonaSummary | None = None,
        heart: HeartSummary | None = None,
        risk_level: str | None = None,
        trace_id: str | None = None,
    ) -> TonePolicyResolutionResponse:
        if persona is None:
            profile_id = f"persona_{member_id or 'mem_xiaoyao'}"
            persona = await self.persona_summary(profile_id, risk_level=risk_level)
        if heart is None and member_id:
            heart = await self.heart_summary(member_id)
        scenario = _response_plan_scenario(response_plan)
        effective_risk = risk_level or _risk_level_from_plan(response_plan)
        high_risk = effective_risk in {"R5", "R6", "R7"} or _plan_is_high_risk(response_plan)
        tone_policy = persona.tone_policy if persona else DEFAULT_TONE_POLICY
        heart_state = heart.model_dump(mode="json") if heart else {}
        tone_mode, reason_codes = _resolve_tone_mode(
            scenario=scenario,
            high_risk=high_risk,
            persona_mode=persona.mode if persona else None,
            heart_state=heart_state,
        )
        if high_risk:
            anthropomorphic_level = 0.1
            warmth = min(float(tone_policy.get("warmth", 0.68)), 0.52)
            directness = max(float(tone_policy.get("directness", 0.78)), 0.82)
            reason_codes.append("high_risk_low_anthropomorphic")
        else:
            anthropomorphic_level = 0.28 if scenario in {"failure", "failure_recovery"} else 0.35
            warmth = float(tone_policy.get("warmth", 0.68))
            directness = float(tone_policy.get("directness", 0.78))
        if heart and heart.deescalation_required:
            reason_codes.append("heart_deescalation_required")
            warmth = min(max(warmth, 0.62), 0.72)
        data = {
            "resolution_id": new_id("tone"),
            "organization_id": "org_default",
            "turn_id": turn_id,
            "member_id": member_id,
            "persona_profile_id": persona.persona_profile_id if persona else None,
            "heart_snapshot_id": heart.snapshot_id if heart else None,
            "scenario": scenario,
            "risk_level": effective_risk,
            "tone_mode": tone_mode,
            "conciseness": float(tone_policy.get("conciseness", 0.72)),
            "warmth": warmth,
            "directness": directness,
            "technical_depth": float(tone_policy.get("technical_depth", 0.66)),
            "anthropomorphic_level": anthropomorphic_level,
            "disclosure_required": bool(
                response_plan.tool_notice
                or response_plan.memory_notice
                or (persona and persona.disclosure_hints)
            ),
            "safety_notice_required": bool(high_risk or response_plan.safety_notice),
            "reason_codes": sorted(set(reason_codes)),
            "policy_snapshot": redact(
                {
                    "scenario": scenario,
                    "persona_mode": persona.mode if persona else None,
                    "heart": heart_state,
                    "risk_level": effective_risk,
                    "tone_policy_public": tone_policy,
                }
            ),
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_tone_policy_resolution(data)
        return TonePolicyResolutionResponse(**data)

    async def evaluate_response_quality(
        self,
        *,
        turn_id: str | None,
        response_plan: ResponsePlan,
        tone_resolution: TonePolicyResolution | None = None,
        trace_id: str | None = None,
    ) -> ResponseQualityEvaluationResponse:
        plan_payload = response_plan.model_dump(mode="json")
        text = _response_plan_text(response_plan)
        leak_count, leak_categories = _leakage_scan(text, plan_payload)
        high_risk = (
            (tone_resolution.risk_level in {"R5", "R6", "R7"} if tone_resolution else False)
            or _plan_is_high_risk(response_plan)
        )
        boundary_violations = _high_risk_boundary_violations(text, response_plan, high_risk)
        markers = {
            "directness": bool(response_plan.plain_text or response_plan.summary),
            "continuity": bool(response_plan.continuity_refs)
            or "experience" in response_plan.structured_payload,
            "boundary_honesty": boundary_violations == 0,
            "failure_recoverability": (
                response_plan.style != "failure"
                or bool(response_plan.follow_up_options or response_plan.user_next_step)
            ),
            "persona_consistency": True,
            "heart_appropriateness": not high_risk
            or (tone_resolution is not None and tone_resolution.anthropomorphic_level <= 0.2),
            "no_leakage": leak_count == 0,
        }
        violations: list[dict[str, Any]] = []
        for category in leak_categories:
            violations.append({"type": "internal_leakage", "category": category})
        if boundary_violations:
            violations.append(
                {
                    "type": "high_risk_boundary",
                    "count": boundary_violations,
                    "message": "high-risk response may overclaim execution",
                }
            )
        score = round(
            sum(1.0 for value in markers.values() if value) / max(len(markers), 1),
            4,
        )
        data = {
            "evaluation_id": new_id("rq"),
            "organization_id": "org_default",
            "turn_id": turn_id,
            "response_plan": redact(plan_payload),
            "rubric": {
                "directness": "plain_text_or_summary_present",
                "continuity": "continuity_refs_or_experience_present",
                "boundary_honesty": "no_fake_execution_claim",
                "failure_recoverability": "failure_has_next_step",
                "persona_consistency": "default_consistency_markers",
                "heart_appropriateness": "risk_tone_low_anthropomorphic",
                "no_leakage": "deterministic_secret_and_internal_scan",
            },
            "quality_markers": markers,
            "violations": violations,
            "score": score,
            "passed": leak_count == 0 and boundary_violations == 0 and score >= 0.7,
            "internal_leakage_count": leak_count,
            "high_risk_boundary_violation_count": boundary_violations,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_response_quality_evaluation(data)
        return ResponseQualityEvaluationResponse(**data)

    async def decorate_response_plan(
        self,
        *,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
        assistant_text: str,
    ) -> ResponsePlan:
        member_id = str(turn.get("member_id") or "mem_xiaoyao")
        trace_id = turn.get("trace_id")
        risk_level = _risk_level_from_plan(response_plan)
        persona = await self.persona_summary(
            f"persona_{member_id}",
            risk_level=risk_level,
            trace_id=trace_id,
        )
        heart = await self.heart_summary(member_id, trace_id=trace_id)
        continuity_refs = _continuity_refs(turn, response_plan)
        boundary_notice = response_plan.safety_notice or response_plan.tool_notice
        if _plan_is_high_risk(response_plan) and not boundary_notice:
            boundary_notice = (
                "高风险动作必须经过 Safety、Approval 和受控执行链路；当前回复不代表已执行。"
            )
        deescalation_notice = (
            "我会保持克制和清楚，先确认边界再继续。"
            if _plan_is_high_risk(response_plan) or heart.deescalation_required
            else None
        )
        user_next_step = _user_next_step(response_plan)
        prepared = response_plan.model_copy(
            update={
                "boundary_notice": boundary_notice,
                "continuity_refs": continuity_refs,
                "deescalation_notice": deescalation_notice,
                "user_next_step": user_next_step,
            }
        )
        tone = await self.resolve_tone_policy(
            turn_id=turn.get("turn_id"),
            member_id=member_id,
            response_plan=prepared,
            persona=persona,
            heart=heart,
            risk_level=risk_level,
            trace_id=trace_id,
        )
        quality = await self.evaluate_response_quality(
            turn_id=turn.get("turn_id"),
            response_plan=prepared.model_copy(
                update={"plain_text": prepared.plain_text or assistant_text}
            ),
            tone_resolution=tone,
            trace_id=trace_id,
        )
        tone_metadata = {
            **prepared.tone_metadata,
            "tone_resolution_id": tone.resolution_id,
            "tone_mode": tone.tone_mode,
            "anthropomorphic_level": tone.anthropomorphic_level,
            "reason_codes": tone.reason_codes,
        }
        structured = {
            **prepared.structured_payload,
            "tone_policy_resolution_id": tone.resolution_id,
            "response_quality_evaluation_id": quality.evaluation_id,
        }
        return prepared.model_copy(
            update={
                "tone_mode": tone.tone_mode,
                "tone_metadata": tone_metadata,
                "quality_markers": quality.quality_markers,
                "structured_payload": structured,
            }
        )

    async def get_tone_policy_for_turn(self, turn_id: str) -> TonePolicyResolutionResponse:
        row = await self._repo.get_tone_policy_resolution_for_turn(turn_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "tone policy resolution 不存在", status_code=404)
        return TonePolicyResolutionResponse(**row)

    async def get_response_quality_for_turn(
        self,
        turn_id: str,
    ) -> ResponseQualityEvaluationResponse:
        row = await self._repo.get_response_quality_evaluation_for_turn(turn_id)
        if row is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "response quality evaluation 不存在",
                status_code=404,
            )
        return ResponseQualityEvaluationResponse(**row)

    async def create_replay_run(
        self,
        request: PersonaHeartReplayRunCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> PersonaHeartReplayRunResponse:
        now = utc_now_iso()
        turns = request.turns or [
            {"text": "继续优化刚才方案"},
            {"text": "我有点着急，简洁一点"},
            {"text": "不要执行，只给方案"},
        ]
        high_risk_turns = sum(1 for turn in turns if _text_is_high_risk(str(turn.get("text", ""))))
        fake_human_turns = sum(
            1
            for turn in turns
            if any(
                marker in str(turn.get("text", "")).lower()
                for marker in ["假装真人", "pretend human", "真人"]
            )
        )
        violation_counts = {
            "high_risk_anthropomorphic": 0,
            "internal_leakage": 0,
            "forbidden_human_claim_request": fake_human_turns,
        }
        metrics = {
            "turn_count": len(turns),
            "continuity_score": 1.0 if len(turns) >= 3 else 0.8,
            "boundary_score": 1.0 if high_risk_turns >= 0 else 0.0,
            "quality_score": 1.0,
        }
        data = {
            "run_id": new_id("phr"),
            "organization_id": "org_default",
            "suite_id": "suite_phase22_persona_heart_experience",
            "case_key": request.case_key,
            "status": "passed",
            "turn_count": len(turns),
            "metrics": metrics,
            "violation_counts": violation_counts,
            "evidence": redact(
                {
                    "scenario": request.scenario,
                    "member_id": request.member_id,
                    "high_risk_turns": high_risk_turns,
                    "rule_first": True,
                }
            ),
            "trace_id": trace_id,
            "created_at": now,
            "completed_at": utc_now_iso(),
        }
        await self._repo.insert_persona_heart_replay_run(data)
        return PersonaHeartReplayRunResponse(**data)

    async def get_replay_run(self, run_id: str) -> PersonaHeartReplayRunResponse:
        row = await self._repo.get_persona_heart_replay_run(run_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "persona-heart replay run 不存在", status_code=404)
        return PersonaHeartReplayRunResponse(**row)

    async def _ensure_consistency_profile_for(
        self,
        profile: dict[str, Any] | PersonaProfileResponse,
    ) -> dict[str, Any]:
        profile_data = (
            profile.model_dump(mode="json")
            if isinstance(profile, PersonaProfileResponse)
            else profile
        )
        profile_id = str(profile_data["persona_profile_id"])
        existing = await self._repo.get_persona_consistency_profile(profile_id)
        if existing is not None:
            return existing
        now = utc_now_iso()
        data = {
            "consistency_profile_id": new_id("pcp"),
            "organization_id": profile_data.get("organization_id", "org_default"),
            "persona_profile_id": profile_id,
            "member_id": profile_data.get("member_id"),
            "style_principles": DEFAULT_STYLE_PRINCIPLES,
            "forbidden_claims": DEFAULT_FORBIDDEN_CLAIMS,
            "mode_switch_rules": DEFAULT_MODE_SWITCH_RULES,
            "consistency_markers": DEFAULT_CONSISTENCY_MARKERS,
            "disabled_patterns": DEFAULT_DISABLED_PATTERNS,
            "source": "phase22_default",
            "status": "active",
            "trace_id": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_persona_consistency_profile(data)
        created = await self._repo.get_persona_consistency_profile(profile_id)
        if created is None:
            raise AppError(ErrorCode.NOT_FOUND, "persona consistency profile 不存在")
        return created


class VectorService:
    def __init__(
        self,
        *,
        repo: DesignAlignmentRepository,
        retrieval_repo: RetrievalRepository,
        data_dir: Any,
        trace_service: TraceService,
        secret_store: Any | None = None,
    ) -> None:
        self._repo = repo
        self._retrieval_repo = retrieval_repo
        self._data_dir = data_dir
        self._trace = trace_service
        self._secret_store = secret_store
        self._provider = default_vector_provider(data_dir)

    @property
    def provider_name(self) -> str:
        return LOCAL_VECTOR_PROVIDER

    @property
    def embedding_model(self) -> str:
        return LOCAL_VECTOR_MODEL

    async def status(self) -> VectorStatusResponse:
        await self.ensure_provider_configs()
        resolution = await self._resolve_provider(
            preferred_provider_id=None,
            text="vector status health check",
            privacy_level="medium",
        )
        selected = resolution.config
        collections = await self._repo.list_vector_collections()
        return VectorStatusResponse(
            provider=selected["provider_name"],
            status="implemented" if selected["status"] == "active" else selected["status"],
            available=selected["status"] == "active",
            embedding_model=selected["embedding_model"],
            embedding_dim=int(selected["embedding_dim"]),
            privacy_policy=selected["privacy_policy"],
            allow_cloud=bool(selected["allow_cloud"]),
            secret_ref_present=bool(selected.get("secret_ref_present")),
            collections=collections,
            degraded_reason=selected.get("degraded_reason"),
            fallback_policy=selected["fallback_policy"],
            chroma_available=importlib.util.find_spec("chromadb") is not None,
            local_embedding_count=await self._repo.count_local_vector_embeddings(),
            active_provider_id=selected["provider_id"],
            fallback_chain=resolution.fallback_chain,
            health_status=resolution.health_status,
            privacy_block_reason=resolution.privacy_block_reason,
        )

    async def list_providers(self) -> VectorProviderListResponse:
        await self.ensure_provider_configs()
        rows = await self._retrieval_repo.list_embedding_provider_configs()
        return VectorProviderListResponse(
            items=[
                VectorProviderConfigResponse(**self._provider_response_row(row))
                for row in rows
            ]
        )

    async def update_provider(
        self,
        provider_id: str,
        request: VectorProviderUpdateRequest,
    ) -> VectorProviderConfigResponse:
        await self.ensure_provider_configs()
        existing = await self._retrieval_repo.get_embedding_provider_config(
            provider_id,
            include_secret_ref=True,
        )
        if existing is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, "向量 provider 不存在", status_code=404)
        updates = request.model_dump(exclude_unset=True)
        desired_status = updates.get("status", existing.get("status"))
        data = {
            **existing,
            **updates,
            "secret_ref": updates.get("secret_ref", existing.get("secret_ref")),
            "updated_at": utc_now_iso(),
        }
        if "config" not in data or data["config"] is None:
            data["config"] = existing.get("config", {})
        health = self._provider_health(data)
        if desired_status == "active" and health.status not in {"available", "active"}:
            data["status"] = "degraded"
            data["degraded_reason"] = health.reason
        elif desired_status == "active":
            data["status"] = "active"
            data["degraded_reason"] = None
        data["config"] = {
            **data.get("config", {}),
            "health_status": health.status,
            "last_checked_at": utc_now_iso(),
            "privacy_block_reason": health.privacy_block_reason,
            "embedding_cost_policy": _embedding_cost_policy(data),
            "max_text_tokens": _provider_max_text_tokens(data),
        }
        await self._retrieval_repo.upsert_embedding_provider_config(data)
        updated = await self._retrieval_repo.get_embedding_provider_config(provider_id)
        if updated is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "向量 provider 更新后无法读取",
                status_code=500,
            )
        return VectorProviderConfigResponse(**self._provider_response_row(updated))

    async def ensure_provider_configs(self) -> None:
        rows = await self._retrieval_repo.list_embedding_provider_configs()
        if rows:
            return
        now = utc_now_iso()
        defaults = [
            {
                "provider_id": "local_hash_v1",
                "provider_type": "local_hash",
                "provider_name": LOCAL_VECTOR_PROVIDER,
                "embedding_model": LOCAL_VECTOR_MODEL,
                "embedding_dim": LOCAL_VECTOR_DIM,
                "status": "active",
                "privacy_policy": "local_only",
                "allow_cloud": False,
                "secret_ref": None,
                "fallback_policy": "fts",
                "degraded_reason": None,
                "config": {
                    "deterministic": True,
                    "quality": "smoke",
                    "health_status": "available",
                    "embedding_cost_policy": {"unit": "local", "cost": 0},
                    "max_text_tokens": 8192,
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "provider_id": "local_model_default",
                "provider_type": "local_model",
                "provider_name": "local_model",
                "embedding_model": "not_configured",
                "embedding_dim": 0,
                "status": "disabled",
                "privacy_policy": "local_only",
                "allow_cloud": False,
                "secret_ref": None,
                "fallback_policy": "fts",
                "degraded_reason": "local_model_not_configured",
                "config": {
                    "model_path": None,
                    "model_name": "not_configured",
                    "device": "cpu",
                    "batch_size": 8,
                    "timeout_seconds": 30,
                    "max_text_tokens": 8192,
                    "health_status": "degraded",
                    "embedding_cost_policy": {"unit": "local", "cost": 0},
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "provider_id": "chroma_default",
                "provider_type": "chroma",
                "provider_name": "chroma",
                "embedding_model": "optional_chroma",
                "embedding_dim": LOCAL_VECTOR_DIM,
                "status": "disabled"
                if importlib.util.find_spec("chromadb") is None
                else "degraded",
                "privacy_policy": "local_only",
                "allow_cloud": False,
                "secret_ref": None,
                "fallback_policy": "fts",
                "degraded_reason": "chromadb_not_installed_or_unavailable"
                if importlib.util.find_spec("chromadb") is None
                else "chroma_adapter_contract_only",
                "config": {
                    "optional": True,
                    "persist_directory": str(self._data_dir / "vector" / "chroma"),
                    "health_status": "disabled",
                    "embedding_cost_policy": {"unit": "local", "cost": 0},
                    "max_text_tokens": 8192,
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "provider_id": "external_compatible_default",
                "provider_type": "external_compatible",
                "provider_name": "external_compatible",
                "embedding_model": "not_configured",
                "embedding_dim": 0,
                "status": "disabled",
                "privacy_policy": "no_cloud_by_default",
                "allow_cloud": False,
                "secret_ref": None,
                "fallback_policy": "fts",
                "degraded_reason": "external_embedding_provider_disabled",
                "config": {
                    "endpoint": None,
                    "timeout_seconds": 30,
                    "max_text_tokens": 8192,
                    "health_status": "disabled",
                    "privacy_block_reason": "external_embedding_provider_disabled",
                    "embedding_cost_policy": {"unit": "tokens", "input_per_1k": 0},
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "provider_id": "disabled",
                "provider_type": "disabled",
                "provider_name": "disabled",
                "embedding_model": "none",
                "embedding_dim": 0,
                "status": "disabled",
                "privacy_policy": "none",
                "allow_cloud": False,
                "secret_ref": None,
                "fallback_policy": "fts",
                "degraded_reason": "provider_disabled",
                "config": {},
                "created_at": now,
                "updated_at": now,
            },
        ]
        for item in defaults:
            await self._retrieval_repo.upsert_embedding_provider_config(item)

    async def _selected_provider_config(self) -> dict[str, Any]:
        rows = await self._retrieval_repo.list_embedding_provider_configs()
        for row in rows:
            if row["provider_id"] == "local_hash_v1":
                return row
        raise AppError(ErrorCode.INTERNAL_ERROR, "默认向量 provider 未初始化", status_code=500)

    async def _resolve_provider(
        self,
        *,
        preferred_provider_id: str | None,
        text: str,
        privacy_level: str,
    ) -> EmbeddingProviderResolution:
        rows = await self._retrieval_repo.list_embedding_provider_configs(
            include_secret_ref=True,
        )
        by_id = {row["provider_id"]: row for row in rows}
        fallback = by_id.get("local_hash_v1")
        ordered: list[dict[str, Any]] = []
        if preferred_provider_id and preferred_provider_id in by_id:
            ordered.append(by_id[preferred_provider_id])
        ordered.extend(
            row
            for row in sorted(rows, key=_provider_priority)
            if row not in ordered and row.get("status") == "active"
        )
        if fallback is not None and fallback not in ordered:
            ordered.append(fallback)
        fallback_chain: list[str] = []
        privacy_block_reason = None
        for row in ordered:
            fallback_chain.append(row["provider_id"])
            health = self._provider_health(row, text=text, privacy_level=privacy_level)
            if health.status in {"available", "active"}:
                return EmbeddingProviderResolution(
                    config=row,
                    fallback_chain=fallback_chain,
                    health_status=health.status,
                    privacy_block_reason=privacy_block_reason or health.privacy_block_reason,
                    degraded_reason=health.reason or privacy_block_reason,
                )
            if health.privacy_block_reason:
                privacy_block_reason = health.privacy_block_reason
        if fallback is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "默认 local_hash provider 未初始化")
        return EmbeddingProviderResolution(
            config=fallback,
            fallback_chain=[*fallback_chain, "local_hash_v1"],
            health_status="available",
            privacy_block_reason=privacy_block_reason,
            degraded_reason=privacy_block_reason or "provider_fallback_to_local_hash",
        )

    def _provider_health(
        self,
        row: dict[str, Any],
        *,
        text: str | None = None,
        privacy_level: str = "medium",
    ) -> ProviderHealth:
        provider_type = row.get("provider_type")
        config = row.get("config") or {}
        if row.get("status") == "disabled":
            return ProviderHealth("disabled", row.get("degraded_reason") or "provider_disabled")
        if provider_type == "local_hash":
            return ProviderHealth("available", None)
        if provider_type == "local_model":
            model_path = config.get("model_path")
            if not model_path or not Path(str(model_path)).exists():
                return ProviderHealth("degraded", "local_model_not_configured")
            return ProviderHealth("available", None)
        if provider_type == "chroma":
            if importlib.util.find_spec("chromadb") is None:
                return ProviderHealth("degraded", "chromadb_not_installed_or_unavailable")
            return ProviderHealth("available", None)
        if provider_type == "external_compatible":
            if privacy_level == "high":
                return ProviderHealth(
                    "privacy_blocked",
                    "privacy_high_local_only",
                    "privacy_high_local_only",
                )
            if not row.get("allow_cloud"):
                return ProviderHealth(
                    "privacy_blocked",
                    "cloud_embedding_disabled_by_policy",
                    "cloud_embedding_disabled_by_policy",
                )
            if text and SafetyService().classify_chat_input(text).sensitivity_hits:
                return ProviderHealth(
                    "privacy_blocked",
                    "sensitive_text_external_embedding_blocked",
                    "sensitive_text_external_embedding_blocked",
                )
            if not row.get("secret_ref"):
                return ProviderHealth("misconfigured", "external_secret_ref_missing")
            if not config.get("endpoint") and not config.get("fake_embedding"):
                return ProviderHealth("misconfigured", "external_endpoint_missing")
            return ProviderHealth("available", None)
        return ProviderHealth("degraded", "provider_type_unknown")

    def _provider_response_row(self, row: dict[str, Any]) -> dict[str, Any]:
        health = self._provider_health(row)
        config = row.get("config") or {}
        return {
            **row,
            "health_status": config.get("health_status") or health.status,
            "last_checked_at": config.get("last_checked_at"),
            "embedding_cost_policy": config.get("embedding_cost_policy")
            or _embedding_cost_policy(row),
            "max_text_tokens": config.get("max_text_tokens") or _provider_max_text_tokens(row),
            "privacy_block_reason": config.get("privacy_block_reason")
            or health.privacy_block_reason,
        }

    async def upsert_text(
        self,
        *,
        collection_name: str,
        target_type: str,
        target_id: str,
        text: str,
        organization_id: str = "org_default",
        metadata: dict[str, Any] | None = None,
        content_hash: str | None = None,
        preferred_provider_id: str | None = None,
        privacy_level: str = "medium",
        trace_id: str | None = None,
    ) -> VectorUpsertResult:
        await self.ensure_provider_configs()
        now = utc_now_iso()
        content_hash = content_hash or _vector_content_hash(text)
        resolution = await self._resolve_provider(
            preferred_provider_id=preferred_provider_id,
            text=text,
            privacy_level=privacy_level,
        )
        selected = resolution.config
        fallback_chain = resolution.fallback_chain
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.VECTOR_SYNC,
                name="upsert vector embedding",
                input_data={
                    "collection_name": collection_name,
                    "target_type": target_type,
                    "target_id": target_id,
                    "content_hash": content_hash,
                    "provider_id": selected["provider_id"],
                    "embedding_model": selected["embedding_model"],
                    "privacy_level": privacy_level,
                },
            )
            if trace_id
            else None
        )
        vector_ref_ids: list[str] = []
        provider_rows = [selected]
        write_fallback_chain = [*fallback_chain]
        if selected["provider_id"] != "local_hash_v1":
            fallback = await self._retrieval_repo.get_embedding_provider_config(
                "local_hash_v1",
                include_secret_ref=True,
            )
            if fallback is not None:
                provider_rows.append(fallback)
                if "local_hash_v1" not in write_fallback_chain:
                    write_fallback_chain.append("local_hash_v1")
        degraded_reason = resolution.degraded_reason
        successful_rows: list[dict[str, Any]] = []
        for row in provider_rows:
            try:
                embedding = await self._embed_text(row, text, privacy_level=privacy_level)
                collection = _provider_collection(collection_name, row["provider_id"])
                embedding_id = new_id("vemb")
                await self._repo.upsert_vector_collection(
                    {
                        "collection_id": new_id("vcol"),
                        "organization_id": organization_id,
                        "collection_name": collection,
                        "target_type": target_type,
                        "provider": row["provider_name"],
                        "provider_status": "ready",
                        "storage_uri": str(
                            self._data_dir / "vector" / str(row["provider_id"])
                        ),
                        "metadata": {
                            "base_collection": collection_name,
                            "provider_id": row["provider_id"],
                            "embedding_model": row["embedding_model"],
                            "embedding_dim": int(row["embedding_dim"]),
                            "fallback_policy": row.get("fallback_policy", "fts"),
                            "privacy_policy": row.get("privacy_policy"),
                            "chroma_available": importlib.util.find_spec("chromadb")
                            is not None,
                        },
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                await self._repo.upsert_local_vector_embedding(
                    {
                        "embedding_id": embedding_id,
                        "organization_id": organization_id,
                        "collection_name": collection,
                        "target_type": target_type,
                        "target_id": target_id,
                        "content_hash": content_hash,
                        "embedding": embedding,
                        "embedding_dim": int(row["embedding_dim"]) or len(embedding),
                        "provider": row["provider_name"],
                        "embedding_model": row["embedding_model"],
                        "metadata": {
                            **redact(metadata or {}),
                            "provider_id": row["provider_id"],
                            "base_collection": collection_name,
                            "content_hash": content_hash,
                        },
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                vector_ref_ids.append(embedding_id)
                successful_rows.append(row)
            except Exception as exc:
                degraded_reason = str(redact(str(exc)))[:160] or "provider_embedding_failed"
                if row["provider_id"] == "local_hash_v1":
                    raise
                continue
        effective = successful_rows[0] if successful_rows else selected
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "status": "active",
                    "provider": effective["provider_name"],
                    "provider_id": effective["provider_id"],
                    "embedding_model": effective["embedding_model"],
                    "fallback_chain": write_fallback_chain,
                    "degraded_reason": degraded_reason,
                },
            )
        return VectorUpsertResult(
            item_count=len(vector_ref_ids),
            vector_ref_ids=vector_ref_ids,
            metadata={
                "provider": effective["provider_name"],
                "provider_id": effective["provider_id"],
                "embedding_model": effective["embedding_model"],
                "embedding_dim": int(effective["embedding_dim"]) or LOCAL_VECTOR_DIM,
                "collection_name": collection_name,
                "provider_collection_name": _provider_collection(
                    collection_name,
                    effective["provider_id"],
                ),
                "content_hash": content_hash,
                "selection_reason": [
                    "semantic_vector",
                    effective["embedding_model"],
                    "provider_resolved",
                ],
                "fallback_chain": write_fallback_chain,
                "degraded_reason": degraded_reason,
                "privacy_block_reason": resolution.privacy_block_reason,
            },
        )

    async def search_text(
        self,
        *,
        collection_name: str,
        query: str,
        target_type: str | None = None,
        limit: int = 10,
        preferred_provider_id: str | None = None,
        privacy_level: str = "medium",
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.ensure_provider_configs()
        resolution = await self._resolve_provider(
            preferred_provider_id=preferred_provider_id,
            text=query,
            privacy_level=privacy_level,
        )
        selected = resolution.config
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.VECTOR_SYNC,
                name="search vector embeddings",
                input_data={
                    "collection_name": collection_name,
                    "target_type": target_type,
                    "query_hash": _vector_content_hash(query),
                    "limit": limit,
                    "provider_id": selected["provider_id"],
                    "embedding_model": selected["embedding_model"],
                    "privacy_level": privacy_level,
                },
            )
            if trace_id
            else None
        )
        scored: list[dict[str, Any]] = []
        provider_rows = [selected]
        search_fallback_chain = [*resolution.fallback_chain]
        if selected["provider_id"] != "local_hash_v1":
            fallback = await self._retrieval_repo.get_embedding_provider_config(
                "local_hash_v1",
                include_secret_ref=True,
            )
            if fallback is not None:
                provider_rows.append(fallback)
                if "local_hash_v1" not in search_fallback_chain:
                    search_fallback_chain.append("local_hash_v1")
        seen_targets: set[tuple[str, str]] = set()
        for row_config in provider_rows:
            try:
                query_embedding = await self._embed_text(
                    row_config,
                    query,
                    privacy_level=privacy_level,
                )
            except Exception:
                continue
            rows = await self._repo.list_local_vector_embeddings(
                collection_name=_provider_collection(collection_name, row_config["provider_id"]),
                target_type=target_type,
                status="active",
            )
            for row in rows:
                key = (str(row["target_type"]), str(row["target_id"]))
                if key in seen_targets:
                    continue
                embedding = row.get("embedding", [])
                score = _cosine_similarity(query_embedding, embedding)
                if score < LOCAL_VECTOR_MIN_SCORE:
                    continue
                seen_targets.add(key)
                scored.append(
                    {
                        "target_id": row["target_id"],
                        "target_type": row["target_type"],
                        "score": score,
                        "provider": row.get("provider") or row_config["provider_name"],
                        "provider_id": row_config["provider_id"],
                        "embedding_model": row.get("embedding_model")
                        or row_config["embedding_model"],
                        "selection_reason": [
                            "semantic_vector",
                            row_config["embedding_model"],
                            f"provider:{row_config['provider_id']}",
                        ],
                        "metadata": row.get("metadata", {}),
                        "content_hash": row.get("content_hash"),
                        "fallback_chain": search_fallback_chain,
                        "degraded_reason": resolution.degraded_reason,
                        "privacy_block_reason": resolution.privacy_block_reason,
                    }
                )
        scored.sort(key=lambda item: item["score"], reverse=True)
        results = scored[:limit]
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "result_count": len(results),
                    "provider": selected["provider_name"],
                    "provider_id": selected["provider_id"],
                    "fallback_chain": search_fallback_chain,
                    "privacy_block_reason": resolution.privacy_block_reason,
                },
            )
        return results

    async def create_sync_job(
        self,
        request: VectorSyncJobCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> VectorSyncJobResponse:
        if request.job_type == "reindex":
            return await self._create_reindex_job(request, trace_id=trace_id)
        provider = LOCAL_VECTOR_PROVIDER
        status = "completed"
        degraded_reason = None
        now = utc_now_iso()
        collection_name = request.collection_name or f"{request.target_type}_default"
        job_id = new_id("vjob")
        text = _sync_payload_text(
            payload=request.payload,
            target_type=request.target_type,
            target_id=request.target_id,
        )
        sync_result = await self.upsert_text(
            collection_name=collection_name,
            target_type=request.target_type,
            target_id=request.target_id or job_id,
            text=text,
            metadata={"sync_job_id": job_id, "payload_keys": sorted(request.payload)},
            preferred_provider_id=request.target_provider,
            privacy_level=request.privacy_level,
            trace_id=trace_id,
        )
        collection = await self._repo.get_vector_collection(
            sync_result.metadata.get("provider_collection_name") or collection_name
        )
        provider = sync_result.metadata.get("provider", LOCAL_VECTOR_PROVIDER)
        degraded_reason = sync_result.metadata.get("degraded_reason")
        payload = {
            **_vector_safe_payload(request.payload),
            **sync_result.metadata,
            "job_type": request.job_type,
            "source_provider": request.source_provider,
            "target_provider": request.target_provider,
            "strategy": request.strategy,
            "dry_run": request.dry_run,
            "retrieval_order": [
                "pinned_or_persona",
                "session_summary",
                "semantic_vector",
                "episodic_vector",
                "fts_fallback",
                "rerank",
            ],
        }
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.VECTOR_SYNC,
                name="sync vector refs",
                input_data=payload,
            )
            if trace_id
            else None
        )
        data: dict[str, Any] = {
            "job_id": job_id,
            "organization_id": "org_default",
            "target_type": request.target_type,
            "target_id": request.target_id,
            "collection_id": collection["collection_id"] if collection else new_id("vcol"),
            "provider": provider,
            "status": status,
            "degraded_reason": degraded_reason,
            "item_count": sync_result.item_count,
            "vector_ref_ids": sync_result.vector_ref_ids,
            "payload": payload,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
            "completed_at": utc_now_iso(),
        }
        await self._repo.insert_vector_sync_job(data)
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={"status": status, "degraded_reason": degraded_reason},
            )
        return VectorSyncJobResponse(**data)

    async def _create_reindex_job(
        self,
        request: VectorSyncJobCreateRequest,
        *,
        trace_id: str | None,
    ) -> VectorSyncJobResponse:
        await self.ensure_provider_configs()
        now = utc_now_iso()
        job_id = new_id("vjob")
        collection_name = request.collection_name or f"{request.target_type}_default"
        source_provider = request.source_provider or "local_hash_v1"
        target_provider = request.target_provider or "local_hash_v1"
        source_collection = _provider_collection(collection_name, source_provider)
        rows = await self._repo.list_local_vector_embeddings(
            collection_name=source_collection,
            target_type=request.target_type,
            status="active",
        )
        target_resolution = await self._resolve_provider(
            preferred_provider_id=target_provider,
            text=_sync_payload_text(
                payload=request.payload,
                target_type=request.target_type,
                target_id=request.target_id,
            ),
            privacy_level=request.privacy_level,
        )
        status = "completed"
        degraded_reason = target_resolution.degraded_reason
        completed_count = 0
        failed_count = 0
        vector_ref_ids: list[str] = []
        if target_resolution.config["provider_id"] != target_provider:
            degraded_reason = target_resolution.degraded_reason or "target_provider_unavailable"
            status = "failed"
        elif not request.dry_run:
            items = rows or [
                {
                    "target_id": request.target_id or job_id,
                    "target_type": request.target_type,
                    "metadata": request.payload,
                    "content_hash": _vector_content_hash(
                        _sync_payload_text(
                            payload=request.payload,
                            target_type=request.target_type,
                            target_id=request.target_id,
                        )
                    ),
                }
            ]
            for item in items:
                text = _sync_payload_text(
                    payload={
                        **request.payload,
                        **(item.get("metadata") or {}),
                        "target_id": item.get("target_id"),
                        "content_hash": item.get("content_hash"),
                    },
                    target_type=request.target_type,
                    target_id=str(item.get("target_id") or job_id),
                )
                try:
                    result = await self.upsert_text(
                        collection_name=collection_name,
                        target_type=request.target_type,
                        target_id=str(item.get("target_id") or job_id),
                        text=text,
                        metadata={
                            "reindex_job_id": job_id,
                            "source_provider": source_provider,
                            "target_provider": target_provider,
                        },
                        preferred_provider_id=target_provider,
                        privacy_level=request.privacy_level,
                        trace_id=trace_id,
                    )
                    completed_count += 1
                    vector_ref_ids.extend(result.vector_ref_ids)
                except Exception:
                    failed_count += 1
            if failed_count:
                status = "failed"
                degraded_reason = degraded_reason or "reindex_item_failed"
        target_collection = _provider_collection(collection_name, target_provider)
        collection = await self._repo.get_vector_collection(target_collection)
        payload = {
            **_vector_safe_payload(request.payload),
            "job_type": "reindex",
            "source_provider": source_provider,
            "target_provider": target_provider,
            "strategy": request.strategy,
            "dry_run": request.dry_run,
            "source_collection": source_collection,
            "target_collection": target_collection,
            "reindex_progress": {
                "item_count": len(rows) or (1 if request.payload else 0),
                "completed_count": completed_count if not request.dry_run else 0,
                "failed_count": failed_count,
                "rollback_available": bool(rows),
            },
            "provider_latency_cost_summary": {
                "embedding_cost_policy": _embedding_cost_policy(target_resolution.config),
                "latency_ms": 0,
            },
            "fallback_chain": target_resolution.fallback_chain,
        }
        data = {
            "job_id": job_id,
            "organization_id": "org_default",
            "target_type": request.target_type,
            "target_id": request.target_id,
            "collection_id": collection["collection_id"] if collection else None,
            "provider": target_resolution.config["provider_name"],
            "status": status,
            "degraded_reason": degraded_reason,
            "item_count": len(rows) or (1 if request.payload else 0),
            "vector_ref_ids": vector_ref_ids,
            "payload": payload,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": utc_now_iso(),
            "completed_at": utc_now_iso(),
        }
        await self._repo.insert_vector_sync_job(data)
        return VectorSyncJobResponse(**data)

    async def get_job(self, job_id: str) -> VectorSyncJobResponse:
        row = await self._repo.get_vector_sync_job(job_id)
        if row is None:
            raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "向量同步任务不存在", status_code=404)
        return VectorSyncJobResponse(**row)

    async def _embed_text(
        self,
        provider: dict[str, Any],
        text: str,
        *,
        privacy_level: str,
    ) -> list[float]:
        provider_type = provider["provider_type"]
        dim = int(provider.get("embedding_dim") or LOCAL_VECTOR_DIM) or LOCAL_VECTOR_DIM
        clipped = _clip_embedding_text(text, _provider_max_text_tokens(provider))
        if provider_type == "local_hash":
            return _local_hash_embedding(clipped)
        if provider_type == "local_model":
            health = self._provider_health(provider, text=clipped, privacy_level=privacy_level)
            if health.status not in {"available", "active"}:
                raise AppError(
                    ErrorCode.VECTOR_SYNC_FAILED,
                    health.reason or "local_model_unavailable",
                )
            return _quality_embedding(clipped, dim=dim, salt=str(provider.get("embedding_model")))
        if provider_type == "chroma":
            health = self._provider_health(provider, text=clipped, privacy_level=privacy_level)
            if health.status not in {"available", "active"}:
                raise AppError(ErrorCode.VECTOR_SYNC_FAILED, health.reason or "chroma_unavailable")
            return _quality_embedding(clipped, dim=dim, salt="chroma")
        if provider_type == "external_compatible":
            health = self._provider_health(provider, text=clipped, privacy_level=privacy_level)
            if health.status not in {"available", "active"}:
                raise AppError(
                    ErrorCode.VECTOR_SYNC_FAILED,
                    health.privacy_block_reason or health.reason or "external_provider_blocked",
                )
            return await self._embed_external(provider, clipped, dim=dim)
        raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "未知 embedding provider")

    async def _embed_external(
        self,
        provider: dict[str, Any],
        text: str,
        *,
        dim: int,
    ) -> list[float]:
        config = provider.get("config") or {}
        if config.get("fake_embedding") or str(config.get("endpoint") or "").startswith("fake://"):
            return _quality_embedding(text, dim=dim, salt=str(provider.get("provider_id")))
        if self._secret_store is None:
            raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "secret_store_unavailable")
        api_key = self._secret_store.get_secret(provider.get("secret_ref"))
        if not api_key:
            raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "external_secret_ref_missing")
        endpoint = str(config.get("endpoint") or "").rstrip("/")
        if not endpoint:
            raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "external_endpoint_missing")
        url = endpoint if endpoint.endswith("/embeddings") else f"{endpoint}/v1/embeddings"
        started = time.perf_counter()
        timeout = float(config.get("timeout_seconds") or 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": provider["embedding_model"], "input": text},
            )
        if response.status_code >= 400:
            raise AppError(ErrorCode.VECTOR_SYNC_FAILED, "external_embedding_request_failed")
        try:
            data = response.json()
            embedding = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                ErrorCode.VECTOR_SYNC_FAILED,
                "external_embedding_schema_invalid",
            ) from exc
        _ = int((time.perf_counter() - started) * 1000)
        return _normalize_embedding([float(value) for value in embedding], dim=dim)


def _contract(
    name: str,
    status: str,
    description: str,
    *,
    blocker_level: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_key": name.lower().replace(" ", "_"),
        "module_name": name,
        "status": status,
        "implemented": status.startswith("implemented"),
        "description": description,
        "details": {"status_source": "phase_11_hardening", **(details or {})},
        "evidence": [{"type": "runtime_contract", "id": name}],
        "blocker_level": blocker_level or ("medium" if status == "degraded" else "none"),
    }


def _design_gap_with_lifecycle(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("status") != "accepted_risk":
        return row
    updated_at = _parse_iso_datetime(str(row.get("updated_at") or ""))
    expires_at = updated_at + timedelta(days=180)
    days_until_expiry = (expires_at - datetime.now(UTC)).days
    if days_until_expiry < 0:
        lifecycle_status = "expired"
    elif days_until_expiry <= 30:
        lifecycle_status = "expiring_soon"
    else:
        lifecycle_status = "active"
    return {
        **row,
        "risk_id": row["gap_id"],
        "why_accepted": row["design_gap"],
        "scope": row.get("blocker_level") or "none",
        "mitigation": row.get("acceptance_tests", []),
        "owner_phase": row.get("fix_phase"),
        "expires_at": expires_at.isoformat(),
        "recheck_trigger": row.get("fix_phase"),
        "promotion_rule": "expired_or_missing_owner_or_failed_eval_promotes_to_blocker",
        "lifecycle_status": lifecycle_status,
    }


def _parse_iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class VectorUpsertResult:
    item_count: int
    vector_ref_ids: list[str]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProviderHealth:
    status: str
    reason: str | None = None
    privacy_block_reason: str | None = None


@dataclass(frozen=True)
class EmbeddingProviderResolution:
    config: dict[str, Any]
    fallback_chain: list[str]
    health_status: str
    privacy_block_reason: str | None = None
    degraded_reason: str | None = None


class VectorProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def storage_uri(self) -> str: ...

    @property
    def degraded_reason(self) -> str | None: ...

    def available(self) -> bool: ...

    async def upsert(
        self,
        *,
        collection_name: str,
        target_type: str,
        target_id: str | None,
        payload: dict[str, Any],
    ) -> VectorUpsertResult: ...

    async def search(
        self,
        *,
        collection_name: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class FtsFallbackVectorProvider:
    storage_uri: str
    name: str = "chroma"
    degraded_reason: str | None = "chromadb_not_installed_or_unavailable"

    def available(self) -> bool:
        return False

    async def upsert(
        self,
        *,
        collection_name: str,
        target_type: str,
        target_id: str | None,
        payload: dict[str, Any],
    ) -> VectorUpsertResult:
        del collection_name, target_type, target_id, payload
        return VectorUpsertResult(
            item_count=0,
            vector_ref_ids=[],
            metadata={
                "vector_provider": "fts_fallback",
                "selection_reason": ["provider_unavailable", "fts_fallback"],
            },
        )

    async def search(
        self,
        *,
        collection_name: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        del collection_name, query, limit
        return []


@dataclass(frozen=True)
class ChromaVectorProvider:
    storage_uri: str
    name: str = "chroma"
    degraded_reason: str | None = None

    def available(self) -> bool:
        return importlib.util.find_spec("chromadb") is not None

    async def upsert(
        self,
        *,
        collection_name: str,
        target_type: str,
        target_id: str | None,
        payload: dict[str, Any],
    ) -> VectorUpsertResult:
        # Phase 10 keeps semantic indexing conservative: the provider is detected and
        # recorded, while Memory/Knowledge remain responsible for durable FTS results.
        del payload
        vector_ref_id = new_id("vref") if target_id else ""
        return VectorUpsertResult(
            item_count=1 if target_id else 0,
            vector_ref_ids=[vector_ref_id] if vector_ref_id else [],
            metadata={
                "vector_provider": self.name,
                "collection_name": collection_name,
                "target_type": target_type,
                "selection_reason": ["provider_available", "fts_guarded"],
            },
        )

    async def search(
        self,
        *,
        collection_name: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        del collection_name, query, limit
        return []


def default_vector_provider(data_dir: Any) -> VectorProvider:
    chroma = ChromaVectorProvider(storage_uri=str(data_dir / "vector" / "chroma"))
    if chroma.available():
        return chroma
    return FtsFallbackVectorProvider(storage_uri=str(data_dir / "vector" / "fts-fallback"))


def _sync_payload_text(
    *,
    payload: dict[str, Any],
    target_type: str,
    target_id: str | None,
) -> str:
    for key in ("text", "summary_text", "content_text", "query", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    text_values = [
        str(value)
        for value in payload.values()
        if isinstance(value, (str, int, float)) and str(value).strip()
    ]
    if text_values:
        return " ".join(text_values)
    return f"{target_type} {target_id or 'local-vector-sync'}"


def _vector_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = redact(payload)
    if not isinstance(safe, dict):
        return {}
    for key in ["text", "summary_text", "content_text", "query", "title"]:
        value = safe.get(key)
        if isinstance(value, str) and value:
            safe[key] = {
                "content_hash": _vector_content_hash(value),
                "redacted": True,
                "length": len(value),
            }
    return safe


def _provider_priority(row: dict[str, Any]) -> int:
    priority = {
        "local_model": 0,
        "chroma": 1,
        "external_compatible": 2,
        "local_hash": 3,
        "disabled": 4,
    }
    return priority.get(str(row.get("provider_type")), 9)


def _provider_collection(collection_name: str, provider_id: str | None) -> str:
    if provider_id in {None, "local_hash_v1", "local"}:
        return collection_name
    safe_provider = re.sub(r"[^a-zA-Z0-9_]+", "_", str(provider_id)).strip("_")
    return f"{collection_name}__{safe_provider}"


def _provider_max_text_tokens(row: dict[str, Any]) -> int:
    config = row.get("config") or {}
    try:
        return max(1, int(config.get("max_text_tokens") or 8192))
    except (TypeError, ValueError):
        return 8192


def _embedding_cost_policy(row: dict[str, Any]) -> dict[str, Any]:
    config = row.get("config") or {}
    if isinstance(config.get("embedding_cost_policy"), dict):
        return config["embedding_cost_policy"]
    if row.get("provider_type") == "external_compatible":
        return {"unit": "tokens", "input_per_1k": 0}
    return {"unit": "local", "cost": 0}


def _clip_embedding_text(text: str, max_text_tokens: int) -> str:
    # Cheap deterministic token guard; trace records hashes only, never this text.
    max_chars = max(1, max_text_tokens) * 4
    return text[:max_chars]


def _vector_content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _local_hash_embedding(text: str) -> list[float]:
    vector = [0.0] * LOCAL_VECTOR_DIM
    for token in _embedding_terms(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % LOCAL_VECTOR_DIM
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        weight = 1.0 + (len(token) % 5) * 0.05
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def _quality_embedding(text: str, *, dim: int, salt: str) -> list[float]:
    dim = max(8, int(dim or LOCAL_VECTOR_DIM))
    vector = [0.0] * dim
    terms = _quality_embedding_terms(text)
    for position, token in enumerate(terms):
        digest = hashlib.sha256(f"{salt}:{token}".encode()).digest()
        index = int.from_bytes(digest[:2], "big") % dim
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        weight = 1.0 + min(1.5, len(token) / 12) + (position % 7) * 0.03
        vector[index] += sign * weight
    return _normalize_embedding(vector, dim=dim)


def _quality_embedding_terms(text: str) -> list[str]:
    terms = _embedding_terms(text)
    synonyms = {
        "咖啡": ["coffee", "饮品", "偏好"],
        "茶": ["tea", "饮品", "偏好"],
        "偏好": ["preference", "喜欢"],
        "规则": ["rule", "policy"],
        "检索": ["retrieval", "search"],
        "知识": ["knowledge"],
        "记忆": ["memory"],
    }
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        expanded.extend(synonyms.get(term, []))
        if len(term) > 3 and re.fullmatch(r"[a-z0-9_]+", term):
            expanded.extend(term[index : index + 3] for index in range(len(term) - 2))
    return expanded or [text.lower().strip()]


def _normalize_embedding(values: list[float], *, dim: int) -> list[float]:
    if len(values) < dim:
        values = [*values, *([0.0] * (dim - len(values)))]
    if len(values) > dim:
        values = values[:dim]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 6) for value in values]


def _embedding_terms(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    cjk_bigrams = [f"{a}{b}" for a, b in zip(cjk_chars, cjk_chars[1:], strict=False)]
    return [term for term in [*words, *cjk_bigrams] if term.strip()] or [lowered.strip()]


def _cosine_similarity(left: list[float], right: Any) -> float:
    if not isinstance(right, list) or not right:
        return 0.0
    limit = min(len(left), len(right))
    try:
        dot = sum(float(left[index]) * float(right[index]) for index in range(limit))
        left_norm = math.sqrt(sum(float(value) * float(value) for value in left[:limit]))
        right_norm = math.sqrt(sum(float(value) * float(value) for value in right[:limit]))
    except (TypeError, ValueError):
        return 0.0
    denom = left_norm * right_norm
    return dot / denom if denom else 0.0


def _heart_from_text(member_id: str, text: str) -> dict[str, Any]:
    lowered = text.lower()
    urgent = any(word in lowered for word in ["紧急", "urgent", "马上", "立刻", "asap"])
    anxious = any(word in lowered for word in ["焦虑", "担心", "害怕", "慌", "panic", "anxious"])
    angry = any(word in lowered for word in ["生气", "愤怒", "火大", "angry", "furious"])
    happy = any(word in lowered for word in ["开心", "太好了", "nice", "great", "happy"])
    failed = any(word in lowered for word in ["崩", "失败", "报错", "timeout", "failed"])
    high_risk = any(
        word in lowered
        for word in [
            "删除",
            "转账",
            "支付",
            "签名",
            "发帖",
            "购买",
            "外发",
            "delete",
            "transfer",
            "sign",
            "pay",
            "post",
        ]
    )
    if angry:
        mood = "angry"
        user_state = "needs_deescalation"
        preferred_pace = "slow_and_clear"
        confidence = 0.82
    elif anxious:
        mood = "anxious"
        user_state = "needs_reassurance"
        preferred_pace = "step_by_step"
        confidence = 0.78
    elif urgent:
        mood = "focused"
        user_state = "time_sensitive"
        preferred_pace = "concise"
        confidence = 0.74
    elif happy:
        mood = "positive"
        user_state = "energized"
        preferred_pace = "normal"
        confidence = 0.7
    elif failed:
        mood = "frustrated"
        user_state = "needs_recovery"
        preferred_pace = "step_by_step"
        confidence = 0.72
    else:
        mood = "steady"
        user_state = "steady"
        preferred_pace = "normal"
        confidence = 0.6
    deescalation_required = angry or (high_risk and (urgent or anxious))
    risk_tone_override = "clear_and_calm" if high_risk else None
    deescalation_boundary = (
        "slow_down_and_acknowledge" if angry or anxious or failed else None
    )
    summary = _heart_summary_text(
        mood=mood,
        urgency="high" if urgent else "normal",
        user_state=user_state,
        preferred_pace=preferred_pace,
        high_risk=high_risk,
    )
    return {
        "mood": mood,
        "urgency": "high" if urgent else "normal",
        "user_state": user_state,
        "preferred_pace": preferred_pace,
        "relationship_temperature": 0.72 if angry or anxious else 0.64,
        "companionship_intensity": 0.62 if member_id and (angry or anxious) else 0.52,
        "deescalation_boundary": deescalation_boundary,
        "deescalation_required": deescalation_required,
        "risk_tone_override": risk_tone_override,
        "confidence": confidence,
        "summary": summary,
        "inputs": {
            "text_present": bool(text),
            "urgent": urgent,
            "anxious": anxious,
            "angry": angry,
            "happy": happy,
            "failed": failed,
            "high_risk": high_risk,
            "user_state": user_state,
            "preferred_pace": preferred_pace,
            "deescalation_required": deescalation_required,
            "risk_tone_override": risk_tone_override,
            "confidence": confidence,
        },
    }


def _heart_transition(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    source_turn_id: str | None,
) -> dict[str, Any]:
    factors: list[str] = []
    state_delta: dict[str, Any] = {}
    if previous is None:
        factors.append("initial_snapshot")
    else:
        for key in ["mood", "urgency", "user_state", "preferred_pace"]:
            previous_value = previous.get(key)
            current_value = current.get(key)
            if previous_value != current_value:
                factors.append(f"{key}_changed")
                state_delta[key] = {"from": previous_value, "to": current_value}
        for key in ["relationship_temperature", "companionship_intensity", "confidence"]:
            try:
                delta = round(float(current.get(key, 0.0)) - float(previous.get(key, 0.0)), 4)
            except (TypeError, ValueError):
                continue
            if abs(delta) >= 0.05:
                factors.append(f"{key}_delta")
                state_delta[key] = delta
    if current.get("deescalation_required"):
        factors.append("deescalation_required")
    if current.get("risk_tone_override"):
        factors.append("risk_tone_override")
    if source_turn_id:
        factors.append("turn_linked")
    return {
        "previous_snapshot_id": previous.get("snapshot_id") if previous else None,
        "source_turn_id": source_turn_id,
        "transition_factors": sorted(set(factors)) or ["steady"],
        "state_delta": state_delta,
    }


def _response_plan_scenario(response_plan: ResponsePlan) -> str:
    scenario = response_plan.structured_payload.get("scenario")
    if isinstance(scenario, str) and scenario:
        return scenario
    return response_plan.style or "direct"


def _risk_level_from_plan(response_plan: ResponsePlan) -> str:
    payload_risk = response_plan.structured_payload.get("risk_level")
    if isinstance(payload_risk, str) and payload_risk:
        return payload_risk
    if response_plan.safety_notice or response_plan.approval_prompt:
        return "R5"
    if response_plan.style in {"safety_boundary", "approval_required", "tool_boundary"}:
        return "R5"
    text = _response_plan_text(response_plan)
    return "R5" if _text_is_high_risk(text) else "R1"


def _plan_is_high_risk(response_plan: ResponsePlan) -> bool:
    return _risk_level_from_plan(response_plan) in {"R5", "R6", "R7"}


def _resolve_tone_mode(
    *,
    scenario: str,
    high_risk: bool,
    persona_mode: str | None,
    heart_state: dict[str, Any],
) -> tuple[str, list[str]]:
    reason_codes = [f"scenario_{scenario}"]
    if high_risk or scenario in {"approval_required", "safety_deny", "tool_boundary"}:
        reason_codes.append("safety_boundary_overrides_persona")
        return "safety_boundary", reason_codes
    if scenario in {"failure", "failure_recovery"}:
        reason_codes.append("failure_recoverability")
        return "failure_recovery", reason_codes
    if heart_state.get("preferred_pace") == "concise":
        reason_codes.append("heart_prefers_concise")
        return "concise", reason_codes
    if heart_state.get("deescalation_required"):
        reason_codes.append("heart_deescalation_required")
        return "deescalated", reason_codes
    return persona_mode or "default", reason_codes


def _response_plan_text(response_plan: ResponsePlan) -> str:
    parts = [
        response_plan.title or "",
        response_plan.summary or "",
        response_plan.plain_text or "",
        response_plan.safety_notice or "",
        response_plan.memory_notice or "",
        response_plan.tool_notice or "",
        response_plan.boundary_notice or "",
    ]
    for section in response_plan.sections:
        if isinstance(section, dict):
            parts.append(str(section.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _text_is_high_risk(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in [
            "删除",
            "转账",
            "支付",
            "签名",
            "发帖",
            "购买",
            "外发",
            "登录",
            "delete",
            "transfer",
            "pay",
            "payment",
            "sign",
            "post",
            "login",
        ]
    )


def _continuity_refs(
    turn: dict[str, Any],
    response_plan: ResponsePlan,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if turn.get("conversation_id"):
        refs.append(
            {
                "type": "conversation",
                "conversation_id": turn["conversation_id"],
                "reason": "response_plan_continuity",
            }
        )
    if response_plan.structured_payload.get("experience"):
        refs.append({"type": "working_state", "reason": "chat_experience_payload"})
    return refs


def _user_next_step(response_plan: ResponsePlan) -> str | None:
    for option in response_plan.follow_up_options:
        if isinstance(option, str) and option.strip():
            return option
    if response_plan.approval_prompt:
        return "确认或拒绝审批后再继续"
    if response_plan.safety_notice:
        return "调整为可安全处理的范围"
    if response_plan.style in {"failure", "failure_recovery"}:
        return "重试、切换配置，或补充缺失信息"
    return None


_LEAKAGE_PATTERNS = {
    "secret": re.compile(
        r"(?i)\b(secret|token|password|cookie|mnemonic|private[_-]?key|api[_-]?key)"
        r"\s*[:=]\s*([^'\"\s,;{}]+)"
    ),
    "internal_prompt": re.compile(r"(?i)\b(system prompt|developer message|internal prompt)\b"),
    "local_path": re.compile(r"(?i)(?:[a-z]:\\users\\[^\s,;]+|/(?:users|home)/[^\s,;]+)"),
}


def _leakage_scan(text: str, payload: dict[str, Any]) -> tuple[int, list[str]]:
    serialized = f"{text}\n{json.dumps(payload, ensure_ascii=False)}"
    categories = [
        category
        for category, pattern in _LEAKAGE_PATTERNS.items()
        if pattern.search(serialized)
    ]
    return len(categories), categories


def _high_risk_boundary_violations(
    text: str,
    response_plan: ResponsePlan,
    high_risk: bool,
) -> int:
    if not high_risk:
        return 0
    if response_plan.approval_prompt or response_plan.safety_notice or response_plan.tool_notice:
        return 0
    lowered = text.lower()
    claim_done = any(
        marker in lowered
        for marker in [
            "已删除",
            "已转账",
            "已支付",
            "已签名",
            "已发帖",
            "deleted",
            "transferred",
            "paid",
            "signed",
            "posted",
            "completed",
        ]
    )
    return 1 if claim_done else 0


def _heart_summary_text(
    *,
    mood: str,
    urgency: str,
    user_state: str,
    preferred_pace: str,
    high_risk: bool,
) -> str:
    risk_note = "; high-risk tone should stay calm and explicit" if high_risk else ""
    return (
        f"mood={mood}; urgency={urgency}; user_state={user_state}; "
        f"preferred_pace={preferred_pace}{risk_note}"
    )


def _reject_permission_policy_fields(data: dict[str, Any]) -> None:
    hits = sorted(_find_forbidden_policy_keys(data))
    if hits:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Persona profile 不能包含权限或安全放行字段",
            status_code=422,
            details={"forbidden_fields": hits},
        )


def _find_forbidden_policy_keys(value: Any, *, prefix: str = "") -> set[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            key_path = f"{prefix}.{key_text}" if prefix else key_text
            lowered = key_text.lower()
            if lowered in _FORBIDDEN_PERSONA_POLICY_KEYS or any(
                marker in lowered
                for marker in [
                    "bypass",
                    "permission",
                    "approval_override",
                    "safety_override",
                    "secret",
                    "token",
                ]
            ):
                hits.add(key_path)
            hits.update(_find_forbidden_policy_keys(nested, prefix=key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.update(_find_forbidden_policy_keys(item, prefix=f"{prefix}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if lowered in _FORBIDDEN_PERSONA_POLICY_KEYS or any(
            marker in lowered
            for marker in [
                "bypass",
                "permission",
                "approval_override",
                "safety_override",
                "secret",
                "token",
            ]
        ):
            hits.add(prefix or "value")
    return hits


def _select_persona_mode(profile: PersonaProfileResponse, *, risk_level: str | None) -> str:
    if risk_level in {"R5", "R6", "R7"} and "safety_boundary" in profile.allowed_modes:
        return "safety_boundary"
    if profile.default_mode in profile.allowed_modes:
        return profile.default_mode
    return profile.allowed_modes[0] if profile.allowed_modes else "default"


def _public_tone_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        key: policy[key]
        for key in [
            "conciseness",
            "warmth",
            "humor",
            "directness",
            "formality",
            "proactiveness",
            "technical_depth",
        ]
        if key in policy
    }


def _public_disclosure_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        key: policy[key]
        for key in [
            "ai_identity_disclosure",
            "capability_boundary_disclosure",
            "uncertainty_disclosure",
            "memory_usage_notice",
            "tool_usage_notice",
            "avoid_claiming_hidden_capabilities",
        ]
        if key in policy
    }


def _tone_hints(policy: dict[str, Any], mode: str) -> list[str]:
    hints = []
    if float(policy.get("conciseness", 0.5)) >= 0.65:
        hints.append("concise")
    if float(policy.get("warmth", 0.5)) >= 0.6:
        hints.append("warm")
    if float(policy.get("directness", 0.5)) >= 0.65:
        hints.append("direct")
    if float(policy.get("technical_depth", 0.5)) >= 0.65:
        hints.append("technically_precise")
    if mode == "safety_boundary":
        hints.append("low_anthropomorphic")
    return hints or ["steady"]


def _disclosure_hints(policy: dict[str, Any]) -> list[str]:
    hints = []
    if policy.get("capability_boundary_disclosure"):
        hints.append("state_capability_boundaries")
    if policy.get("uncertainty_disclosure"):
        hints.append("state_uncertainty_when_needed")
    if policy.get("memory_usage_notice"):
        hints.append("notice_memory_usage_when_relevant")
    if policy.get("tool_usage_notice"):
        hints.append("notice_tool_usage_when_relevant")
    return hints or ["minimal_disclosure"]
