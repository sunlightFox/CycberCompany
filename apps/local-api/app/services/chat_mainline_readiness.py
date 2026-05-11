from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ChatMainlineReadinessService:
    CONTROL_PLANE_VERSION = "phase76.chat_mainline_control_plane.v1"
    EXECUTION_BATCHES_VERSION = "phase85.execution_batches_control_plane.v1"
    _PHASE85_RECOMMENDED_PR_ORDER = [
        "PR1 运行时接口收口",
        "PR2 turn 状态机与事件流统一",
        "PR3 渠道 session / conversation 统一",
        "PR4 ContextGateway 能力化与安全注释",
        "PR5 single-turn tool loop",
        "PR6 ResponseComposer 可见收口",
        "PR7 visible filter / honesty 统一",
        "PR8 runtime 话术清退",
        "PR9 memory source 契约收紧",
        "PR10 run ledger 统一",
        "PR11 replay / diagnostics 读路径统一",
        "PR12 hook runner 与标准 hook 面",
        "PR13 hook fail-closed 与治理边界",
        "PR14 全量测试矩阵和门禁脚本收尾",
    ]
    _PHASE85_BATCHES = [
        {
            "batch_id": "batch1_runtime_entry_closure",
            "title": "运行时入口收口",
            "depends_on": [],
            "phase_key": "phase77_runtime_closure",
            "entry_modules": [
                "services/chat-runtime/chat_runtime/runtime.py",
                "apps/local-api/app/services/session_runtime.py",
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/services/chat_turn_execution.py",
                "apps/local-api/app/services/turn_recovery.py",
            ],
            "owned_capabilities": [
                "session_runtime_entry_contract",
                "chat_runtime_host",
                "turn_state_machine",
                "cancel_retry_recover_entrypoints",
            ],
            "blocked_capabilities": [
                "channel_session_semantics",
                "response_visibility_policy",
                "tool_risk_reclassification",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase77_chat_runtime_closure.py",
                "apps/local-api/tests/test_phase60_turn_recovery.py",
                "apps/local-api/tests/test_phase70_runtime_topology.py",
            ],
            "compat_shells_allowed": ["apps/local-api/app/services/chat.py"],
            "removal_gate": [
                "phase77_runtime_closure_ready",
                "phase84_acceptance_matrix_ready",
                "runtime_topology_consistent",
            ],
        },
        {
            "batch_id": "batch2_channel_session_semantics",
            "title": "会话与渠道语义统一",
            "depends_on": ["batch1_runtime_entry_closure"],
            "phase_key": "phase78_session_channel_semantics",
            "entry_modules": [
                "apps/local-api/app/services/channel_ingress_runtime.py",
                "apps/local-api/app/services/channel_session_router.py",
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
            ],
            "owned_capabilities": [
                "channel_session_semantics_runtime",
                "peer_session_reuse_and_dedupe",
                "wechat_feishu_ingress_mainline",
            ],
            "blocked_capabilities": [
                "cross_channel_conversation_reuse_default",
                "gateway_private_turn_building",
                "response_visible_rewrite_in_gateway",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase78_session_channel_semantics.py",
                "apps/local-api/tests/test_phase54_wechat_gateway_full_link.py",
                "apps/local-api/tests/test_phase66_feishu_channel.py",
            ],
            "compat_shells_allowed": [
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
            ],
            "removal_gate": [
                "phase78_session_channel_semantics_ready",
                "channel_acceptance_coverage_present",
                "duplicate_inbound_gate_green",
            ],
        },
        {
            "batch_id": "batch3_context_gateway_layering",
            "title": "ContextGateway 能力化增强",
            "depends_on": ["batch1_runtime_entry_closure", "batch2_channel_session_semantics"],
            "phase_key": "phase79_context_gateway_enhancement",
            "entry_modules": [
                "apps/local-api/app/services/context_gateway.py",
                "apps/local-api/app/services/session_context.py",
                "apps/local-api/app/services/context_budget.py",
                "apps/local-api/app/services/context_visibility.py",
            ],
            "owned_capabilities": [
                "layered_context_packet",
                "current_message_priority_guard",
                "capability_summary_and_handles",
                "untrusted_context_and_dynamic_safety_notes",
            ],
            "blocked_capabilities": [
                "parallel_context_runtime",
                "presence_runtime_overrides_session_facts",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase79_context_gateway_enhancement.py",
                "apps/local-api/tests/test_phase75_quality_takeover_rollout.py",
            ],
            "compat_shells_allowed": ["apps/local-api/app/services/chat_context.py"],
            "removal_gate": [
                "phase79_context_gateway_enhancement_ready",
                "current_message_priority_guarded",
                "context_gateway_layering_visible",
            ],
        },
        {
            "batch_id": "batch4_single_turn_tool_loop",
            "title": "聊天内工具闭环",
            "depends_on": ["batch3_context_gateway_layering"],
            "phase_key": "phase80_tool_loop",
            "entry_modules": [
                "apps/local-api/app/services/chat_model_execution.py",
                "apps/local-api/app/services/tools.py",
                "apps/local-api/app/services/chat_direct_routes_runtime.py",
            ],
            "owned_capabilities": [
                "single_turn_tool_loop",
                "approval_pending_honesty_boundary",
                "task_handoff_boundary",
            ],
            "blocked_capabilities": [
                "bypass_tool_runtime",
                "multi_step_background_work_in_single_turn",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase80_chat_tool_loop.py",
                "apps/local-api/tests/test_phase71_tool_runtime_terminal_queue.py",
            ],
            "compat_shells_allowed": ["apps/local-api/app/services/tools.py"],
            "removal_gate": [
                "phase80_tool_loop_ready",
                "tool_runtime_dispatcher_bound",
                "browser_evidence_refs_visible",
            ],
        },
        {
            "batch_id": "batch5_response_visibility_governance",
            "title": "ResponseComposer 与可见性治理",
            "depends_on": ["batch1_runtime_entry_closure", "batch4_single_turn_tool_loop"],
            "phase_key": "phase81_response_visibility",
            "entry_modules": [
                "apps/local-api/app/services/chat_response.py",
                "apps/local-api/app/services/channel_stream_bridge.py",
            ],
            "owned_capabilities": [
                "response_plan_visible_authority",
                "visible_filter_and_honesty_contract",
                "channel_final_plain_text_bridge",
            ],
            "blocked_capabilities": [
                "runtime_specific_longform_visible_reply_builders",
                "gateway_visible_semantics_rewrite",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase81_response_visibility_contract.py",
                "apps/local-api/tests/test_phase81_response_finalize_visible_output.py",
            ],
            "compat_shells_allowed": [],
            "removal_gate": [
                "phase81_response_visibility_ready",
                "response_filter_standardized",
                "gateway_final_text_source_channel_stream_bridge",
                "chat_response_finalize_removed",
            ],
        },
        {
            "batch_id": "batch6_ledger_memory_unification",
            "title": "记忆写入与运行账本",
            "depends_on": ["batch4_single_turn_tool_loop", "batch5_response_visibility_governance"],
            "phase_key": "phase82_ledger_memory",
            "entry_modules": [
                "apps/local-api/app/services/memory.py",
                "apps/local-api/app/services/chat_run_ledger.py",
                "apps/local-api/app/services/release.py",
            ],
            "owned_capabilities": [
                "memory_source_contract",
                "turn_and_run_ledger",
                "ledger_backed_replay_and_diagnostics",
            ],
            "blocked_capabilities": [
                "memory_write_without_source",
                "trace_only_replay_without_ledger_ref",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase82_ledger_memory_unification.py",
                "apps/local-api/tests/test_memory_phase3.py",
            ],
            "compat_shells_allowed": [],
            "removal_gate": [
                "phase82_ledger_memory_ready",
                "chat_run_ledger_service_bound",
                "memory_source_minimum_fields_present",
            ],
        },
        {
            "batch_id": "batch7_hook_contract_integration",
            "title": "Hook 契约接入",
            "depends_on": [
                "batch1_runtime_entry_closure",
                "batch3_context_gateway_layering",
                "batch4_single_turn_tool_loop",
                "batch6_ledger_memory_unification",
            ],
            "phase_key": "phase83_hooks",
            "entry_modules": [
                "apps/local-api/app/services/chat_hook_runtime.py",
                "apps/local-api/app/services/chat_turn_execution.py",
                "apps/local-api/app/services/memory.py",
            ],
            "owned_capabilities": [
                "hook_runner_and_stages",
                "fail_closed_governance",
                "trace_audit_hook_contract",
            ],
            "blocked_capabilities": [
                "hook_bypass_tool_runtime",
                "hook_bypass_visible_filter",
                "hook_bypass_memory_source_contract",
            ],
            "minimum_test_files": [
                "apps/local-api/tests/test_phase83_hook_runtime_contract.py",
                "apps/local-api/tests/test_phase80_chat_tool_loop.py",
                "apps/local-api/tests/test_phase82_ledger_memory_unification.py",
            ],
            "compat_shells_allowed": [],
            "removal_gate": [
                "phase83_hooks_ready",
                "blocked_stages_include_before_finalize_before_memory_write",
                "hook_trace_audit_contract_present",
            ],
        },
    ]
    _MAINLINE_PATH = [
        "SessionRuntime",
        "ChatRuntime",
        "ContextGateway",
        "Brain",
        "Safety",
        "Tool/Task",
        "ResponseComposer",
        "Memory/Trace",
    ]
    _PHASE_DOCS = {
        "phase77_runtime_closure": "docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md",
        "phase86_runtime_host_uniqueness": "docs/开发计划/86-第八十六阶段-ChatRuntime兼容壳瘦身与主链路唯一化.md",
        "phase87_action_state_semantics": "docs/开发计划/87-第八十七阶段-动作状态机与完成态证据统一.md",
        "phase88_channel_reliability": "docs/开发计划/88-第八十八阶段-渠道可靠性与NoTurn治理闭环.md",
        "phase89_false_interception_governance": "docs/开发计划/89-第八十九阶段-聊天质量误拦截治理与规则减法.md",
        "phase90_compat_cleanup_release_gate": "docs/开发计划/90-第九十阶段-主链路兼容逻辑删除窗口与封版门禁收尾.md",
        "phase91_host_decomposition_governance": "docs/开发计划/91-第九十一阶段-ChatRuntime物理拆分与宿主瘦身收尾.md",
        "phase78_session_channel_semantics": "docs/开发计划/78-第七十八阶段-会话与渠道语义统一.md",
        "phase79_context_gateway_enhancement": "docs/开发计划/79-第七十九阶段-ContextGateway能力化增强.md",
        "phase80_tool_loop": "docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md",
        "phase81_response_visibility": "docs/开发计划/81-第八十一阶段-ResponseComposer与可见性治理统一.md",
        "phase82_ledger_memory": "docs/开发计划/82-第八十二阶段-记忆写入与运行账本统一.md",
        "phase83_hooks": "docs/开发计划/83-第八十三阶段-Hook与扩展点契约.md",
        "phase84_acceptance_matrix": "docs/开发计划/84-第八十四阶段-聊天主链路测试与验收矩阵.md",
        "phase85_execution_batches": "docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md",
    }
    _PHASE_TESTS = {
        "phase70_runtime_topology": "apps/local-api/tests/test_phase70_runtime_topology.py",
        "phase68_quality_gate": "apps/local-api/tests/test_phase68_quality_gate.py",
        "phase60_turn_recovery": "apps/local-api/tests/test_phase60_turn_recovery.py",
        "phase74_runtime_cleanup_acceptance": "apps/local-api/tests/test_phase74_runtime_cleanup_acceptance.py",
        "phase75_quality_takeover_rollout": "apps/local-api/tests/test_phase75_quality_takeover_rollout.py",
        "phase76_chat_mainline_control_plane": "apps/local-api/tests/test_phase76_chat_mainline_control_plane.py",
        "phase77_chat_runtime_closure": "apps/local-api/tests/test_phase77_chat_runtime_closure.py",
        "phase86_runtime_host_uniqueness": "apps/local-api/tests/test_phase86_runtime_host_uniqueness.py",
        "phase87_action_state_semantics": "apps/local-api/tests/test_phase87_action_status_semantics.py",
        "phase88_channel_reliability": "apps/local-api/tests/test_phase88_channel_reliability.py",
        "phase89_false_interception_governance": "apps/local-api/tests/test_phase89_false_interception_governance.py",
        "phase90_compat_cleanup_release_gate": "apps/local-api/tests/test_phase90_compat_cleanup_release_gate.py",
        "phase91_host_decomposition_governance": "apps/local-api/tests/test_phase91_host_decomposition_governance.py",
        "phase78_session_channel_semantics": "apps/local-api/tests/test_phase78_session_channel_semantics.py",
        "phase79_context_gateway_enhancement": "apps/local-api/tests/test_phase79_context_gateway_enhancement.py",
        "phase80_chat_tool_loop": "apps/local-api/tests/test_phase80_chat_tool_loop.py",
        "phase81_response_visibility_contract": "apps/local-api/tests/test_phase81_response_visibility_contract.py",
        "phase82_ledger_memory_unification": "apps/local-api/tests/test_phase82_ledger_memory_unification.py",
        "phase83_hook_runtime_contract": "apps/local-api/tests/test_phase83_hook_runtime_contract.py",
        "phase84_chat_mainline_acceptance_matrix": "apps/local-api/tests/test_phase84_chat_mainline_acceptance_matrix.py",
        "phase85_execution_batches_control_plane": "apps/local-api/tests/test_phase85_execution_batches_control_plane.py",
    }

    def __init__(
        self,
        *,
        root_dir: Path,
        chat_runtime: Any,
        chat_service: Any,
        session_runtime: Any,
        channel_session_semantics_runtime: Any,
        channel_ingress_runtime: Any,
        tool_runtime: Any,
        browser_workflow_runtime: Any,
        skill_plugin_service: Any,
        mcp_service: Any,
        wechat_gateway_service: Any,
        feishu_gateway_service: Any,
        release_gate_service: Any,
        chat_run_ledger_service: Any | None = None,
        chat_hook_runtime: Any | None = None,
    ) -> None:
        self._root_dir = root_dir
        self._chat_runtime = chat_runtime
        self._chat_service = chat_service
        self._session_runtime = session_runtime
        self._channel_session_semantics_runtime = channel_session_semantics_runtime
        self._channel_ingress_runtime = channel_ingress_runtime
        self._tool_runtime = tool_runtime
        self._browser_workflow_runtime = browser_workflow_runtime
        self._skill_plugin_service = skill_plugin_service
        self._mcp_service = mcp_service
        self._wechat_gateway_service = wechat_gateway_service
        self._feishu_gateway_service = feishu_gateway_service
        self._release_gate_service = release_gate_service
        self._chat_run_ledger_service = chat_run_ledger_service
        self._chat_hook_runtime = chat_hook_runtime

    async def diagnostic(self) -> dict[str, Any]:
        session_diag = await self._session_runtime.diagnostic()
        chat_runtime_diag = self._chat_runtime.diagnostic()
        channel_semantics_diag = self._channel_session_semantics_runtime.runtime_diagnostic()
        channel_diag = await self._channel_ingress_runtime.diagnostic()
        tool_diag = await self._tool_runtime.diagnostic()
        skill_diag = await self._skill_plugin_service.runtime_diagnostic()
        mcp_diag = await self._mcp_service.runtime_diagnostic()
        browser_diag = self._browser_workflow_runtime.diagnostic()
        wechat_diag = self._wechat_gateway_service.runtime_diagnostic()
        feishu_diag = self._feishu_gateway_service.runtime_diagnostic()
        phase68_summary = await self._release_gate_service._phase68_report_summary(None)
        release_signals = {
            "runtime_topology_consistent": self._runtime_topology_consistent(
                session_diag=session_diag,
                channel_diag=channel_diag,
                wechat_diag=wechat_diag,
                feishu_diag=feishu_diag,
            ),
            "prompt_contract_coverage": bool(
                dict(phase68_summary.get("prompt_version_coverage") or {}).get(
                    "voice_policy_v4_coverage"
                )
                and dict(phase68_summary.get("prompt_version_coverage") or {}).get(
                    "prompt_assembly_v4_coverage"
                )
            ),
            "visible_leakage_count": int(phase68_summary.get("visible_leakage_count") or 0),
            "shadow_policy_readiness": dict(phase68_summary.get("shadow_policy") or {}),
            "presence_runtime_rollout_visible": self._presence_runtime_rollout_visible(),
            "context_budget_visible": self._relative_exists(
                "apps/local-api/app/services/context_budget.py"
            ),
            "context_visibility_visible": self._relative_exists(
                "apps/local-api/app/services/context_visibility.py"
            ),
            "current_message_priority_guarded": self._relative_exists(
                self._PHASE_TESTS["phase75_quality_takeover_rollout"]
            ),
            "phase79_context_gateway_test_present": self._relative_exists(
                self._PHASE_TESTS["phase79_context_gateway_enhancement"]
            ),
            "context_gateway_layering_visible": self._relative_exists(
                "apps/local-api/app/services/context_gateway.py"
            ),
            "phase81_response_visibility_contract_present": self._relative_exists(
                self._PHASE_TESTS["phase81_response_visibility_contract"]
            ),
            "response_filter_standardized": self._response_filter_standardized(),
            "gateway_final_text_source": self._gateway_final_text_source(),
            "chat_response_finalize_removed": self._chat_response_finalize_removed(),
        }

        phase_docs_present = {
            phase: self._relative_exists(path)
            for phase, path in self._PHASE_DOCS.items()
        }
        phase_tests_present = {
            name: self._relative_exists(path) for name, path in self._PHASE_TESTS.items()
        }

        phase77 = self._phase77(session_diag, chat_runtime_diag)
        phase86 = self._phase86(session_diag, chat_runtime_diag)
        phase87 = self._phase87()
        phase88 = self._phase88(wechat_diag, feishu_diag)
        phase89 = self._phase89()
        phase78 = self._phase78(channel_diag, channel_semantics_diag, wechat_diag, feishu_diag)
        phase79 = self._phase79(release_signals)
        phase80 = self._phase80(tool_diag, browser_diag)
        phase81 = self._phase81(release_signals)
        phase82 = self._phase82()
        phase83 = self._phase83()
        prior_phase_readiness = {
            "phase77_runtime_closure": phase77,
            "phase86_runtime_host_uniqueness": phase86,
            "phase87_action_state_semantics": phase87,
            "phase88_channel_reliability": phase88,
            "phase89_false_interception_governance": phase89,
            "phase78_session_channel_semantics": phase78,
            "phase79_context_gateway_enhancement": phase79,
            "phase80_tool_loop": phase80,
            "phase81_response_visibility": phase81,
            "phase82_ledger_memory": phase82,
            "phase83_hooks": phase83,
        }
        phase84 = self._phase84(
            release_signals,
            phase_tests_present,
            prior_phase_readiness,
        )
        phase_readiness = {
            **prior_phase_readiness,
            "phase84_acceptance_matrix": phase84,
            "phase85_execution_batches": self._phase85(
                skill_diag,
                mcp_diag,
                prior_phase_readiness=prior_phase_readiness,
                phase84_readiness=phase84,
            ),
        }
        phase90 = self._phase90(
            phase85=phase_readiness["phase85_execution_batches"],
            phase88=phase88,
            phase89=phase89,
            wechat_diag=wechat_diag,
            feishu_diag=feishu_diag,
        )
        phase_readiness["phase90_compat_cleanup_release_gate"] = phase90
        phase91 = self._phase91()
        phase_readiness["phase91_host_decomposition_governance"] = phase91
        blocking_gaps = [
            {
                "phase": phase,
                "status": details["status"],
                "blocking_reasons": details["blocking_reasons"],
                "source_of_truth": details["source_of_truth"],
            }
            for phase, details in phase_readiness.items()
            if details["status"] != "ready"
        ]

        evidence_refs = (
            [
                self._evidence_ref(path, "doc")
                for path in self._PHASE_DOCS.values()
                if self._relative_exists(path)
            ]
            + [
                self._evidence_ref(path, "test")
                for path in self._PHASE_TESTS.values()
                if self._relative_exists(path)
            ]
            + [
                {"type": "service", "path": "apps/local-api/app/services/session_runtime.py"},
                {"type": "service", "path": "apps/local-api/app/services/chat.py"},
                {"type": "service", "path": "apps/local-api/app/services/chat_response.py"},
            ]
        )

        return {
            "phase76_control_plane_version": self.CONTROL_PLANE_VERSION,
            "mainline_path_declared": list(self._MAINLINE_PATH),
            "phase_readiness": phase_readiness,
            "blocking_gaps": blocking_gaps,
            "evidence_refs": evidence_refs,
            "runtime_facts": {
                "session_runtime_role": "entry_runtime",
                "chat_service_host": "apps/local-api/app/services/chat.py",
                "compat_hosts": [
                    "apps/local-api/app/services/chat.py",
                    "apps/local-api/app/services/tools.py",
                    "apps/local-api/app/services/wechat_gateway.py",
                    "apps/local-api/app/services/feishu_gateway.py",
                ],
                "presence_runtime_rollout_visible": bool(
                    release_signals.get("presence_runtime_rollout_visible")
                ),
                "runtime_topology_consistent": bool(
                    release_signals.get("runtime_topology_consistent")
                ),
                "phase88_gateway_snapshots": {
                    "wechat": getattr(
                        self._wechat_gateway_service,
                        "reliability_snapshot",
                        lambda: {},
                    )(),
                    "feishu": getattr(
                        self._feishu_gateway_service,
                        "reliability_snapshot",
                        lambda: {},
                    )(),
                },
                "phase91_host_governance": dict(phase91.get("details") or {}),
                "phase_docs_present": phase_docs_present,
                "phase_tests_present": phase_tests_present,
            },
        }

    def _phase77(
        self,
        session_diag: dict[str, Any],
        chat_runtime_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        if session_diag.get("delegates_to") != "chat_runtime":
            blockers.append("session_runtime_not_delegating_to_chat_runtime")
        if "run_turn" not in list(session_diag.get("public_entrypoints") or []):
            blockers.append("turn_runtime_entrypoints_incomplete")
        if session_diag.get("ownership_mode") != "proxy_only":
            blockers.append("session_runtime_not_proxy_only")
        if chat_runtime_diag.get("runtime") != "chat_runtime":
            blockers.append("chat_runtime_topology_missing")
        if chat_runtime_diag.get("ownership_mode") != "exclusive_runtime_host":
            blockers.append("chat_runtime_not_exclusive_owner")
        if (
            getattr(self._chat_service._execution._runner, "__self__", None)
            is not self._chat_runtime
        ):
            blockers.append("turn_execution_manager_not_bound_to_chat_runtime")
        compat_methods = [
            "_execute_turn",
            "_execute_turn_impl",
            "_run_model_path",
            "_run_model_path_impl",
            "_complete_without_model",
            "_complete_without_model_impl",
        ]
        if any(hasattr(self._chat_service, name) for name in compat_methods):
            blockers.append("chat_service_still_exposes_runtime_host_methods")
        status = "ready" if not blockers else "blocked"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/session_runtime.py",
                "services/chat-runtime/chat_runtime/runtime.py",
                "apps/local-api/app/services/chat.py",
                "docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md",
            ],
            blockers=blockers,
            next_owner="services/chat-runtime/chat_runtime/runtime.py",
        )

    def _phase86(
        self,
        session_diag: dict[str, Any],
        chat_runtime_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        if session_diag.get("delegates_to") != "chat_runtime":
            blockers.append("session_runtime_not_delegating_to_chat_runtime")
        if session_diag.get("ownership_mode") != "proxy_only":
            blockers.append("session_runtime_not_proxy_only")
        if session_diag.get("state_machine_owner") != "chat_runtime":
            blockers.append("session_runtime_state_machine_owner_mismatch")
        if session_diag.get("event_source") != "chat_runtime":
            blockers.append("session_runtime_event_source_mismatch")
        if chat_runtime_diag.get("runtime") != "chat_runtime":
            blockers.append("chat_runtime_topology_missing")
        if chat_runtime_diag.get("ownership_mode") != "exclusive_runtime_host":
            blockers.append("chat_runtime_not_exclusive_owner")
        if chat_runtime_diag.get("execution_owner") != "chat_runtime":
            blockers.append("chat_runtime_execution_owner_mismatch")
        if chat_runtime_diag.get("state_machine_owner") != "chat_runtime":
            blockers.append("chat_runtime_state_machine_owner_mismatch")
        if chat_runtime_diag.get("event_source") != "chat_runtime":
            blockers.append("chat_runtime_event_source_mismatch")
        compat_methods = [
            "_create_turn_impl",
            "_stream_turn_events_impl",
            "_run_turn_impl",
            "_recover_incomplete_turns_impl",
            "_cancel_turn_impl",
            "_retry_turn_impl",
            "_execute_turn",
            "_execute_turn_impl",
            "_run_model_path",
            "_run_model_path_impl",
            "_complete_without_model",
            "_complete_without_model_impl",
        ]
        if any(hasattr(self._chat_service, name) for name in compat_methods):
            blockers.append("chat_service_still_exposes_runtime_or_compat_impl_methods")
        status = "ready" if not blockers else "blocked"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/session_runtime.py",
                "services/chat-runtime/chat_runtime/runtime.py",
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/api/routes_system.py",
                "docs/开发计划/86-第八十六阶段-ChatRuntime兼容壳瘦身与主链路唯一化.md",
            ],
            blockers=blockers,
            next_owner="services/chat-runtime/chat_runtime/runtime.py",
        )

    def _phase87(self) -> dict[str, Any]:
        blockers: list[str] = []
        response_text = self._read_text("apps/local-api/app/services/chat_response.py")
        direct_routes_text = self._read_text("apps/local-api/app/services/chat_direct_routes_runtime.py")
        composer_text = self._read_text("services/response-composer/response_composer/contracts.py")
        if "action_status_semantics" not in response_text:
            blockers.append("response_plan_missing_canonical_action_status_semantics")
        if "completed_with_evidence" not in composer_text:
            blockers.append("response_composer_missing_completed_with_evidence_contract")
        if "waiting_for_approval" not in direct_routes_text:
            blockers.append("direct_routes_not_using_phase87_status_names")
        if not self._relative_exists(self._PHASE_DOCS["phase87_action_state_semantics"]):
            blockers.append("phase87_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase87_action_state_semantics"]):
            blockers.append("phase87_test_missing")
        status = "ready" if not blockers else "blocked"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase87_action_state_semantics"],
                "apps/local-api/app/services/chat_response.py",
                "apps/local-api/app/services/chat_direct_routes_runtime.py",
                "services/response-composer/response_composer/contracts.py",
                self._PHASE_TESTS["phase87_action_state_semantics"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_response.py",
            details={
                "canonical_action_state_owner": "action_status_semantics",
                "completed_evidence_gate": "completed_with_evidence",
                "approval_wait_status": "waiting_for_approval",
            },
        )

    def _phase88(
        self,
        wechat_diag: dict[str, Any],
        feishu_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        context_text = self._read_text("apps/local-api/app/services/channel_session_context.py")
        ingress_runtime_text = self._read_text(
            "apps/local-api/app/services/channel_ingress_runtime.py"
        )
        reliability_text = self._read_text("apps/local-api/app/services/channel_reliability.py")
        wechat_text = self._read_text("apps/local-api/app/services/wechat_gateway.py")
        feishu_text = self._read_text("apps/local-api/app/services/feishu_gateway.py")
        if wechat_diag.get("phase88_reliability_contract") != "phase88.channel_reliability.v1":
            blockers.append("wechat_phase88_reliability_contract_missing")
        if feishu_diag.get("phase88_reliability_contract") != "phase88.channel_reliability.v1":
            blockers.append("feishu_phase88_reliability_contract_missing")
        if "inbound_event_id" not in context_text or "inbound_event_id" not in ingress_runtime_text:
            blockers.append("inbound_correlation_contract_missing")
        if "wrong_conversation_reuse" not in reliability_text or "wrong_reuse_payload" not in wechat_text or "wrong_reuse_payload" not in feishu_text:
            blockers.append("conversation_binding_consistency_checks_missing")
        if "turn_completed_but_delivery_binding_missing" not in wechat_text or "turn_completed_but_delivery_binding_missing" not in feishu_text:
            blockers.append("delivery_binding_completeness_not_visible")
        if not self._relative_exists(self._PHASE_DOCS["phase88_channel_reliability"]):
            blockers.append("phase88_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase88_channel_reliability"]):
            blockers.append("phase88_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase88_channel_reliability"],
                "apps/local-api/app/services/channel_reliability.py",
                "apps/local-api/app/services/channel_ingress_runtime.py",
                "apps/local-api/app/services/channel_session_context.py",
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
                self._PHASE_TESTS["phase88_channel_reliability"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/channel_reliability.py",
            details={
                "phase88_contract_version": "phase88.channel_reliability.v1",
                "taxonomy": [
                    "no_turn",
                    "orphan_turn",
                    "duplicate_turn",
                    "wrong_conversation_reuse",
                ],
                "ready_conditions": [
                    "no_turn taxonomy present",
                    "duplicate inbound suppression present",
                    "conversation binding consistency checks present",
                    "delivery binding completeness visible",
                    "wechat/feishu acceptance present",
                ],
            },
        )

    def _phase89(self) -> dict[str, Any]:
        blockers: list[str] = []
        turn_execution_text = self._read_text(
            "apps/local-api/app/services/chat_turn_execution.py"
        )
        natural_text = self._read_text("apps/local-api/app/services/natural_chat.py")
        quality_text = self._read_text("apps/local-api/app/services/chat_quality.py")
        chat_text = self._read_text("apps/local-api/app/services/chat.py")
        wechat20_summary = self._read_json(
            "docs/测试/聊天主链路/2026-05-07-wechat-20-scenarios/evidence/summary.json"
        )
        if "deterministic_execution_state_reply" in turn_execution_text:
            blockers.append("execution_state_soft_heuristic_still_terminal")
        if "latest_instruction_override_direct_reply" in turn_execution_text:
            blockers.append("latest_instruction_override_still_terminal")
        if "pending_clarification_followup" in turn_execution_text:
            blockers.append("clarification_followup_still_terminal")
        if "_deterministic_plain_reply(text)" in natural_text:
            blockers.append("natural_plain_reply_shortcut_still_enabled")
        if "phase89_heuristic_runtime" not in chat_text:
            blockers.append("heuristic_inventory_not_visible")
        if "professional_" not in quality_text or "privacy_block" not in quality_text:
            blockers.append("chat_quality_hard_boundary_contract_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase89_false_interception_governance"]):
            blockers.append("phase89_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase89_false_interception_governance"]):
            blockers.append("phase89_test_missing")
        if not wechat20_summary:
            blockers.append("wechat_20_scenarios_summary_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase89_false_interception_governance"],
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/services/chat_turn_execution.py",
                "apps/local-api/app/services/natural_chat.py",
                "apps/local-api/app/services/chat_quality.py",
                "docs/测试/聊天主链路/2026-05-07-wechat-20-scenarios/evidence/summary.json",
                self._PHASE_TESTS["phase89_false_interception_governance"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_turn_execution.py",
            details={
                "phase89_contract_version": "phase89.false_interception_governance.v1",
                "ready_conditions": [
                    "hard guard 与 soft heuristic 已分离",
                    "soft heuristic 不再允许 terminal shortcut",
                    "continuation / latest override / plain analysis 已回收到正式主链",
                    "false interception regression 存在",
                    "wechat 20 scenarios 验收存在",
                ],
                "wechat_20_summary": wechat20_summary,
            },
        )

    def _phase90(
        self,
        *,
        phase85: dict[str, Any],
        phase88: dict[str, Any],
        phase89: dict[str, Any],
        wechat_diag: dict[str, Any],
        feishu_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        chat_text = self._read_text("apps/local-api/app/services/chat.py")
        continuation_text = self._read_text(
            "apps/local-api/app/services/chat_continuation.py"
        )
        phase85_details = dict(phase85.get("details") or {})
        compat_window = dict(phase85_details.get("compat_cleanup_window") or {})
        wechat20_summary = self._read_json(
            "docs/测试/聊天主链路/2026-05-07-wechat-20-scenarios/evidence/summary.json"
        )
        removal_gates = self._phase90_removal_gates(
            phase85=phase85,
            wechat_diag=wechat_diag,
            feishu_diag=feishu_diag,
            chat_text=chat_text,
        )
        if phase85.get("status") != "ready":
            blockers.append("phase85_cleanup_window_not_ready")
        if phase88.get("status") != "ready":
            blockers.append("phase88_channel_reliability_not_ready")
        if phase89.get("status") != "ready":
            blockers.append("phase89_false_interception_not_ready")
        if not all(bool(compat_window.get(key)) for key in compat_window):
            blockers.append("compat_cleanup_window_not_fully_open")
        if any(not item.get("can_delete_internal_compat_now") for item in removal_gates):
            blockers.append("component_removal_gate_blocked")
        if "markdown 表格" not in continuation_text and "markdown表格" not in continuation_text:
            blockers.append("strict_format_continuity_gate_not_extended")
        if any(
            hasattr(self._chat_service, name)
            for name in (
                "_deterministic_execution_state_reply_text",
                "_deterministic_latest_instruction_reply_text",
                "_maybe_handle_pending_clarification_followup",
            )
        ):
            blockers.append("chat_service_phase90_compat_methods_still_present")
        strict_warn = 0
        for item in list(wechat20_summary.get("items") or []):
            if str(item.get("case_id") or "") == "wechat-20-013" and str(
                item.get("verdict") or ""
            ) != "pass":
                strict_warn += 1
        if strict_warn:
            blockers.append("strict_format_continuity_warn_present")
        if not self._relative_exists(self._PHASE_DOCS["phase90_compat_cleanup_release_gate"]):
            blockers.append("phase90_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase90_compat_cleanup_release_gate"]):
            blockers.append("phase90_test_missing")
        minimum_suite = self._phase90_minimum_suite()
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase90_compat_cleanup_release_gate"],
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/services/chat_turn_execution.py",
                "apps/local-api/app/services/chat_response.py",
                "apps/local-api/app/services/channel_stream_bridge.py",
                "apps/local-api/app/services/release.py",
                "apps/local-api/app/api/routes_system.py",
                self._PHASE_TESTS["phase90_compat_cleanup_release_gate"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details={
                "phase90_contract_version": "phase90.compat_cleanup_release_gate.v1",
                "minimum_suite": minimum_suite,
                "minimum_suite_present": all(
                    self._relative_exists(path) for path in minimum_suite
                ),
                "removal_gates": removal_gates,
                "compat_cleanup_window": compat_window,
                "ready_conditions": [
                    "component removal gates all green",
                    "strict-format continuity gate pass",
                    "phase88 reliability still green",
                    "phase89 false interception still green",
                    "minimum release suite fixed",
                ],
            },
        )

    def _phase90_minimum_suite(self) -> list[str]:
        return [
            self._PHASE_TESTS["phase70_runtime_topology"],
            self._PHASE_TESTS["phase76_chat_mainline_control_plane"],
            self._PHASE_TESTS["phase77_chat_runtime_closure"],
            self._PHASE_TESTS["phase78_session_channel_semantics"],
            self._PHASE_TESTS["phase79_context_gateway_enhancement"],
            self._PHASE_TESTS["phase80_chat_tool_loop"],
            self._PHASE_TESTS["phase81_response_visibility_contract"],
            self._PHASE_TESTS["phase82_ledger_memory_unification"],
            self._PHASE_TESTS["phase83_hook_runtime_contract"],
            self._PHASE_TESTS["phase84_chat_mainline_acceptance_matrix"],
            self._PHASE_TESTS["phase85_execution_batches_control_plane"],
            self._PHASE_TESTS["phase86_runtime_host_uniqueness"],
            self._PHASE_TESTS["phase87_action_state_semantics"],
            self._PHASE_TESTS["phase88_channel_reliability"],
            self._PHASE_TESTS["phase89_false_interception_governance"],
        ]

    def _phase90_removal_gates(
        self,
        *,
        phase85: dict[str, Any],
        wechat_diag: dict[str, Any],
        feishu_diag: dict[str, Any],
        chat_text: str,
    ) -> list[dict[str, Any]]:
        minimum_suite = self._phase90_minimum_suite()
        phase85_details = dict(phase85.get("details") or {})
        compat_window = dict(phase85_details.get("compat_cleanup_window") or {})
        base_statuses = {
            "phase85_execution_batches": str(phase85.get("status") or "blocked"),
        }
        gates = [
            {
                "component": "chat_service",
                "delete_priority": 1,
                "retained_shell": True,
                "can_delete_internal_compat_now": not any(
                    token in chat_text
                    for token in (
                        "def _deterministic_execution_state_reply_text",
                        "def _deterministic_latest_instruction_reply_text",
                        "def _maybe_handle_pending_clarification_followup",
                    )
                ),
                "blocked_by": [],
                "retained_reason": "public facade api and dependency host remain",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses
                | {
                    "phase84_acceptance_matrix": "ready",
                    "phase86_runtime_host_uniqueness": "ready",
                    "phase89_false_interception_governance": "ready",
                },
                "pre_delete_checks": [
                    "phase84_acceptance_matrix green",
                    "phase86_runtime_host_uniqueness ready",
                ],
                "post_delete_smokes": [
                    "test_phase84_chat_mainline_acceptance_matrix.py",
                    "test_phase89_false_interception_governance.py",
                ],
                "rollback_signal": "chat_mainline_readiness.phase90 chat_service gate red",
            },
            {
                "component": "wechat_gateway",
                "delete_priority": 2,
                "retained_shell": True,
                "can_delete_internal_compat_now": bool(wechat_diag.get("fallback_removed"))
                and compat_window.get("phase77_batch1_removal_open", False),
                "blocked_by": []
                if bool(wechat_diag.get("fallback_removed"))
                else ["gateway_fallback_not_removed"],
                "retained_reason": "provider ingress shell remains public",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses
                | {
                    "phase88_channel_reliability": "ready",
                },
                "pre_delete_checks": [
                    "phase88_channel_reliability ready",
                    "wechat gateway fallback removed",
                ],
                "post_delete_smokes": [
                    "test_phase88_channel_reliability.py",
                    "test_phase84_chat_mainline_acceptance_matrix.py",
                ],
                "rollback_signal": "phase88_channel_reliability turns partial",
            },
            {
                "component": "feishu_gateway",
                "delete_priority": 2,
                "retained_shell": True,
                "can_delete_internal_compat_now": bool(feishu_diag.get("fallback_removed"))
                and compat_window.get("phase77_batch1_removal_open", False),
                "blocked_by": []
                if bool(feishu_diag.get("fallback_removed"))
                else ["gateway_fallback_not_removed"],
                "retained_reason": "provider ingress shell remains public",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses
                | {
                    "phase88_channel_reliability": "ready",
                },
                "pre_delete_checks": [
                    "phase88_channel_reliability ready",
                    "feishu gateway fallback removed",
                ],
                "post_delete_smokes": [
                    "test_phase88_channel_reliability.py",
                    "test_phase84_chat_mainline_acceptance_matrix.py",
                ],
                "rollback_signal": "phase88_channel_reliability turns partial",
            },
            {
                "component": "chat_response_helper",
                "delete_priority": 3,
                "retained_shell": True,
                "can_delete_internal_compat_now": self._chat_response_finalize_removed(),
                "blocked_by": []
                if self._chat_response_finalize_removed()
                else ["legacy_finalize_compat_still_present"],
                "retained_reason": "visible response coordinator remains first-class helper",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses
                | {
                    "phase81_response_visibility": "ready",
                },
                "pre_delete_checks": ["phase81_response_visibility ready"],
                "post_delete_smokes": [
                    "test_phase81_response_visibility_contract.py",
                ],
                "rollback_signal": "response_plan visible authority drifts from plain_text",
            },
            {
                "component": "chat_model_helper",
                "delete_priority": 4,
                "retained_shell": True,
                "can_delete_internal_compat_now": True,
                "blocked_by": [],
                "retained_reason": "prompt/model assembly helper still needed",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses,
                "pre_delete_checks": ["phase80_tool_loop ready"],
                "post_delete_smokes": [
                    "test_phase80_chat_tool_loop.py",
                ],
                "rollback_signal": "model orchestration path loses prompt contract metadata",
            },
            {
                "component": "chat_context_helper",
                "delete_priority": 5,
                "retained_shell": True,
                "can_delete_internal_compat_now": True,
                "blocked_by": [],
                "retained_reason": "context redaction diagnostic helper still named",
                "required_suites": minimum_suite,
                "required_phase_statuses": base_statuses,
                "pre_delete_checks": ["phase79_context_gateway_enhancement ready"],
                "post_delete_smokes": [
                    "test_phase79_context_gateway_enhancement.py",
                ],
                "rollback_signal": "context redaction diagnostics become incomplete",
            },
        ]
        return gates

    def _phase91_budget_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "component": "chat_service",
                "path": "apps/local-api/app/services/chat.py",
                "size_budget_lines": 2800,
                "target_status": "facade_shell_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/chat.py",
                    [
                        "def _phase91_legacy_",
                        "def _looks_like_explicit_continuation",
                        "def _looks_like_plain_analysis_request",
                        "def _looks_like_latest_instruction_override",
                        "def _looks_like_short_followup",
                    ],
                    target="facade_shell_only",
                ),
            },
            {
                "component": "natural_chat",
                "path": "apps/local-api/app/services/natural_chat.py",
                "size_budget_lines": 950,
                "target_status": "runtime_surface_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/natural_chat.py",
                    [
                        "def _phase91_legacy_",
                        "def _looks_like_resolution",
                        "def _is_confirm",
                        "def _is_deny",
                        "def _is_edit",
                        "def _looks_like_new_action_request",
                    ],
                    target="runtime_surface_only",
                ),
            },
            {
                "component": "brain_decision",
                "path": "apps/local-api/app/services/brain_decision.py",
                "size_budget_lines": 950,
                "target_status": "decision_orchestrator_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/brain_decision.py",
                    [
                        "def _phase91_legacy_",
                        "def _intent_decision(",
                        "def _mode_decision(",
                        "def _context_decision(",
                        "def _clarification_decision(",
                    ],
                    target="decision_orchestrator_only",
                ),
            },
            {
                "component": "wechat_gateway",
                "path": "apps/local-api/app/services/wechat_gateway.py",
                "size_budget_lines": 1600,
                "target_status": "provider_shell_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/wechat_gateway.py",
                    [
                        "def _normalize_wechat_event",
                        "def _wechat_worker_health_payload",
                    ],
                    target="provider_shell_only",
                ),
            },
            {
                "component": "feishu_gateway",
                "path": "apps/local-api/app/services/feishu_gateway.py",
                "size_budget_lines": 900,
                "target_status": "provider_shell_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/feishu_gateway.py",
                    [
                        "def _normalize_feishu_event",
                        "async def _provider_health",
                    ],
                    target="provider_shell_only",
                ),
            },
        ]

    def _phase91_ownership_status(
        self,
        path: str,
        legacy_markers: list[str],
        *,
        target: str,
    ) -> str:
        text = self._read_text(path)
        if not text:
            return "missing"
        if any(marker in text for marker in legacy_markers):
            return "legacy_residue_present"
        return target

    def _count_lines(self, path: str) -> int:
        text = self._read_text(path)
        if not text:
            return 0
        return len(text.splitlines())

    def _phase91(self) -> dict[str, Any]:
        blockers: list[str] = []
        specs = self._phase91_budget_specs()
        component_details: list[dict[str, Any]] = []
        budget_exceeded_components: list[str] = []
        allowed_to_grow_violations: list[str] = []
        ownership_split_status_by_component: dict[str, str] = {}
        for item in specs:
            current_size = self._count_lines(item["path"])
            within_budget = current_size <= int(item["size_budget_lines"])
            ownership_status = str(item["status"])
            growth_gate = "pass" if within_budget and ownership_status == item["target_status"] else "fail"
            component_details.append(
                {
                    "component": item["component"],
                    "path": item["path"],
                    "size_budget_lines": item["size_budget_lines"],
                    "current_size_lines": current_size,
                    "growth_gate": growth_gate,
                    "ownership_split_status": ownership_status,
                    "target_status": item["target_status"],
                }
            )
            ownership_split_status_by_component[item["component"]] = ownership_status
            if not within_budget:
                budget_exceeded_components.append(item["component"])
            if ownership_status != item["target_status"]:
                allowed_to_grow_violations.append(item["component"])
        if not self._relative_exists(
            self._PHASE_DOCS["phase91_host_decomposition_governance"]
        ):
            blockers.append("phase91_doc_missing")
        if not self._relative_exists(
            self._PHASE_TESTS["phase91_host_decomposition_governance"]
        ):
            blockers.append("phase91_test_missing")
        if budget_exceeded_components:
            blockers.append("host_size_budget_exceeded")
        if allowed_to_grow_violations:
            blockers.append("ownership_split_status_not_met")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase91_host_decomposition_governance"],
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/services/natural_chat.py",
                "apps/local-api/app/services/brain_decision.py",
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
                self._PHASE_TESTS["phase91_host_decomposition_governance"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details={
                "phase91_contract_version": "phase91.host_decomposition_governance.v1",
                "host_size_gate": "budget_and_ownership",
                "host_components": component_details,
                "ownership_split_status_by_component": ownership_split_status_by_component,
                "allowed_to_grow_violations": allowed_to_grow_violations,
                "budget_exceeded_components": budget_exceeded_components,
                "ready_conditions": [
                    "chat.py facade shell only",
                    "natural_chat.py runtime surface only",
                    "brain_decision.py decision orchestrator only",
                    "gateway main files provider shell only",
                    "size budgets enforced by readiness and release",
                ],
            },
        )

    def _phase78(
        self,
        channel_diag: dict[str, Any],
        channel_semantics_diag: dict[str, Any],
        wechat_diag: dict[str, Any],
        feishu_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        if channel_diag.get("runtime") != "channel_ingress_runtime":
            blockers.append("channel_ingress_runtime_missing")
        if channel_semantics_diag.get("runtime") != "channel_session_semantics":
            blockers.append("channel_session_semantics_runtime_missing")
        if wechat_diag.get("ingress_runtime") != "channel_ingress_runtime":
            blockers.append("wechat_gateway_not_bound_to_channel_ingress_runtime")
        if feishu_diag.get("ingress_runtime") != "channel_ingress_runtime":
            blockers.append("feishu_gateway_not_bound_to_channel_ingress_runtime")
        if not all(
            diag.get("session_context_runtime") == "channel_session_context"
            for diag in (wechat_diag, feishu_diag)
        ):
            blockers.append("channel_session_context_bridge_missing")
        if not all(diag.get("session_semantics_runtime") == "channel_session_semantics" for diag in (wechat_diag, feishu_diag)):
            blockers.append("gateway_session_semantics_runtime_missing")
        if not all(diag.get("fallback_removed") is True for diag in (wechat_diag, feishu_diag)):
            blockers.append("gateway_chat_service_fallback_still_present")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/channel_session_semantics.py",
                "apps/local-api/app/services/channel_ingress_runtime.py",
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/channel_session_semantics.py",
        )

    def _phase79(self, release_signals: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        if not release_signals.get("presence_runtime_rollout_visible"):
            blockers.append("presence_runtime_rollout_not_visible")
        if not release_signals.get("current_message_priority_guarded"):
            blockers.append("current_message_priority_guard_missing")
        if not release_signals.get("context_budget_visible"):
            blockers.append("context_budget_not_exposed")
        if not release_signals.get("context_visibility_visible"):
            blockers.append("context_visibility_not_exposed")
        if not release_signals.get("phase79_context_gateway_test_present"):
            blockers.append("phase79_test_missing")
        if not release_signals.get("context_gateway_layering_visible"):
            blockers.append("context_gateway_layering_not_visible")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/context_gateway.py",
                "apps/local-api/app/services/context_budget.py",
                "apps/local-api/app/services/context_visibility.py",
                "apps/local-api/tests/test_phase79_context_gateway_enhancement.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/context_gateway.py",
        )

    def _phase80(self, tool_diag: dict[str, Any], browser_diag: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        terminal_diag = dict(tool_diag.get("terminal") or {})
        browser_runtime_diag = dict(tool_diag.get("browser") or {})
        if not terminal_diag.get("queue_enabled"):
            blockers.append("terminal_queue_not_visible")
        if tool_diag.get("dispatcher") != "tool_dispatcher":
            blockers.append("tool_dispatcher_not_bound")
        if tool_diag.get("safety_bridge") != "tool_safety_bridge":
            blockers.append("tool_safety_bridge_not_bound")
        if browser_diag.get("runtime") != "browser_workflow_runtime":
            blockers.append("browser_workflow_runtime_not_exposed")
        replay_store = dict(browser_runtime_diag.get("replay_store") or {})
        page_state_runtime = dict(browser_runtime_diag.get("page_state_runtime") or {})
        if not replay_store.get("latest_page_state_supported"):
            blockers.append("browser_evidence_refs_not_visible")
        if "login_required" not in list(page_state_runtime.get("status_model") or []):
            blockers.append("browser_page_state_semantics_incomplete")
        if not self._relative_exists(self._PHASE_TESTS["phase80_chat_tool_loop"]):
            blockers.append("phase80_chat_tool_loop_test_missing")
        status = "partial" if blockers else "ready"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/tools.py",
                "apps/local-api/app/services/browser_workflow_runtime.py",
                "apps/local-api/app/services/chat_direct_routes_runtime.py",
                "apps/local-api/tests/test_phase80_chat_tool_loop.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_direct_routes_runtime.py",
        )

    def _phase81(self, release_signals: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        if not release_signals.get("prompt_contract_coverage"):
            blockers.append("prompt_contract_coverage_missing")
        if int(release_signals.get("visible_leakage_count") or 0) > 0:
            blockers.append("visible_leakage_detected")
        if not release_signals.get("response_filter_standardized"):
            blockers.append("response_filter_shape_unstable")
        if release_signals.get("gateway_final_text_source") != "channel_stream_bridge":
            blockers.append("gateway_rewrites_visible_reply")
        if not release_signals.get("phase81_response_visibility_contract_present"):
            blockers.append("phase81_response_visibility_contract_missing")
        if not release_signals.get("chat_response_finalize_removed"):
            blockers.append("chat_response_finalize_not_removed")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/chat_response.py",
                "apps/local-api/app/services/channel_stream_bridge.py",
                "services/response-composer/response_composer/contracts.py",
                "apps/local-api/tests/test_phase81_response_visibility_contract.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_response.py",
        )

    def _phase82(self) -> dict[str, Any]:
        blockers: list[str] = []
        memory_source_text = self._read_text("packages/core-types/core_types/memory.py")
        if "captured_at" not in memory_source_text or "tool_call_id" not in memory_source_text:
            blockers.append("memory_source_minimum_fields_missing")
        if not self._relative_exists(
            "apps/local-api/app/db/migrations/051_chat_ledger_memory_unification.sql"
        ):
            blockers.append("phase82_migration_missing")
        if not self._relative_exists("apps/local-api/app/services/chat_run_ledger.py"):
            blockers.append("chat_run_ledger_service_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase82_ledger_memory_unification"]):
            blockers.append("phase82_test_missing")
        if self._chat_run_ledger_service is None:
            blockers.append("chat_run_ledger_service_not_bound")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "packages/core-types/core_types/memory.py",
                "apps/local-api/app/db/migrations/051_chat_ledger_memory_unification.sql",
                "apps/local-api/app/services/chat_run_ledger.py",
                "apps/local-api/tests/test_phase82_ledger_memory_unification.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_run_ledger.py",
        )

    def _phase83(self) -> dict[str, Any]:
        blockers: list[str] = []
        diagnostic = (
            self._chat_hook_runtime.runtime_diagnostic()
            if self._chat_hook_runtime is not None
            else {}
        )
        if diagnostic.get("runtime") != "chat_hook_runtime":
            blockers.append("hook_runtime_missing")
        blocked_stages = set(diagnostic.get("blocked_stages") or [])
        if "before_tool_call" not in blocked_stages:
            blockers.append("before_tool_call_not_enforced")
        if "before_finalize" not in blocked_stages:
            blockers.append("before_finalize_fail_closed_missing")
        if "before_memory_write" not in blocked_stages:
            blockers.append("before_memory_write_source_guard_missing")
        if not diagnostic.get("registered_hooks"):
            blockers.append("hook_trace_audit_contract_missing")
        if not self._relative_exists("apps/local-api/app/services/chat_hook_runtime.py"):
            blockers.append("hook_runtime_not_bound_to_chat_runtime")
        if not self._relative_exists("apps/local-api/tests/test_phase83_hook_runtime_contract.py"):
            blockers.append("phase83_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/chat_hook_runtime.py",
                "apps/local-api/tests/test_phase83_hook_runtime_contract.py",
                "docs/开发计划/83-第八十三阶段-Hook与扩展点契约.md",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_hook_runtime.py",
        )

    def _phase84(
        self,
        release_signals: dict[str, Any],
        phase_tests_present: dict[str, bool],
        prior_phase_readiness: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        required_signal_keys = {
            "runtime_topology_consistent",
            "response_filter_standardized",
            "visible_leakage_count",
            "current_message_priority_guarded",
        }
        if not required_signal_keys.issubset(release_signals):
            blockers.append("chat_mainline_signal_summary_missing")
        if not phase_tests_present.get("phase84_chat_mainline_acceptance_matrix"):
            blockers.append("phase84_matrix_test_missing")
        required_phase_tests = (
            "phase77_chat_runtime_closure",
            "phase78_session_channel_semantics",
            "phase79_context_gateway_enhancement",
            "phase80_chat_tool_loop",
            "phase81_response_visibility_contract",
            "phase82_ledger_memory_unification",
            "phase83_hook_runtime_contract",
        )
        if not all(phase_tests_present.get(name) for name in required_phase_tests):
            blockers.append("phase77_to_phase83_acceptance_coverage_incomplete")
        core_statuses = {
            phase: str(details.get("status") or "blocked")
            for phase, details in prior_phase_readiness.items()
        }
        if any(status == "blocked" for status in core_statuses.values()):
            blockers.append("phase77_to_phase83_acceptance_coverage_incomplete")
        if not (
            phase_tests_present.get("phase78_session_channel_semantics")
            and self._relative_exists("apps/local-api/tests/test_phase54_wechat_gateway_full_link.py")
            and self._relative_exists("apps/local-api/tests/test_phase66_feishu_channel.py")
        ):
            blockers.append("channel_acceptance_coverage_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase60_turn_recovery"]):
            blockers.append("recovery_acceptance_coverage_missing")
        if not (
            phase_tests_present.get("phase68_quality_gate")
            and phase_tests_present.get("phase81_response_visibility_contract")
            and phase_tests_present.get("phase83_hook_runtime_contract")
        ):
            blockers.append("security_acceptance_coverage_missing")
        matrix_groups = {
            "runtime_acceptance": (
                core_statuses.get("phase77_runtime_closure") == "ready"
                and phase_tests_present.get("phase70_runtime_topology", False)
            ),
            "channel_acceptance": (
                core_statuses.get("phase78_session_channel_semantics") == "ready"
                and self._relative_exists("apps/local-api/tests/test_phase54_wechat_gateway_full_link.py")
                and self._relative_exists("apps/local-api/tests/test_phase66_feishu_channel.py")
            ),
            "tool_loop_acceptance": core_statuses.get("phase80_tool_loop") in {"ready", "partial"},
            "response_visibility_acceptance": core_statuses.get("phase81_response_visibility")
            in {"ready", "partial"},
            "ledger_hook_acceptance": (
                core_statuses.get("phase82_ledger_memory") in {"ready", "partial"}
                and core_statuses.get("phase83_hooks") in {"ready", "partial"}
            ),
            "recovery_failure_acceptance": self._relative_exists(
                self._PHASE_TESTS["phase60_turn_recovery"]
            ),
            "security_honesty_acceptance": (
                phase_tests_present.get("phase68_quality_gate", False)
                and phase_tests_present.get("phase81_response_visibility_contract", False)
                and int(release_signals.get("visible_leakage_count") or 0) == 0
            ),
        }
        details = {
            "acceptance_matrix_version": "phase84.chat_mainline_acceptance_matrix.v1",
            "phase77_to_phase83_statuses": core_statuses,
            "acceptance_groups": matrix_groups,
            "matrix_groups": sorted(matrix_groups),
            "matrix_sources": [
                self._PHASE_TESTS["phase84_chat_mainline_acceptance_matrix"],
                self._PHASE_TESTS["phase77_chat_runtime_closure"],
                self._PHASE_TESTS["phase78_session_channel_semantics"],
                self._PHASE_TESTS["phase79_context_gateway_enhancement"],
                self._PHASE_TESTS["phase80_chat_tool_loop"],
                self._PHASE_TESTS["phase81_response_visibility_contract"],
                self._PHASE_TESTS["phase82_ledger_memory_unification"],
                self._PHASE_TESTS["phase83_hook_runtime_contract"],
                self._PHASE_TESTS["phase60_turn_recovery"],
                "apps/local-api/tests/test_phase54_wechat_gateway_full_link.py",
                "apps/local-api/tests/test_phase66_feishu_channel.py",
            ],
            "coverage_gaps": sorted(set(blockers)),
            "blocking_reasons": sorted(set(blockers)),
        }
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "docs/开发计划/84-第八十四阶段-聊天主链路测试与验收矩阵.md",
                self._PHASE_TESTS["phase84_chat_mainline_acceptance_matrix"],
                self._PHASE_TESTS["phase77_chat_runtime_closure"],
                self._PHASE_TESTS["phase78_session_channel_semantics"],
                self._PHASE_TESTS["phase80_chat_tool_loop"],
                self._PHASE_TESTS["phase81_response_visibility_contract"],
                self._PHASE_TESTS["phase82_ledger_memory_unification"],
                self._PHASE_TESTS["phase83_hook_runtime_contract"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details=details,
        )

    def _phase85(
        self,
        skill_diag: dict[str, Any],
        mcp_diag: dict[str, Any],
        *,
        prior_phase_readiness: dict[str, dict[str, Any]],
        phase84_readiness: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        if skill_diag.get("execution") != "skill_runtime":
            blockers.append("skill_runtime_split_not_visible")
        if mcp_diag.get("conversation_bridge") != "mcp_conversation_bridge":
            blockers.append("mcp_conversation_bridge_not_visible")
        details = self._phase85_details(
            skill_diag=skill_diag,
            mcp_diag=mcp_diag,
            prior_phase_readiness=prior_phase_readiness,
            phase84_readiness=phase84_readiness,
        )
        blockers.extend(
            reason
            for reason in details["current_batch_blockers"]
            if reason not in blockers
        )
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md",
                "apps/local-api/app/services/chat_mainline_readiness.py",
                "apps/local-api/tests/test_phase85_execution_batches_control_plane.py",
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details=details,
        )

    def _phase85_details(
        self,
        *,
        skill_diag: dict[str, Any],
        mcp_diag: dict[str, Any],
        prior_phase_readiness: dict[str, dict[str, Any]],
        phase84_readiness: dict[str, Any],
    ) -> dict[str, Any]:
        batches: list[dict[str, Any]] = []
        covered_batches: list[str] = []
        blocked_batches: list[str] = []
        current_batch_blockers: list[str] = []
        phase_to_batch_status = {
            "ready": "covered",
            "partial": "in_progress",
            "blocked": "blocked",
        }
        if not self._relative_exists(self._PHASE_TESTS["phase85_execution_batches_control_plane"]):
            current_batch_blockers.append("batch_test_set_incomplete")
        for batch in self._PHASE85_BATCHES:
            phase_details = prior_phase_readiness[batch["phase_key"]]
            batch_status = phase_to_batch_status.get(str(phase_details.get("status")), "blocked")
            if batch_status == "covered":
                covered_batches.append(batch["batch_id"])
            else:
                blocked_batches.append(batch["batch_id"])
            batch_blockers = list(phase_details.get("blocking_reasons") or [])
            if not all(self._relative_exists(path) for path in batch["minimum_test_files"]):
                batch_blockers.append("batch_test_set_incomplete")
            if batch_status == "blocked" and "phase_dependency_blocked" not in batch_blockers:
                batch_blockers.append("phase_dependency_blocked")
            batches.append(
                {
                    "batch_id": batch["batch_id"],
                    "title": batch["title"],
                    "status": batch_status,
                    "depends_on": list(batch["depends_on"]),
                    "entry_modules": list(batch["entry_modules"]),
                    "owned_capabilities": list(batch["owned_capabilities"]),
                    "blocked_capabilities": list(batch["blocked_capabilities"]),
                    "minimum_test_files": list(batch["minimum_test_files"]),
                    "compat_shells_allowed": list(batch["compat_shells_allowed"]),
                    "removal_gate": list(batch["removal_gate"]),
                    "blocking_reasons": batch_blockers,
                }
            )
        next_batch = "phase85_fully_covered"
        for batch in batches:
            if batch["status"] != "covered":
                next_batch = str(batch["batch_id"])
                current_batch_blockers = list(batch["blocking_reasons"])
                break
        compat_cleanup_window = {
            "phase84_acceptance_ready": phase84_readiness.get("status") == "ready",
            "phase77_batch1_removal_open": any(
                batch["batch_id"] == "batch1_runtime_entry_closure" and batch["status"] == "covered"
                for batch in batches
            )
            and phase84_readiness.get("status") == "ready",
            "phase81_batch5_removal_open": any(
                batch["batch_id"] == "batch5_response_visibility_governance"
                and batch["status"] == "covered"
                for batch in batches
            )
            and phase84_readiness.get("status") == "ready",
            "phase83_batch7_removal_open": any(
                batch["batch_id"] == "batch7_hook_contract_integration"
                and batch["status"] == "covered"
                for batch in batches
            )
            and phase84_readiness.get("status") == "ready",
        }
        if next_batch != "phase85_fully_covered" and not current_batch_blockers:
            current_batch_blockers = ["phase85_control_plane_not_productized"]
        if not covered_batches:
            current_batch_blockers.append("compat_cleanup_window_not_open")
        return {
            "execution_batches_version": self.EXECUTION_BATCHES_VERSION,
            "recommended_pr_order": list(self._PHASE85_RECOMMENDED_PR_ORDER),
            "batches": batches,
            "next_batch": next_batch,
            "current_batch_blockers": current_batch_blockers,
            "compat_cleanup_window": compat_cleanup_window,
            "release_gate_minimum_suite": [
                self._PHASE_TESTS["phase70_runtime_topology"],
                self._PHASE_TESTS["phase76_chat_mainline_control_plane"],
                self._PHASE_TESTS["phase77_chat_runtime_closure"],
                self._PHASE_TESTS["phase78_session_channel_semantics"],
                self._PHASE_TESTS["phase79_context_gateway_enhancement"],
                self._PHASE_TESTS["phase80_chat_tool_loop"],
                self._PHASE_TESTS["phase81_response_visibility_contract"],
                self._PHASE_TESTS["phase82_ledger_memory_unification"],
                self._PHASE_TESTS["phase83_hook_runtime_contract"],
                self._PHASE_TESTS["phase84_chat_mainline_acceptance_matrix"],
            ],
            "covered_batches": covered_batches,
            "blocked_batches": blocked_batches,
            "skill_runtime_visible": skill_diag.get("execution") == "skill_runtime",
            "mcp_conversation_bridge_visible": (
                mcp_diag.get("conversation_bridge") == "mcp_conversation_bridge"
            ),
        }

    def _phase_item(
        self,
        *,
        status: str,
        sources: list[str],
        blockers: list[str],
        next_owner: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "source_of_truth": sources,
            "blocking_reasons": blockers,
            "next_owner_module": next_owner,
            "details": details or {},
        }

    def _relative_exists(self, relative_path: str) -> bool:
        return (self._root_dir / relative_path).exists()

    def _evidence_ref(self, path: str, ref_type: str) -> dict[str, Any]:
        return {"type": ref_type, "path": path}

    def _presence_runtime_rollout_visible(self) -> bool:
        chat_path = self._root_dir / "apps/local-api/app/services/chat.py"
        if not chat_path.exists():
            return False
        text = chat_path.read_text(encoding="utf-8")
        return all(
            marker in text
            for marker in ("advisory_mode", "quality_takeover_scope", "fallback_reason_codes")
        )

    def _response_filter_standardized(self) -> bool:
        text = self._read_text("apps/local-api/app/services/chat_safety.py")
        return all(
            marker in text
            for marker in (
                "visible_text",
                "filtered_segments",
                "suppression_reason_codes",
                "final_from_filtered_delta",
            )
        )

    def _gateway_final_text_source(self) -> str:
        text = self._read_text("apps/local-api/app/services/channel_stream_bridge.py")
        if "response_plan_plain_text" in text and "final_text_details" in text:
            return "channel_stream_bridge"
        return "unknown"

    def _chat_response_finalize_removed(self) -> bool:
        file_removed = not self._relative_exists(
            "apps/local-api/app/services/chat_response_finalize.py"
        )
        topology_text = self._read_text("apps/local-api/app/api/routes_system.py")
        topology_cleaned = '"host_files=["chat_response_finalize.py"]' not in topology_text
        return file_removed and topology_cleaned

    def _read_text(self, relative_path: str) -> str:
        path = self._root_dir / relative_path
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _read_json(self, relative_path: str) -> dict[str, Any]:
        text = self._read_text(relative_path)
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _runtime_topology_consistent(
        self,
        *,
        session_diag: dict[str, Any],
        channel_diag: dict[str, Any],
        wechat_diag: dict[str, Any],
        feishu_diag: dict[str, Any],
    ) -> bool:
        return (
            session_diag.get("delegates_to") == "chat_runtime"
            and channel_diag.get("runtime") == "channel_ingress_runtime"
            and wechat_diag.get("ingress_runtime") == "channel_ingress_runtime"
            and feishu_diag.get("ingress_runtime") == "channel_ingress_runtime"
        )
