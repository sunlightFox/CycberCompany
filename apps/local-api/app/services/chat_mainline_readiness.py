from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.extensions import _PHASE115_GOLDEN_BUNDLE_SPECS
from app.services.extensions_compat import import_extension_from_root
from app.services.gate_signal_plane import smoke_signal_suite_summary


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
        "phase101_extension_capability_runtime": "docs/开发计划/101-第一百零一阶段-扩展兼容层与渠道平台插件化闭环.md",
        "phase102_video_workflow_closure": "docs/开发计划/102-第一百零二阶段-视频理解剪辑执行域聚焦打深与媒体工作流闭环.md",
        "phase103_task_closure_gate": "docs/开发计划/103-第一百零三阶段-闭环成功率度量体系与真实任务门禁.md",
        "phase104_check_script_recovery": "docs/开发计划/104-第一百零四阶段-质量门禁修复与检查脚本恢复.md",
        "phase105_gate_signal_plane_governance": "docs/开发计划/105-第一百零五阶段-扩展兼容层稳定化与运行时边界收紧.md",
        "phase77_runtime_closure": "docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md",
        "phase86_runtime_host_uniqueness": "docs/开发计划/86-第八十六阶段-ChatRuntime兼容壳瘦身与主链路唯一化.md",
        "phase87_action_state_semantics": "docs/开发计划/87-第八十七阶段-动作状态机与完成态证据统一.md",
        "phase88_channel_reliability": "docs/开发计划/88-第八十八阶段-渠道可靠性与NoTurn治理闭环.md",
        "phase89_false_interception_governance": "docs/开发计划/89-第八十九阶段-聊天质量误拦截治理与规则减法.md",
        "phase90_compat_cleanup_release_gate": "docs/开发计划/90-第九十阶段-主链路兼容逻辑删除窗口与封版门禁收尾.md",
        "phase91_host_decomposition_governance": "docs/开发计划/91-第九十一阶段-ChatRuntime物理拆分与宿主瘦身收尾.md",
        "phase108_runtime_host_decomposition_closure": "docs/开发计划/108-第一百零八阶段-ChatRuntime宿主瘦身与职责拆分封口.md",
        "phase109_real_world_maturity_recheck": "docs/开发计划/109-第一百零九阶段-真实场景长稳运行与成熟度复核.md",
        "phase110_channel_routing_stability": "docs/开发计划/110-第一百一十阶段-渠道路由稳定性与NoTurn根因收敛.md",
        "phase111_task_delivery_evidence": "docs/开发计划/111-第一百一十一阶段-任务交付证据与可见完成态硬化.md",
        "phase112_extension_runtime_sync_closure": "docs/开发计划/112-第一百一十二阶段-扩展运行时同步与执行闭环补齐.md",
        "phase113_check_matrix_execution_restored": "docs/开发计划/113-第一百一十三阶段-质量门禁执行恢复与检查矩阵跑通.md",
        "phase114_mainline_observability_closure": "docs/开发计划/114-第一百一十四阶段-真实场景稳定性与主链路可观测性收口.md",
        "phase92_long_term_memory_recall_governance": "docs/开发计划/92-第九十二阶段-长期记忆检索成熟化与跨会话召回闭环.md",
        "phase107_memory_semantic_contract_unification": "docs/开发计划/107-第一百零七阶段-长期记忆语义契约统一与纠错闭环硬化.md",
        "phase94_failure_experience_governance": "docs/开发计划/94-第九十四阶段-失败经验记忆治理与回归候选闭环.md",
        "phase78_session_channel_semantics": "docs/开发计划/78-第七十八阶段-会话与渠道语义统一.md",
        "phase79_context_gateway_enhancement": "docs/开发计划/79-第七十九阶段-ContextGateway能力化增强.md",
        "phase80_tool_loop": "docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md",
        "phase81_response_visibility": "docs/开发计划/81-第八十一阶段-ResponseComposer与可见性治理统一.md",
        "phase82_ledger_memory": "docs/开发计划/82-第八十二阶段-记忆写入与运行账本统一.md",
        "phase83_hooks": "docs/开发计划/83-第八十三阶段-Hook与扩展点契约.md",
        "phase84_acceptance_matrix": "docs/开发计划/84-第八十四阶段-聊天主链路测试与验收矩阵.md",
        "phase85_execution_batches": "docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md",
        "phase115_golden_extension_packages": "docs/开发计划/115-第一百一十五阶段-黄金扩展包与扩展生态样板闭环.md",
        "phase116_maturity_dashboard_unification": "docs/开发计划/116-第一百一十六阶段-成熟度看板与发布门禁统一化.md",
    }
    _PHASE_TESTS = {
        "phase101_extension_capability_runtime": "apps/local-api/tests/test_phase101_extension_capability_runtime.py",
        "phase102_video_workflow_closure": "apps/local-api/tests/test_phase102_video_workflow_closure.py",
        "phase103_task_closure_gate": "apps/local-api/tests/test_phase103_task_closure_gate.py",
        "phase104_check_script_recovery": "apps/local-api/tests/test_phase104_check_script_recovery.py",
        "phase104_check_report_contract": "apps/local-api/tests/test_phase104_check_report_contract.py",
        "phase105_gate_signal_plane_governance": "apps/local-api/tests/test_phase105_gate_signal_plane_governance.py",
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
        "phase108_runtime_host_decomposition_closure": "apps/local-api/tests/test_phase108_runtime_host_decomposition_closure.py",
        "phase109_real_world_maturity_recheck": "apps/local-api/tests/test_phase109_real_world_maturity_recheck.py",
        "phase110_channel_routing_stability": "apps/local-api/tests/test_phase110_channel_routing_stability.py",
        "phase111_task_delivery_evidence": "apps/local-api/tests/test_phase111_task_delivery_evidence.py",
        "phase112_extension_runtime_sync_closure": "apps/local-api/tests/test_phase112_extension_runtime_sync_closure.py",
        "phase113_check_matrix_execution_restored": "apps/local-api/tests/test_phase113_gate_check_reconciliation.py",
        "phase114_mainline_observability_closure": "apps/local-api/tests/test_phase114_mainline_observability_closure.py",
        "phase92_long_term_memory_recall_governance": "apps/local-api/tests/test_phase92_long_term_memory_recall_governance.py",
        "phase107_memory_semantic_contract_unification": "apps/local-api/tests/test_phase107_memory_semantic_contract_unification.py",
        "phase94_failure_experience_governance": "apps/local-api/tests/test_phase94_failure_experience_governance.py",
        "phase78_session_channel_semantics": "apps/local-api/tests/test_phase78_session_channel_semantics.py",
        "phase79_context_gateway_enhancement": "apps/local-api/tests/test_phase79_context_gateway_enhancement.py",
        "phase80_chat_tool_loop": "apps/local-api/tests/test_phase80_chat_tool_loop.py",
        "phase81_response_visibility_contract": "apps/local-api/tests/test_phase81_response_visibility_contract.py",
        "phase82_ledger_memory_unification": "apps/local-api/tests/test_phase82_ledger_memory_unification.py",
        "phase83_hook_runtime_contract": "apps/local-api/tests/test_phase83_hook_runtime_contract.py",
        "phase84_chat_mainline_acceptance_matrix": "apps/local-api/tests/test_phase84_chat_mainline_acceptance_matrix.py",
        "phase85_execution_batches_control_plane": "apps/local-api/tests/test_phase85_execution_batches_control_plane.py",
        "phase115_golden_extension_packages": "apps/local-api/tests/test_phase115_golden_extension_packages.py",
        "phase116_maturity_dashboard_unification": "apps/local-api/tests/test_phase116_maturity_dashboard_unification.py",
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
        channel_gateway_registry: Any,
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
        self._channel_gateway_registry = channel_gateway_registry
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
        wechat_diag = self._channel_gateway_registry.require("wechat").runtime_diagnostic()
        feishu_diag = self._channel_gateway_registry.require("feishu").runtime_diagnostic()
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
        phase108 = self._phase108(phase91)
        phase_readiness["phase108_runtime_host_decomposition_closure"] = phase108
        phase92 = self._phase92()
        phase_readiness["phase92_long_term_memory_recall_governance"] = phase92
        phase107 = self._phase107()
        phase_readiness["phase107_memory_semantic_contract_unification"] = phase107
        phase94 = self._phase94()
        phase_readiness["phase94_failure_experience_governance"] = phase94
        phase105 = self._phase105()
        phase_readiness["phase105_gate_signal_plane_governance"] = phase105
        phase109 = self._phase109(
            phase88=phase88,
            phase92=phase92,
            phase94=phase94,
            phase105=phase105,
        )
        phase_readiness["phase109_real_world_maturity_recheck"] = phase109
        phase110 = self._phase110(
            phase88=phase88,
            phase109=phase109,
            channel_diag=channel_diag,
            channel_semantics_diag=channel_semantics_diag,
        )
        phase_readiness["phase110_channel_routing_stability"] = phase110
        phase111 = self._phase111(phase87=phase_readiness["phase87_action_state_semantics"])
        phase_readiness["phase111_task_delivery_evidence"] = phase111
        phase112 = self._phase112(phase111=phase111)
        phase_readiness["phase112_extension_runtime_sync_closure"] = phase112
        phase113 = self._phase113(phase105=phase105)
        phase_readiness["phase113_check_matrix_execution_restored"] = phase113
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
        phase114 = await self._phase114(
            phase109=phase109,
            phase110=phase110,
            phase111=phase111,
            phase113=phase113,
            evidence_refs=evidence_refs,
        )
        phase_readiness["phase114_mainline_observability_closure"] = phase114
        phase115 = await self._phase115(
            phase112=phase112,
            phase114=phase114,
        )
        phase_readiness["phase115_golden_extension_packages"] = phase115
        phase116 = await self._phase116(
            phase109=phase109,
            phase110=phase110,
            phase111=phase111,
            phase112=phase112,
            phase113=phase113,
            phase114=phase114,
            phase115=phase115,
            evidence_refs=evidence_refs,
        )
        phase_readiness["phase116_maturity_dashboard_unification"] = phase116
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
                        self._channel_gateway_registry.require("wechat"),
                        "reliability_snapshot",
                        lambda: {},
                    )(),
                    "feishu": getattr(
                        self._channel_gateway_registry.require("feishu"),
                        "reliability_snapshot",
                        lambda: {},
                    )(),
                },
                "phase91_host_governance": dict(phase91.get("details") or {}),
                "phase92_memory_governance": dict(phase92.get("details") or {}),
                "phase94_failure_experience_governance": dict(phase94.get("details") or {}),
                "phase109_real_world_maturity_recheck": dict(phase109.get("details") or {}),
                "phase110_channel_routing_stability": dict(phase110.get("details") or {}),
                "phase111_task_delivery_evidence": dict(phase111.get("details") or {}),
                "phase112_extension_runtime_sync_closure": dict(phase112.get("details") or {}),
                "phase113_check_matrix_execution_restored": dict(phase113.get("details") or {}),
                "phase114_mainline_observability_closure": dict(phase114.get("details") or {}),
                "phase115_golden_extension_packages": dict(phase115.get("details") or {}),
                "phase116_maturity_dashboard_unification": dict(phase116.get("details") or {}),
                "phase_docs_present": phase_docs_present,
                "phase_tests_present": phase_tests_present,
            },
        }

    async def mainline_observability(self) -> dict[str, Any]:
        readiness = await self.diagnostic()
        phase114 = dict(
            dict(readiness.get("phase_readiness") or {}).get(
                "phase114_mainline_observability_closure"
            )
            or {}
        )
        details = dict(phase114.get("details") or {})
        return {
            "contract_version": details.get("phase114_contract_version")
            or "phase114.mainline_observability.v1",
            "status": phase114.get("status") or "partial",
            "ready_conditions": list(details.get("ready_conditions") or []),
            "mainline_rates": dict(details.get("mainline_rates") or {}),
            "segmented_views": dict(details.get("segmented_views") or {}),
            "top_blockers": list(details.get("top_blockers") or []),
            "replay_alignment": dict(details.get("replay_alignment") or {}),
            "evidence_refs": list(details.get("evidence_refs") or readiness.get("evidence_refs") or []),
        }

    async def maturity_dashboard(self) -> dict[str, Any]:
        readiness = await self.diagnostic()
        phase116 = dict(
            dict(readiness.get("phase_readiness") or {}).get(
                "phase116_maturity_dashboard_unification"
            )
            or {}
        )
        details = dict(phase116.get("details") or {})
        return {
            "contract_version": details.get("phase116_contract_version")
            or "phase116.maturity_dashboard.v1",
            "status": phase116.get("status") or "partial",
            "release_readiness": dict(details.get("release_readiness") or {}),
            "dimensions": list(details.get("dimensions") or []),
            "priority_queue": list(details.get("priority_queue") or []),
            "top_blockers": list(details.get("top_blockers") or []),
            "evidence_refs": list(details.get("evidence_refs") or readiness.get("evidence_refs") or []),
            "upstream_contracts": dict(details.get("upstream_contracts") or {}),
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
                "size_budget_lines": 1100,
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
                "size_budget_lines": 2700,
                "target_status": "provider_shell_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/wechat_gateway.py",
                    ["def _phase91_legacy_"],
                    target="provider_shell_only",
                ),
            },
            {
                "component": "feishu_gateway",
                "path": "apps/local-api/app/services/feishu_gateway.py",
                "size_budget_lines": 1400,
                "target_status": "provider_shell_only",
                "status": self._phase91_ownership_status(
                    "apps/local-api/app/services/feishu_gateway.py",
                    ["def _phase91_legacy_"],
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

    def _phase108(self, phase91: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        chat_text = self._read_text("apps/local-api/app/services/chat.py")
        natural_text = self._read_text("apps/local-api/app/services/natural_chat.py")
        if phase91.get("status") != "ready":
            blockers.append("phase91_not_ready")
        if "class ChatService(ChatFacadeShellMixin)" not in chat_text:
            blockers.append("chat_service_shell_mixin_missing")
        if "from app.services.natural_chat_response_plan import (" not in natural_text:
            blockers.append("natural_chat_response_plan_not_extracted")
        if not self._relative_exists(self._PHASE_DOCS["phase108_runtime_host_decomposition_closure"]):
            blockers.append("phase108_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase108_runtime_host_decomposition_closure"]):
            blockers.append("phase108_test_missing")
        if not self._relative_exists("apps/local-api/app/services/chat_facade_shell.py"):
            blockers.append("chat_facade_shell_missing")
        if not self._relative_exists("apps/local-api/app/services/natural_chat_response_plan.py"):
            blockers.append("natural_chat_response_plan_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase108_runtime_host_decomposition_closure"],
                "apps/local-api/app/services/chat.py",
                "apps/local-api/app/services/chat_facade_shell.py",
                "apps/local-api/app/services/natural_chat.py",
                "apps/local-api/app/services/natural_chat_response_plan.py",
                self._PHASE_TESTS["phase108_runtime_host_decomposition_closure"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_facade_shell.py",
            details={
                "phase108_contract_version": "phase108.runtime_host_decomposition_closure.v1",
                "phase91_status": phase91.get("status"),
                "chat_service_host_lines": self._count_lines("apps/local-api/app/services/chat.py"),
                "natural_chat_host_lines": self._count_lines("apps/local-api/app/services/natural_chat.py"),
                "shell_modules": [
                    "apps/local-api/app/services/chat_facade_shell.py",
                    "apps/local-api/app/services/natural_chat_response_plan.py",
                ],
                "ready_conditions": [
                    "phase91 host budget reaches ready",
                    "chat.py delegates facade helpers to mixin shell",
                    "natural_chat.py delegates response-plan helpers to dedicated module",
                    "phase108 regression coverage present",
                ],
            },
        )

    def _phase92(self) -> dict[str, Any]:
        blockers: list[str] = []
        memory_text = self._read_text("packages/core-types/core_types/memory.py")
        schema_text = self._read_text("apps/local-api/app/schemas/memory.py")
        context_text = self._read_text("apps/local-api/app/services/context_gateway.py")
        session_text = self._read_text("apps/local-api/app/services/session_context.py")
        release_text = self._read_text("apps/local-api/app/services/release.py")
        required_memory_tokens = [
            "memory_class",
            "scope_policy",
            "durability",
            "freshness_state",
            "evidence_strength",
            "recall_scope_applied",
        ]
        if not all(token in memory_text for token in required_memory_tokens):
            blockers.append("phase92_memory_contract_missing")
        required_request_tokens = [
            "recall_scope",
            "exclude_conversation_id",
            "include_cross_session",
            "memory_classes",
            "durability_filter",
            "freshness_policy",
        ]
        if not all(token in schema_text for token in required_request_tokens):
            blockers.append("phase92_search_request_contract_missing")
        if "MemorySearchApiRequest(" not in context_text or "exclude_conversation_id=turn[\"conversation_id\"]" not in context_text:
            blockers.append("context_gateway_not_sole_recall_caller")
        if "_canonical_memory_items" not in session_text:
            blockers.append("session_context_not_consuming_canonical_memory")
        if "phase92_long_term_memory_recall_governance" not in release_text:
            blockers.append("phase92_release_gate_missing")
        if not self._relative_exists("apps/local-api/app/db/migrations/052_phase92_memory_recall_governance.sql"):
            blockers.append("phase92_migration_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase92_long_term_memory_recall_governance"]):
            blockers.append("phase92_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase92_long_term_memory_recall_governance"],
                "packages/core-types/core_types/memory.py",
                "apps/local-api/app/schemas/memory.py",
                "apps/local-api/app/services/memory.py",
                "apps/local-api/app/services/context_gateway.py",
                "apps/local-api/app/services/session_context.py",
                self._PHASE_TESTS["phase92_long_term_memory_recall_governance"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/memory.py",
            details={
                "phase92_contract_version": "phase92.long_term_memory_recall.v1",
                "cross_session_scoped_recall_contract": True,
                "canonical_memory_classes": [
                    "preference",
                    "fact",
                    "experience",
                    "transient_working_state",
                ],
                "freshness_policy": [
                    "exclude_stale",
                    "prefer_fresh",
                    "allow_superseded",
                    "allow_expired",
                ],
                "supersede_policy": "latest_correction_wins",
                "context_gateway_memory_owner": "apps/local-api/app/services/context_gateway.py",
                "ready_conditions": [
                    "cross-session scoped recall contract present",
                    "canonical memory classification present",
                    "supersede and stale suppression present",
                    "ContextGateway is sole recall caller",
                    "phase92 regression coverage present",
                ],
            },
        )

    def _phase107(self) -> dict[str, Any]:
        blockers: list[str] = []
        memory_text = self._read_text("packages/core-types/core_types/memory.py")
        service_text = self._read_text("apps/local-api/app/services/memory.py")
        release_text = self._read_text("apps/local-api/app/services/release.py")
        required_memory_tokens = [
            "memory_contract_version",
            "correction_status",
            "supersedes",
            "superseded_by",
            "freshness_state",
        ]
        if not all(token in memory_text for token in required_memory_tokens):
            blockers.append("phase107_memory_contract_missing")
        required_service_tokens = [
            "MEMORY_SEMANTIC_CONTRACT_VERSION",
            "_correction_status",
            "memory_semantic_contract_version",
            "MemorySearchFilteredItem(",
        ]
        if not all(token in service_text for token in required_service_tokens):
            blockers.append("phase107_memory_service_contract_missing")
        if "phase107_memory_semantic_contract_unification" not in release_text:
            blockers.append("phase107_release_gate_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase107_memory_semantic_contract_unification"]):
            blockers.append("phase107_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase107_memory_semantic_contract_unification"]):
            blockers.append("phase107_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase107_memory_semantic_contract_unification"],
                "packages/core-types/core_types/memory.py",
                "apps/local-api/app/services/memory.py",
                self._PHASE_TESTS["phase107_memory_semantic_contract_unification"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/memory.py",
            details={
                "phase107_contract_version": "phase107.memory_semantic_contract.v1",
                "memory_semantic_contract_version": "phase107.memory_semantic_contract.v1",
                "status_fields": [
                    "status",
                    "freshness_state",
                    "supersedes",
                    "superseded_by",
                    "correction_status",
                ],
                "filtered_reason_contract": True,
                "correction_closure_contract": "new_memory_wins_old_memory_explained",
                "ready_conditions": [
                    "search items expose semantic contract version",
                    "filtered entries expose stable state explanations",
                    "correction closure exposes supersede linkage",
                    "phase107 regression coverage present",
                ],
            },
        )

    def _phase94(self) -> dict[str, Any]:
        blockers: list[str] = []
        failure_service = getattr(self._chat_service, "_failure_experience", None)
        runtime_diag = (
            failure_service.runtime_diagnostic()
            if failure_service is not None and hasattr(failure_service, "runtime_diagnostic")
            else {}
        )
        if runtime_diag.get("runtime") != "failure_experience_service":
            blockers.append("failure_experience_service_not_bound")
        if not self._relative_exists("apps/local-api/app/services/failure_experience.py"):
            blockers.append("phase94_service_missing")
        if not self._relative_exists("apps/local-api/app/db/migrations/054_phase94_failure_experience_governance.sql"):
            blockers.append("phase94_migration_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase94_failure_experience_governance"]):
            blockers.append("phase94_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase94_failure_experience_governance"]):
            blockers.append("phase94_test_missing")
        routes_text = self._read_text("apps/local-api/app/api/routes_memory.py")
        if "/failure-experiences" not in routes_text:
            blockers.append("phase94_failure_experience_routes_missing")
        if "/regression-candidates" not in routes_text:
            blockers.append("phase94_regression_candidate_routes_missing")
        if "phase94_failure_experience_governance" not in self._read_text(
            "apps/local-api/app/services/release.py"
        ):
            blockers.append("phase94_release_gate_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "apps/local-api/app/services/failure_experience.py",
                "apps/local-api/app/api/routes_memory.py",
                "apps/local-api/app/db/migrations/054_phase94_failure_experience_governance.sql",
                self._PHASE_TESTS["phase94_failure_experience_governance"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/failure_experience.py",
            details={
                "phase94_contract_version": "phase94.failure_experience_governance.v1",
                "review_actions": runtime_diag.get("review_actions") or [],
                "regression_threshold": runtime_diag.get("regression_threshold") or {},
            },
        )

    def _phase105(self) -> dict[str, Any]:
        blockers: list[str] = []
        signal_summary = smoke_signal_suite_summary()
        signal_suites = list(signal_summary.get("signal_suites") or [])
        signal_paths = [str(item.get("path") or "") for item in signal_suites]
        signal_phase_keys = [
            str(item.get("phase_key") or "")
            for item in signal_suites
            if str(item.get("phase_key") or "")
        ]
        required_phase_keys = [
            "phase90_compat_cleanup_release_gate",
            "phase101_extension_capability_runtime",
            "phase103_task_closure_gate",
            "phase104_check_script_recovery",
            "phase104_check_report_contract",
        ]
        if not signal_summary.get("check_contract_version"):
            blockers.append("phase105_signal_contract_missing")
        if not self._relative_exists("config/gate_signal_plane.json"):
            blockers.append("phase105_signal_manifest_missing")
        if not signal_suites:
            blockers.append("phase105_smoke_signal_suites_missing")
        missing_paths = [path for path in signal_paths if path and not self._relative_exists(path)]
        if missing_paths:
            blockers.append("phase105_signal_suite_paths_missing")
        missing_phase_keys = [
            phase_key for phase_key in required_phase_keys if phase_key not in signal_phase_keys
        ]
        if missing_phase_keys:
            blockers.append("phase105_smoke_backbone_incomplete")
        if not self._relative_exists(self._PHASE_DOCS["phase104_check_script_recovery"]):
            blockers.append("phase104_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase104_check_script_recovery"]):
            blockers.append("phase104_recovery_test_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase104_check_report_contract"]):
            blockers.append("phase104_contract_test_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase105_gate_signal_plane_governance"]):
            blockers.append("phase105_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase105_gate_signal_plane_governance"]):
            blockers.append("phase105_test_missing")
        release_text = self._read_text("apps/local-api/app/services/release.py")
        if "phase105_gate_signal_plane_governance_status" not in release_text:
            blockers.append("phase105_release_summary_missing")
        check_script_text = self._read_text("scripts/check.ps1")
        if "Get-GateSignalProfile" not in check_script_text or "signal_suites" not in check_script_text:
            blockers.append("phase105_check_script_not_bound_to_signal_manifest")
        latest_smoke_report = self._latest_root_check_report(profile="smoke") or {}
        latest_signal_suites = [
            item
            for item in latest_smoke_report.get("signal_suites", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        latest_signal_paths = [str(item.get("path") or "") for item in latest_signal_suites]
        smoke_report_blockers: list[str] = []
        if not latest_smoke_report:
            smoke_report_blockers.append("phase105_latest_smoke_report_missing")
        elif str(latest_smoke_report.get("check_contract_version") or "") != str(
            signal_summary.get("check_contract_version") or ""
        ):
            smoke_report_blockers.append("phase105_latest_smoke_contract_drift")
        if latest_smoke_report and latest_signal_paths != signal_paths:
            smoke_report_blockers.append("phase105_latest_smoke_signal_suite_drift")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                "config/gate_signal_plane.json",
                "scripts/check.ps1",
                "apps/local-api/app/services/release.py",
                self._PHASE_DOCS["phase104_check_script_recovery"],
                self._PHASE_DOCS["phase105_gate_signal_plane_governance"],
                self._PHASE_TESTS["phase104_check_script_recovery"],
                self._PHASE_TESTS["phase104_check_report_contract"],
                self._PHASE_TESTS["phase105_gate_signal_plane_governance"],
            ],
            blockers=blockers,
            next_owner="config/gate_signal_plane.json",
            details={
                "phase105_contract_version": "phase105.gate_signal_plane.v1",
                "check_contract_version": signal_summary.get("check_contract_version"),
                "smoke_suite_id": signal_summary.get("suite_id"),
                "smoke_suite_name": signal_summary.get("suite_name"),
                "smoke_signal_paths": signal_paths,
                "smoke_signal_phase_keys": signal_phase_keys,
                "required_phase_keys": required_phase_keys,
                "missing_signal_paths": missing_paths,
                "missing_phase_keys": missing_phase_keys,
                "smoke_regression_command": ".\\scripts\\check.ps1 -Profile smoke",
                "latest_smoke_report_present": bool(latest_smoke_report),
                "latest_smoke_report_status": (
                    str(latest_smoke_report.get("status") or "not_run")
                    if latest_smoke_report
                    else "not_run"
                ),
                "latest_smoke_contract_match": "phase105_latest_smoke_contract_drift"
                not in smoke_report_blockers,
                "latest_smoke_signal_suite_match": "phase105_latest_smoke_signal_suite_drift"
                not in smoke_report_blockers,
                "latest_smoke_missing_signal_paths": [
                    path for path in signal_paths if path not in latest_signal_paths
                ],
                "latest_smoke_drift_signal_paths": [
                    path for path in latest_signal_paths if path not in signal_paths
                ],
                "latest_smoke_report_blockers": smoke_report_blockers,
            },
        )

    def _phase109(
        self,
        *,
        phase88: dict[str, Any],
        phase92: dict[str, Any],
        phase94: dict[str, Any],
        phase105: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        gateway_snapshots = {
            "wechat": getattr(
                self._channel_gateway_registry.require("wechat"),
                "reliability_snapshot",
                lambda: {},
            )(),
            "feishu": getattr(
                self._channel_gateway_registry.require("feishu"),
                "reliability_snapshot",
                lambda: {},
            )(),
        }
        evidence_bundles = [
            self._phase109_evidence_bundle(
                bundle_id="wechat_50_smoke",
                summary_path=(
                    "docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/"
                    "evidence-smoke/02-summary.json"
                ),
                gap_path=(
                    "docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/"
                    "evidence-smoke/03-gap-list.json"
                ),
            ),
            self._phase109_evidence_bundle(
                bundle_id="wechat_real_smoke",
                summary_path=(
                    "docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/"
                    "evidence-smoke/02-summary.json"
                ),
                gap_path=(
                    "docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/"
                    "evidence-smoke/03-gap-list.json"
                ),
            ),
        ]
        dependency_statuses = {
            "phase88_channel_reliability": str(phase88.get("status") or "missing"),
            "phase92_long_term_memory_recall_governance": str(
                phase92.get("status") or "missing"
            ),
            "phase94_failure_experience_governance": str(phase94.get("status") or "missing"),
            "phase105_gate_signal_plane_governance": str(phase105.get("status") or "missing"),
        }
        if any(status != "ready" for status in dependency_statuses.values()):
            blockers.append("phase109_dependency_not_ready")
        if not self._relative_exists(self._PHASE_DOCS["phase109_real_world_maturity_recheck"]):
            blockers.append("phase109_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase109_real_world_maturity_recheck"]):
            blockers.append("phase109_test_missing")
        if not all(bundle["summary_present"] and bundle["gap_report_present"] for bundle in evidence_bundles):
            blockers.append("phase109_long_run_evidence_missing")
        total_gap_count = sum(int(bundle["gap_count"]) for bundle in evidence_bundles)
        total_p0_gap_count = sum(int(bundle["p0_gap_count"]) for bundle in evidence_bundles)
        total_no_turn_count = sum(int(bundle["no_turn_count"]) for bundle in evidence_bundles)
        evidence_no_turn_group_counts = self._merge_phase109_counts(
            [dict(bundle.get("no_turn_group_counts") or {}) for bundle in evidence_bundles]
        )
        runtime_no_turn_reason_counts = self._phase109_runtime_no_turn_reason_counts(
            gateway_snapshots
        )
        top_runtime_no_turn_reasons = self._phase109_top_counts(runtime_no_turn_reason_counts)
        top_evidence_no_turn_groups = self._phase109_top_counts(evidence_no_turn_group_counts)
        likely_primary_causes = self._phase109_likely_primary_causes(
            top_evidence_no_turn_groups=top_evidence_no_turn_groups,
            top_runtime_no_turn_reasons=top_runtime_no_turn_reasons,
        )
        remediation_queue = [
            {
                "cause_code": str(item.get("cause_code") or ""),
                "classification": str(item.get("classification") or "unknown"),
                "priority": str(item.get("priority") or "p1"),
                "recommended_next_step": str(item.get("recommended_next_step") or ""),
            }
            for item in likely_primary_causes
        ]
        if total_p0_gap_count > 0:
            blockers.append("real_world_evidence_p0_gaps_present")
        if total_no_turn_count > 0:
            blockers.append("channel_long_run_no_turn_present")
        maturity_grade = "ready"
        if blockers:
            core_dependencies_ready = all(status == "ready" for status in dependency_statuses.values())
            evidence_present = all(
                bundle["summary_present"] and bundle["gap_report_present"]
                for bundle in evidence_bundles
            )
            if core_dependencies_ready and evidence_present and total_p0_gap_count == 0:
                maturity_grade = "beta"
            else:
                maturity_grade = "partial"
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase109_real_world_maturity_recheck"],
                "docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/evidence-smoke/02-summary.json",
                "docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/evidence-smoke/03-gap-list.json",
                "docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/evidence-smoke/02-summary.json",
                "docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/evidence-smoke/03-gap-list.json",
                "data/check-reports",
                self._PHASE_TESTS["phase109_real_world_maturity_recheck"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details={
                "phase109_contract_version": "phase109.real_world_maturity_recheck.v1",
                "maturity_grade": maturity_grade,
                "dependency_statuses": dependency_statuses,
                "evidence_bundles": evidence_bundles,
                "long_run_evidence_present": all(
                    bundle["summary_present"] and bundle["gap_report_present"]
                    for bundle in evidence_bundles
                ),
                "blocking_gap_quantification": {
                    "total_gap_count": total_gap_count,
                    "total_p0_gap_count": total_p0_gap_count,
                    "total_no_turn_count": total_no_turn_count,
                },
                "no_turn_diagnostics": {
                    "evidence_no_turn_group_counts": evidence_no_turn_group_counts,
                    "top_evidence_no_turn_groups": top_evidence_no_turn_groups,
                    "runtime_no_turn_reason_counts": runtime_no_turn_reason_counts,
                    "top_runtime_no_turn_reasons": top_runtime_no_turn_reasons,
                    "likely_primary_causes": likely_primary_causes,
                    "remediation_queue": remediation_queue,
                },
                "ready_conditions": [
                    "long-run evidence bundles are present",
                    "phase88/phase92/phase94/phase105 are ready",
                    "real-world evidence has no P0 structural gaps",
                    "real-world evidence has no no-turn routing gaps",
                ],
            },
        )

    def _phase110(
        self,
        *,
        phase88: dict[str, Any],
        phase109: dict[str, Any],
        channel_diag: dict[str, Any],
        channel_semantics_diag: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        gateway_snapshots = {
            "wechat": getattr(
                self._channel_gateway_registry.require("wechat"),
                "reliability_snapshot",
                lambda: {},
            )(),
            "feishu": getattr(
                self._channel_gateway_registry.require("feishu"),
                "reliability_snapshot",
                lambda: {},
            )(),
        }
        if phase88.get("status") != "ready":
            blockers.append("phase88_channel_reliability_not_ready")
        if (
            channel_diag.get("phase110_routing_contract_version")
            != "phase110.channel_routing_stability.v1"
        ):
            blockers.append("channel_ingress_phase110_contract_missing")
        if (
            channel_semantics_diag.get("phase110_routing_contract_version")
            != "phase110.channel_routing_stability.v1"
        ):
            blockers.append("channel_semantics_phase110_contract_missing")
        if not channel_diag.get("supports_route_replay_evidence"):
            blockers.append("channel_ingress_route_replay_evidence_missing")
        if not channel_diag.get("routing_replay_fields"):
            blockers.append("channel_ingress_routing_replay_fields_missing")
        if not channel_semantics_diag.get("route_replay_fields"):
            blockers.append("channel_semantics_route_replay_fields_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase110_channel_routing_stability"]):
            blockers.append("phase110_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase110_channel_routing_stability"]):
            blockers.append("phase110_test_missing")
        runtime_reason_counts = self._phase109_runtime_no_turn_reason_counts(gateway_snapshots)
        runtime_group_counts = self._merge_phase109_counts(
            [
                dict(snapshot.get("no_turn_reason_group_counts") or {})
                for snapshot in gateway_snapshots.values()
            ]
        )
        evidence_group_counts = dict(
            dict(phase109.get("details") or {})
            .get("no_turn_diagnostics", {})
            .get("evidence_no_turn_group_counts")
            or {}
        )
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase110_channel_routing_stability"],
                "apps/local-api/app/services/channel_reliability.py",
                "apps/local-api/app/services/channel_ingress_runtime.py",
                "apps/local-api/app/services/channel_session_context.py",
                "apps/local-api/app/services/channel_session_semantics.py",
                "apps/local-api/app/services/wechat_gateway.py",
                "apps/local-api/app/services/feishu_gateway.py",
                self._PHASE_TESTS["phase110_channel_routing_stability"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/channel_ingress_runtime.py",
            details={
                "phase110_contract_version": "phase110.channel_routing_stability.v1",
                "routing_contract_alignment": {
                    "channel_ingress_runtime": channel_diag.get(
                        "phase110_routing_contract_version"
                    ),
                    "channel_session_semantics": channel_semantics_diag.get(
                        "phase110_routing_contract_version"
                    ),
                },
                "routing_replay_fields": channel_diag.get("routing_replay_fields") or [],
                "session_route_replay_fields": channel_semantics_diag.get(
                    "route_replay_fields"
                )
                or [],
                "route_identity_fields": channel_semantics_diag.get("route_identity_fields")
                or [],
                "no_turn_reason_codes": channel_diag.get("no_turn_reason_codes") or [],
                "runtime_no_turn_reason_counts": runtime_reason_counts,
                "runtime_no_turn_reason_group_counts": runtime_group_counts,
                "evidence_no_turn_group_counts": evidence_group_counts,
                "phase109_open_blockers": list(phase109.get("blocking_reasons") or []),
                "ready_conditions": [
                    "channel ingress runtime exposes routing replay evidence",
                    "session semantics runtime exposes deterministic route identity",
                    "gateway snapshots aggregate no-turn reason groups",
                    "readiness and release summary share the same routing diagnostics",
                ],
            },
        )

    def _phase111(self, *, phase87: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        tasks_text = self._read_text("apps/local-api/app/services/tasks.py")
        workflow_text = self._read_text("apps/local-api/app/services/task_workflow_runtime.py")
        response_text = self._read_text("apps/local-api/app/services/chat_response.py")
        release_text = self._read_text("apps/local-api/app/services/release.py")
        if phase87.get("status") != "ready":
            blockers.append("phase87_action_state_semantics_not_ready")
        if "phase111_deliverable_proof" not in tasks_text:
            blockers.append("task_detail_deliverable_proof_contract_missing")
        if "phase111_completion_semantics" not in tasks_text:
            blockers.append("task_detail_completion_semantics_missing")
        if "phase111.completion_semantics.v1" not in workflow_text:
            blockers.append("task_workflow_completion_contract_missing")
        if "completed_with_evidence" not in response_text:
            blockers.append("response_visible_completion_semantics_not_aligned")
        if "phase111_task_delivery_evidence" not in release_text:
            blockers.append("release_summary_phase111_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase111_task_delivery_evidence"]):
            blockers.append("phase111_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase111_task_delivery_evidence"]):
            blockers.append("phase111_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase111_task_delivery_evidence"],
                "apps/local-api/app/services/tasks.py",
                "apps/local-api/app/services/task_workflow_runtime.py",
                "apps/local-api/app/services/chat_response.py",
                "apps/local-api/app/services/release.py",
                self._PHASE_TESTS["phase111_task_delivery_evidence"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/tasks.py",
            details={
                "phase111_contract_version": "phase111.task_delivery_evidence.v1",
                "minimum_deliverable_proof_contracts": {
                    "repo_local": ["artifact_or_diff", "verification_passed"],
                    "code_hosting": ["remote_artifact_or_receipt", "verification_passed"],
                    "office_productivity": ["typed_output_or_artifact"],
                    "video_workflow": ["render_output_or_media_evidence", "verification_passed"],
                    "content_platform": ["visible_publish_proof"],
                },
                "completion_requires": [
                    "delivery_status",
                    "verification_status",
                    "deliverable_proof",
                    "visible_summary",
                ],
                "blocked_terminal_statuses": [
                    "waiting_approval",
                    "waiting_handoff",
                    "completed_unverified",
                    "failed_verification",
                ],
            },
        )

    def _phase112(self, *, phase111: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        extension_text = self._read_text("apps/local-api/app/services/extensions.py")
        release_text = self._read_text("apps/local-api/app/services/release.py")
        if phase111.get("status") != "ready":
            blockers.append("phase111_task_delivery_evidence_not_ready")
        if "phase112.extension_runtime_snapshot.v1" not in extension_text:
            blockers.append("extension_runtime_snapshot_contract_missing")
        if "runtime_snapshot=diagnostic[\"runtime_snapshot\"]" not in extension_text:
            blockers.append("diagnostics_plan_run_snapshot_not_shared")
        if "_deactivate_extension_mcp" not in extension_text:
            blockers.append("extension_disable_mcp_sync_missing")
        if "_deactivate_extension_tools" not in extension_text:
            blockers.append("extension_disable_tool_sync_missing")
        if "phase112_extension_runtime_sync_closure" not in release_text:
            blockers.append("release_summary_phase112_missing")
        if not self._relative_exists(self._PHASE_DOCS["phase112_extension_runtime_sync_closure"]):
            blockers.append("phase112_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase112_extension_runtime_sync_closure"]):
            blockers.append("phase112_test_missing")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase112_extension_runtime_sync_closure"],
                "apps/local-api/app/services/extensions.py",
                "apps/local-api/app/services/release.py",
                self._PHASE_TESTS["phase112_extension_runtime_sync_closure"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/extensions.py",
            details={
                "phase112_contract_version": "phase112.extension_runtime_sync_closure.v1",
                "runtime_snapshot_contract": "phase112.extension_runtime_snapshot.v1",
                "extension_state_machine": [
                    "installed",
                    "enabled",
                    "bound",
                    "ready",
                    "degraded",
                    "disabled",
                ],
                "sync_closure_requirements": [
                    "shared_runtime_snapshot",
                    "disable_syncs_tools",
                    "disable_syncs_mcp",
                    "diagnostics_plan_run_consistent",
                ],
            },
        )

    def _phase113(self, *, phase105: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        latest_smoke = self._latest_root_check_report(profile="smoke") or {}
        if phase105.get("status") != "ready":
            blockers.append("phase105_gate_signal_plane_not_ready")
        if not self._relative_exists(self._PHASE_DOCS["phase113_check_matrix_execution_restored"]):
            blockers.append("phase113_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase113_check_matrix_execution_restored"]):
            blockers.append("phase113_test_missing")
        if not latest_smoke:
            blockers.append("phase113_latest_smoke_report_missing")
        elif str(latest_smoke.get("status") or "") != "passed":
            blockers.append("phase113_latest_smoke_report_not_passed")
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase113_check_matrix_execution_restored"],
                "scripts/check.ps1",
                "config/gate_signal_plane.json",
                self._PHASE_TESTS["phase113_check_matrix_execution_restored"],
            ],
            blockers=blockers,
            next_owner="scripts/check.ps1",
            details={
                "phase113_contract_version": "phase113.check_matrix_execution_restored.v1",
                "latest_smoke_profile": str(latest_smoke.get("profile") or "not_run"),
                "latest_smoke_status": str(latest_smoke.get("status") or "not_run"),
                "latest_smoke_contract_version": str(
                    latest_smoke.get("check_contract_version") or ""
                ),
                "signal_suite_count": len(list(latest_smoke.get("signal_suites") or [])),
            },
        )

    async def _phase114(
        self,
        *,
        phase109: dict[str, Any],
        phase110: dict[str, Any],
        phase111: dict[str, Any],
        phase113: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        observability = await self._phase114_summary(
            phase109=phase109,
            phase110=phase110,
            phase111=phase111,
            phase113=phase113,
            evidence_refs=evidence_refs,
        )
        blockers: list[str] = list(observability.get("blocking_reasons") or [])
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase114_mainline_observability_closure"],
                "apps/local-api/app/services/chat_mainline_readiness.py",
                "apps/local-api/app/services/release.py",
                "apps/local-api/app/api/routes_system.py",
                self._PHASE_TESTS["phase114_mainline_observability_closure"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details=observability,
        )

    async def _phase114_summary(
        self,
        *,
        phase109: dict[str, Any],
        phase110: dict[str, Any],
        phase111: dict[str, Any],
        phase113: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        repo = self._release_gate_service._repo
        table_names = set(await repo.table_names())
        phase103_summary = await self._release_gate_service._phase103_report_summary(None)
        phase109_details = dict(phase109.get("details") or {})
        phase110_details = dict(phase110.get("details") or {})
        phase111_details = dict(phase111.get("details") or {})
        gateway_snapshots = {
            "wechat": getattr(
                self._channel_gateway_registry.require("wechat"),
                "reliability_snapshot",
                lambda: {},
            )(),
            "feishu": getattr(
                self._channel_gateway_registry.require("feishu"),
                "reliability_snapshot",
                lambda: {},
            )(),
        }
        mainline_rates = await self._phase114_mainline_rates(
            repo=repo,
            table_names=table_names,
            gateway_snapshots=gateway_snapshots,
            phase103_summary=phase103_summary,
        )
        segmented_views = await self._phase114_segmented_views(
            repo=repo,
            table_names=table_names,
            gateway_snapshots=gateway_snapshots,
            phase103_summary=phase103_summary,
        )
        top_blockers = self._phase114_top_blockers(
            phase109_details=phase109_details,
            phase110_details=phase110_details,
            phase103_summary=phase103_summary,
        )
        replay_alignment = {
            "routing_replay_fields_present": bool(
                phase110_details.get("routing_replay_fields")
            )
            and bool(phase110_details.get("session_route_replay_fields")),
            "task_replay_trace_channel_linkable": bool(
                phase110_details.get("routing_replay_fields")
            )
            and bool(phase110_details.get("route_identity_fields"))
            and bool(phase109_details.get("evidence_bundles")),
            "top_blockers_mapped_to_stage": all(
                bool(item.get("impacted_segment")) for item in top_blockers
            ),
            "routing_replay_fields": list(phase110_details.get("routing_replay_fields") or []),
            "session_route_replay_fields": list(
                phase110_details.get("session_route_replay_fields") or []
            ),
            "route_identity_fields": list(phase110_details.get("route_identity_fields") or []),
        }
        missing_metrics = [
            key
            for key, value in mainline_rates.items()
            if not isinstance(value, dict) or value.get("sample_size", 0) <= 0 or value.get("rate") is None
        ]
        ready_conditions = [
            "phase109/phase110/phase111/phase113 are ready",
            "chat mainline observability endpoint exposes a legal contract",
            "release summary and readiness share the same mainline metrics",
            "turn/queue/execution/approval/delivery metrics are all visible",
            "routing-class p0 blockers are cleared from top blockers",
        ]
        blocking_reasons: list[str] = []
        dependency_statuses = {
            "phase109_real_world_maturity_recheck": str(phase109.get("status") or "missing"),
            "phase110_channel_routing_stability": str(phase110.get("status") or "missing"),
            "phase111_task_delivery_evidence": str(phase111.get("status") or "missing"),
            "phase113_check_matrix_execution_restored": str(phase113.get("status") or "missing"),
        }
        if any(status != "ready" for status in dependency_statuses.values()):
            blocking_reasons.append("phase114_dependency_not_ready")
        if not self._relative_exists(self._PHASE_DOCS["phase114_mainline_observability_closure"]):
            blocking_reasons.append("phase114_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase114_mainline_observability_closure"]):
            blocking_reasons.append("phase114_test_missing")
        if missing_metrics:
            blocking_reasons.append("phase114_mainline_metric_visibility_missing")
        if any(
            item.get("impacted_segment") == "routing" and item.get("severity") == "p0"
            for item in top_blockers
        ):
            blocking_reasons.append("phase114_routing_p0_blocker_present")
        if not replay_alignment["routing_replay_fields_present"]:
            blocking_reasons.append("phase114_routing_replay_alignment_missing")
        return {
            "phase114_contract_version": "phase114.mainline_observability.v1",
            "dependency_statuses": dependency_statuses,
            "ready_conditions": ready_conditions,
            "mainline_rates": mainline_rates,
            "segmented_views": segmented_views,
            "top_blockers": top_blockers,
            "replay_alignment": replay_alignment,
            "evidence_refs": evidence_refs,
            "missing_metrics": missing_metrics,
            "phase103_contract_version": str(phase103_summary.get("contract_version") or ""),
            "phase111_contract_version": str(
                phase111_details.get("phase111_contract_version") or ""
            ),
            "blocking_reasons": blocking_reasons,
        }

    async def _phase115(
        self,
        *,
        phase112: dict[str, Any],
        phase114: dict[str, Any],
    ) -> dict[str, Any]:
        details = await self._phase115_summary(
            phase112=phase112,
            phase114=phase114,
        )
        blockers: list[str] = list(details.get("blocking_reasons") or [])
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase115_golden_extension_packages"],
                "config/skill-repositories/fixtures/clawhub-xiaohongshu-content-platform",
                "config/skill-repositories/fixtures/clawhub-github-pr-workflow",
                "config/skill-repositories/fixtures/clawhub-email-draft",
                self._PHASE_TESTS["phase115_golden_extension_packages"],
            ],
            blockers=blockers,
            next_owner="config/skill-repositories/fixtures",
            details=details,
        )

    async def _phase115_summary(
        self,
        *,
        phase112: dict[str, Any],
        phase114: dict[str, Any],
    ) -> dict[str, Any]:
        phase103_summary = await self._release_gate_service._phase103_report_summary(None)
        release_text = self._read_text("apps/local-api/app/services/release.py")
        inventory = [self._phase115_fixture_inventory(bundle_id, spec) for bundle_id, spec in _PHASE115_GOLDEN_BUNDLE_SPECS.items()]
        extension_scorecard = dict(
            dict(phase103_summary.get("per_domain_scorecard") or {}).get("extension_ecosystem") or {}
        )
        coverage = {
            "inventory_count": len(inventory),
            "importable_count": sum(1 for item in inventory if item.get("importable") is True),
            "delivery_template_count": sum(
                1
                for item in inventory
                if dict(item.get("task_delivery_template") or {}).get("artifact_path")
            ),
            "runtime_snapshot_template_count": sum(
                1 for item in inventory if item.get("runtime_snapshot_contract") == "phase112.extension_runtime_snapshot.v1"
            ),
        }
        blockers: list[str] = []
        if phase112.get("status") != "ready":
            blockers.append("phase115_dependency_phase112_not_ready")
        if phase114.get("status") != "ready":
            blockers.append("phase115_dependency_phase114_not_ready")
        if not self._relative_exists(self._PHASE_DOCS["phase115_golden_extension_packages"]):
            blockers.append("phase115_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase115_golden_extension_packages"]):
            blockers.append("phase115_test_missing")
        if "phase115_golden_extension_packages" not in release_text:
            blockers.append("release_summary_phase115_missing")
        if any(item.get("importable") is not True for item in inventory):
            blockers.append("phase115_fixture_import_failed")
        if any(item.get("missing_artifacts") for item in inventory):
            blockers.append("phase115_fixture_structure_incomplete")
        if any(
            not dict(item.get("task_delivery_template") or {}).get("artifact_path")
            for item in inventory
        ):
            blockers.append("phase115_task_delivery_template_missing")
        threshold_status = dict(extension_scorecard.get("threshold_status") or {})
        if not threshold_status.get("final_deliverable_rate", True):
            blockers.append("phase115_extension_ecosystem_deliverable_rate_regressed")
        return {
            "phase115_contract_version": "phase115.golden_extension_packages.v1",
            "package_contract_version": "phase115.golden_extension_package.v1",
            "golden_package_inventory": inventory,
            "inventory_coverage": coverage,
            "diagnostics_contract_fields": [
                "missing_bindings",
                "runtime_sync_missing",
                "external_runtime_required",
            ],
            "runtime_snapshot_contract": "phase112.extension_runtime_snapshot.v1",
            "extension_ecosystem_scorecard": {
                "total_tasks": int(extension_scorecard.get("total_tasks") or 0),
                "final_deliverable_rate": extension_scorecard.get("final_deliverable_rate"),
                "threshold_status": threshold_status,
            },
            "dependency_statuses": {
                "phase112_extension_runtime_sync_closure": str(phase112.get("status") or "missing"),
                "phase114_mainline_observability_closure": str(phase114.get("status") or "missing"),
            },
            "blocking_reasons": blockers,
        }

    async def _phase116(
        self,
        *,
        phase109: dict[str, Any],
        phase110: dict[str, Any],
        phase111: dict[str, Any],
        phase112: dict[str, Any],
        phase113: dict[str, Any],
        phase114: dict[str, Any],
        phase115: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        details = await self._phase116_summary(
            phase109=phase109,
            phase110=phase110,
            phase111=phase111,
            phase112=phase112,
            phase113=phase113,
            phase114=phase114,
            phase115=phase115,
            evidence_refs=evidence_refs,
        )
        blockers = list(details.get("blocking_reasons") or [])
        status = "ready" if not blockers else "partial"
        return self._phase_item(
            status=status,
            sources=[
                self._PHASE_DOCS["phase116_maturity_dashboard_unification"],
                "apps/local-api/app/api/routes_system.py",
                "apps/local-api/app/schemas/system.py",
                "apps/local-api/app/services/chat_mainline_readiness.py",
                "apps/local-api/app/services/release.py",
                "scripts/check.ps1",
                self._PHASE_TESTS["phase116_maturity_dashboard_unification"],
            ],
            blockers=blockers,
            next_owner="apps/local-api/app/services/chat_mainline_readiness.py",
            details=details,
        )

    async def _phase116_summary(
        self,
        *,
        phase109: dict[str, Any],
        phase110: dict[str, Any],
        phase111: dict[str, Any],
        phase112: dict[str, Any],
        phase113: dict[str, Any],
        phase114: dict[str, Any],
        phase115: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        phase109_details = dict(phase109.get("details") or {})
        phase110_details = dict(phase110.get("details") or {})
        phase111_details = dict(phase111.get("details") or {})
        phase112_details = dict(phase112.get("details") or {})
        phase113_details = dict(phase113.get("details") or {})
        phase114_details = dict(phase114.get("details") or {})
        phase115_details = dict(phase115.get("details") or {})
        phase105 = self._phase105()
        phase105_details = dict(phase105.get("details") or {})
        release_text = self._read_text("apps/local-api/app/services/release.py")
        routes_text = self._read_text("apps/local-api/app/api/routes_system.py")
        phase103_summary = await self._release_gate_service._phase103_report_summary(None)

        dimensions = [
            self._phase116_dimension(
                key="stability",
                contract_version=phase109_details.get("phase109_contract_version"),
                phase_key="phase109_real_world_maturity_recheck",
                phase_item=phase109,
                blockers=[
                    self._phase116_blocker_entry(
                        code=code,
                        source_phase="phase109_real_world_maturity_recheck",
                        dimension="stability",
                        next_owner=phase109.get("next_owner_module"),
                        evidence_ref={
                            "phase": "phase109_real_world_maturity_recheck",
                            "maturity_grade": phase109_details.get("maturity_grade"),
                        },
                    )
                    for code in list(phase109.get("blocking_reasons") or [])
                ],
                evidence_refs=[
                    {
                        "phase": "phase109_real_world_maturity_recheck",
                        "bundles": list(phase109_details.get("evidence_bundles") or []),
                    }
                ],
            ),
            self._phase116_dimension(
                key="routing",
                contract_version=phase110_details.get("phase110_contract_version"),
                phase_key="phase110_channel_routing_stability",
                phase_item=phase110,
                blockers=[
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase110_channel_routing_stability",
                            dimension="routing",
                            next_owner=phase110.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase110_channel_routing_stability",
                                "routing_replay_fields": list(
                                    phase110_details.get("routing_replay_fields") or []
                                ),
                            },
                        )
                        for code in list(phase110.get("blocking_reasons") or [])
                    ],
                    *[
                        self._phase116_blocker_entry_from_phase114(
                            item,
                            dimension="routing",
                            next_owner=phase110.get("next_owner_module"),
                        )
                        for item in list(phase114_details.get("top_blockers") or [])
                        if str(dict(item).get("impacted_segment") or "") == "routing"
                    ],
                ],
                evidence_refs=[
                    {
                        "phase": "phase110_channel_routing_stability",
                        "routing_replay_fields": list(
                            phase110_details.get("routing_replay_fields") or []
                        ),
                        "route_identity_fields": list(
                            phase110_details.get("route_identity_fields") or []
                        ),
                    }
                ],
            ),
            self._phase116_dimension(
                key="delivery",
                contract_version=phase111_details.get("phase111_contract_version"),
                phase_key="phase111_task_delivery_evidence",
                phase_item=phase111,
                blockers=[
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase111_task_delivery_evidence",
                            dimension="delivery",
                            next_owner=phase111.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase111_task_delivery_evidence",
                                "completion_requires": list(
                                    phase111_details.get("completion_requires") or []
                                ),
                            },
                        )
                        for code in list(phase111.get("blocking_reasons") or [])
                    ],
                    *[
                        self._phase116_blocker_entry_from_phase114(
                            item,
                            dimension="delivery",
                            next_owner=phase111.get("next_owner_module"),
                        )
                        for item in list(phase114_details.get("top_blockers") or [])
                        if str(dict(item).get("impacted_segment") or "") == "delivery"
                    ],
                    *[
                        self._phase116_blocker_entry(
                            code=str(item.get("code") or item.get("metric") or "phase103_blocker"),
                            source_phase="phase103_task_closure_gate",
                            dimension="delivery",
                            next_owner=phase111.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase103_task_closure_gate",
                                "domain": item.get("domain"),
                            },
                        )
                        for item in list(phase103_summary.get("blocking_reasons") or [])
                    ],
                ],
                evidence_refs=[
                    {
                        "phase": "phase103_task_closure_gate",
                        "overall_metrics": dict(phase103_summary.get("overall_metrics") or {}),
                    }
                ],
            ),
            self._phase116_dimension(
                key="extension",
                contract_version=phase115_details.get("phase115_contract_version")
                or phase112_details.get("phase112_contract_version"),
                phase_key="phase115_golden_extension_packages",
                phase_item=phase115,
                blockers=[
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase112_extension_runtime_sync_closure",
                            dimension="extension",
                            next_owner=phase112.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase112_extension_runtime_sync_closure",
                                "runtime_snapshot_contract": phase112_details.get(
                                    "runtime_snapshot_contract"
                                ),
                            },
                        )
                        for code in list(phase112.get("blocking_reasons") or [])
                    ],
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase115_golden_extension_packages",
                            dimension="extension",
                            next_owner=phase115.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase115_golden_extension_packages",
                                "inventory_coverage": dict(
                                    phase115_details.get("inventory_coverage") or {}
                                ),
                            },
                        )
                        for code in list(phase115.get("blocking_reasons") or [])
                    ],
                ],
                evidence_refs=[
                    {
                        "phase": "phase115_golden_extension_packages",
                        "golden_package_inventory": list(
                            phase115_details.get("golden_package_inventory") or []
                        ),
                    }
                ],
            ),
            self._phase116_dimension(
                key="quality",
                contract_version=phase113_details.get("phase113_contract_version"),
                phase_key="phase113_check_matrix_execution_restored",
                phase_item=phase113,
                blockers=[
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase113_check_matrix_execution_restored",
                            dimension="quality",
                            next_owner=phase113.get("next_owner_module"),
                            evidence_ref={
                                "phase": "phase113_check_matrix_execution_restored",
                                "latest_smoke_status": phase113_details.get("latest_smoke_status"),
                            },
                        )
                        for code in list(phase113.get("blocking_reasons") or [])
                    ],
                    *[
                        self._phase116_blocker_entry(
                            code=code,
                            source_phase="phase105_gate_signal_plane_governance",
                            dimension="quality",
                            next_owner="scripts/check.ps1",
                            evidence_ref={
                                "phase": "phase105_gate_signal_plane_governance",
                            },
                        )
                        for code in list(phase105_details.get("latest_smoke_report_blockers") or [])
                    ],
                ],
                evidence_refs=[
                    {
                        "phase": "phase113_check_matrix_execution_restored",
                        "latest_smoke_status": phase113_details.get("latest_smoke_status"),
                    }
                ],
            ),
        ]
        all_blockers = [
            dict(blocker)
            for dimension in dimensions
            for blocker in list(dimension.get("blockers") or [])
        ]
        priority_queue = self._phase116_priority_queue(all_blockers)
        top_blockers = priority_queue[:8]
        p0_blockers = [item for item in priority_queue if str(item.get("severity") or "") == "P0"]
        upstream_contracts = {
            "phase109_real_world_maturity_recheck": str(
                phase109_details.get("phase109_contract_version") or ""
            ),
            "phase110_channel_routing_stability": str(
                phase110_details.get("phase110_contract_version") or ""
            ),
            "phase111_task_delivery_evidence": str(
                phase111_details.get("phase111_contract_version") or ""
            ),
            "phase112_extension_runtime_sync_closure": str(
                phase112_details.get("phase112_contract_version") or ""
            ),
            "phase113_check_matrix_execution_restored": str(
                phase113_details.get("phase113_contract_version") or ""
            ),
            "phase114_mainline_observability_closure": str(
                phase114_details.get("phase114_contract_version") or ""
            ),
            "phase115_golden_extension_packages": str(
                phase115_details.get("phase115_contract_version") or ""
            ),
        }
        blockers: list[str] = []
        if not self._relative_exists(self._PHASE_DOCS["phase116_maturity_dashboard_unification"]):
            blockers.append("phase116_doc_missing")
        if not self._relative_exists(self._PHASE_TESTS["phase116_maturity_dashboard_unification"]):
            blockers.append("phase116_test_missing")
        if "/maturity-dashboard" not in routes_text:
            blockers.append("phase116_system_api_missing")
        if "phase116_maturity_dashboard_unification" not in release_text:
            blockers.append("phase116_release_summary_missing")
        if len(dimensions) != 5:
            blockers.append("phase116_dimension_inventory_incomplete")
        if p0_blockers:
            blockers.extend(
                str(item.get("blocker_code") or "phase116_p0_blocker_present")
                for item in p0_blockers
            )
        return {
            "phase116_contract_version": "phase116.maturity_dashboard.v1",
            "ready_conditions": [
                "phase109 through phase115 signals are attached to the shared dashboard",
                "system maturity dashboard endpoint is registered",
                "release summary consumes the same phase116 contract",
                "all five maturity dimensions are present",
                "no P0 blockers remain",
            ],
            "status": "ready" if not blockers else "partial",
            "dimensions": dimensions,
            "priority_queue": priority_queue,
            "top_blockers": top_blockers,
            "release_readiness": {
                "status": "no_go"
                if p0_blockers
                else ("go_with_findings" if priority_queue else "ready"),
                "p0_blocker_count": len(p0_blockers),
                "blocking_contract_drifts": [
                    str(item.get("blocker_code") or "")
                    for item in p0_blockers
                    if "contract_drift" in str(item.get("blocker_code") or "")
                    or "signal_suite_drift" in str(item.get("blocker_code") or "")
                ],
            },
            "upstream_contracts": upstream_contracts,
            "evidence_refs": evidence_refs,
            "blocking_reasons": blockers,
        }

    def _phase116_dimension(
        self,
        *,
        key: str,
        contract_version: str | None,
        phase_key: str,
        phase_item: dict[str, Any],
        blockers: list[dict[str, Any]],
        evidence_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "key": key,
            "status": "ready" if not blockers and phase_item.get("status") == "ready" else "partial",
            "contract_version": contract_version,
            "blockers": blockers,
            "evidence_refs": evidence_refs,
            "next_owner": phase_item.get("next_owner_module"),
            "upstream_phase_keys": [phase_key],
        }

    def _phase116_blocker_entry(
        self,
        *,
        code: str,
        source_phase: str,
        dimension: str,
        next_owner: str | None,
        evidence_ref: dict[str, Any] | None = None,
        count: int = 1,
    ) -> dict[str, Any]:
        return {
            "blocker_code": str(code or "unknown_blocker"),
            "category": self._phase116_blocker_category(code, source_phase, dimension),
            "severity": self._phase116_blocker_severity(code, source_phase, dimension),
            "source_phase": source_phase,
            "dimension": dimension,
            "next_owner": next_owner,
            "count": int(count),
            "evidence_ref": evidence_ref or {"phase": source_phase},
            "recommended_next_step": self._phase116_recommended_step(code, dimension),
        }

    def _phase116_blocker_entry_from_phase114(
        self,
        item: dict[str, Any],
        *,
        dimension: str,
        next_owner: str | None,
    ) -> dict[str, Any]:
        blocker_code = str(item.get("blocker_code") or "phase114_blocker")
        return {
            "blocker_code": blocker_code,
            "category": self._phase116_blocker_category(
                blocker_code,
                str(item.get("source") or "phase114_mainline_observability_closure"),
                dimension,
            ),
            "severity": str(item.get("severity") or "P1").upper(),
            "source_phase": str(item.get("source") or "phase114_mainline_observability_closure"),
            "dimension": dimension,
            "next_owner": next_owner,
            "count": int(item.get("count") or 0),
            "evidence_ref": dict(item.get("evidence_ref") or {"phase": "phase114"}),
            "recommended_next_step": str(item.get("recommended_next_step") or ""),
        }

    def _phase116_priority_queue(self, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        severity_rank = {"P0": 0, "P1": 1, "P2": 2}
        return sorted(
            blockers,
            key=lambda item: (
                severity_rank.get(str(item.get("severity") or "P2"), 3),
                str(item.get("dimension") or ""),
                -int(item.get("count") or 0),
                str(item.get("blocker_code") or ""),
            ),
        )

    def _phase116_blocker_category(self, code: str, source_phase: str, dimension: str) -> str:
        key = str(code or "")
        if "contract_drift" in key or "signal_suite_drift" in key:
            return "governance_gap"
        if dimension == "extension":
            return "ecosystem_gap"
        if "doc_missing" in key or "test_missing" in key or "release_summary" in key:
            return "governance_gap"
        if source_phase.startswith("phase109") or "evidence" in key:
            return "evidence_gap"
        return "runtime_fix"

    def _phase116_blocker_severity(self, code: str, source_phase: str, dimension: str) -> str:
        key = str(code or "")
        if (
            dimension == "routing"
            or "routing" in key
            or key in {
                "phase105_latest_smoke_report_missing",
                "phase105_latest_smoke_contract_drift",
                "phase105_latest_smoke_signal_suite_drift",
                "phase113_latest_smoke_report_missing",
                "extension_runtime_sync_missing",
                "phase115_fixture_import_failed",
                "phase115_fixture_structure_incomplete",
                "phase115_extension_ecosystem_deliverable_rate_regressed",
            }
            or source_phase == "phase103_task_closure_gate"
        ):
            return "P0"
        if "doc_missing" in key or "test_missing" in key:
            return "P2"
        return "P1"

    def _phase116_recommended_step(self, code: str, dimension: str) -> str:
        key = str(code or "")
        if dimension == "routing" or "routing" in key:
            return "replay the routing path and reconcile channel diagnostics with route identity fields"
        if dimension == "quality":
            return "re-run .\\scripts\\check.ps1 -Profile smoke and reconcile the latest smoke report contract"
        if dimension == "extension":
            return "reconcile runtime snapshot, golden package lifecycle, and extension delivery proof coverage"
        if dimension == "delivery":
            return "verify deliverable proof and phase103 closure blockers for the affected domain"
        return "inspect long-run evidence bundles and close the dominant stability blocker first"

    def _phase115_fixture_inventory(self, bundle_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        fixture_path = str(spec.get("fixture_path") or "")
        root = self._root_dir / fixture_path
        manifest_path = root / "bundle.yaml"
        skill_path = root / "SKILL.md"
        missing_artifacts = [
            name
            for name, exists in {
                "bundle.yaml": manifest_path.exists(),
                "SKILL.md": skill_path.exists(),
            }.items()
            if not exists
        ]
        importable = False
        compatibility_status = "missing"
        task_delivery_template: dict[str, Any] = {}
        compatibility_notes: list[str] = []
        if not missing_artifacts:
            try:
                imported = import_extension_from_root(
                    root,
                    source_type="local_directory",
                    source_uri=str(root),
                )
                importable = True
                compatibility_status = str(imported.package.compatibility_status or "compatible")
                compatibility_notes = list(imported.package.compatibility_notes or [])
                manifest = dict(imported.package.manifest or {})
                steps = list(manifest.get("steps") or [])
                first_step = dict(steps[0] or {}) if steps else {}
                task_delivery_template = {
                    "artifact_path": str(dict(first_step.get("args") or {}).get("path") or "") or None,
                    "tool_name": str(first_step.get("tool_name") or "") or None,
                }
            except Exception as exc:  # pragma: no cover - defensive readiness capture
                compatibility_status = "blocked"
                compatibility_notes = [f"import_failed:{type(exc).__name__}"]
        return {
            "bundle_id": bundle_id,
            "domain": spec.get("domain"),
            "fixture_path": fixture_path,
            "owner": spec.get("owner"),
            "intent": spec.get("intent"),
            "importable": importable,
            "compatibility_status": compatibility_status,
            "compatibility_notes": compatibility_notes,
            "missing_artifacts": missing_artifacts,
            "task_delivery_template": task_delivery_template,
            "diagnostics_template": {
                "required_fields": [
                    "missing_bindings",
                    "runtime_sync_missing",
                    "external_runtime_required",
                ]
            },
            "runtime_snapshot_contract": "phase112.extension_runtime_snapshot.v1",
        }

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
        path = self._root_dir / relative_path
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8-sig")
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _phase114_mainline_rates(
        self,
        *,
        repo: Any,
        table_names: set[str],
        gateway_snapshots: dict[str, dict[str, Any]],
        phase103_summary: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        turn_created = await self._phase114_count_rows(
            repo,
            table_names,
            "chat_turn_ledgers",
        )
        turn_queued = await self._phase114_count_rows(
            repo,
            table_names,
            "chat_run_ledgers",
            "WHERE stage = ? AND event_type = ?",
            ("turn_accept", "turn.accepted"),
        )
        turn_completed = await self._phase114_count_rows(
            repo,
            table_names,
            "chat_turn_ledgers",
            "WHERE status = ?",
            ("completed",),
        )
        no_turn = sum(
            int(dict(snapshot.get("taxonomy_counts") or {}).get("no_turn") or 0)
            for snapshot in gateway_snapshots.values()
        )
        approvals_created = await self._phase114_count_rows(repo, table_names, "approvals")
        approvals_resolved = await self._phase114_count_rows(
            repo,
            table_names,
            "approvals",
            "WHERE resolved_at IS NOT NULL",
        )
        phase103_overall = dict(phase103_summary.get("overall_metrics") or {})
        deliverable_sample = int(phase103_overall.get("total_tasks") or 0)
        ingress_attempted = turn_created + no_turn
        return {
            "turn_created_rate": self._phase114_rate(turn_created, ingress_attempted),
            "turn_queued_rate": self._phase114_rate(turn_queued, turn_created),
            "turn_completed_rate": self._phase114_rate(turn_completed, turn_queued),
            "approval_resolution_rate": self._phase114_rate(
                approvals_resolved,
                approvals_created,
            ),
            "final_deliverable_rate": self._phase114_rate_value(
                float(phase103_overall.get("final_deliverable_rate") or 0.0),
                deliverable_sample,
            ),
        }

    async def _phase114_segmented_views(
        self,
        *,
        repo: Any,
        table_names: set[str],
        gateway_snapshots: dict[str, dict[str, Any]],
        phase103_summary: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        by_channel: list[dict[str, Any]] = []
        for channel in ["local", "wechat", "feishu"]:
            turn_created = await self._phase114_count_rows(
                repo,
                table_names,
                "chat_turn_ledgers",
                "WHERE COALESCE(channel, ?) = ?",
                ("local", channel),
            )
            turn_queued = await self._phase114_count_rows(
                repo,
                table_names,
                "chat_run_ledgers",
                "WHERE stage = ? AND event_type = ? AND turn_id IN (SELECT turn_id FROM chat_turn_ledgers WHERE COALESCE(channel, ?) = ?)",
                ("turn_accept", "turn.accepted", "local", channel),
            )
            turn_completed = await self._phase114_count_rows(
                repo,
                table_names,
                "chat_turn_ledgers",
                "WHERE COALESCE(channel, ?) = ? AND status = ?",
                ("local", channel, "completed"),
            )
            no_turn = int(
                dict(gateway_snapshots.get(channel, {}).get("taxonomy_counts") or {}).get("no_turn")
                or 0
            )
            ingress_attempted = turn_created + no_turn
            by_channel.append(
                {
                    "key": channel,
                    "label": channel,
                    "sample_size": ingress_attempted,
                    "mainline_rates": {
                        "turn_created_rate": self._phase114_rate(turn_created, ingress_attempted),
                        "turn_queued_rate": self._phase114_rate(turn_queued, turn_created),
                        "turn_completed_rate": self._phase114_rate(turn_completed, turn_queued),
                        "approval_resolution_rate": self._phase114_rate_value(None, 0),
                        "final_deliverable_rate": self._phase114_rate_value(None, 0),
                    },
                    "details": {
                        "no_turn_count": no_turn,
                    },
                }
            )
        by_domain = []
        for domain, raw in dict(phase103_summary.get("per_domain_scorecard") or {}).items():
            item = dict(raw or {})
            sample_size = int(item.get("total_tasks") or 0)
            by_domain.append(
                {
                    "key": domain,
                    "label": domain,
                    "sample_size": sample_size,
                    "mainline_rates": {
                        "turn_created_rate": self._phase114_rate_value(None, 0),
                        "turn_queued_rate": self._phase114_rate_value(None, 0),
                        "turn_completed_rate": self._phase114_rate_value(None, 0),
                        "approval_resolution_rate": self._phase114_rate_value(
                            float(item.get("approval_interruption_rate") or 0.0)
                            if sample_size > 0
                            else None,
                            sample_size,
                        ),
                        "final_deliverable_rate": self._phase114_rate_value(
                            float(item.get("final_deliverable_rate") or 0.0)
                            if sample_size > 0
                            else None,
                            sample_size,
                        ),
                    },
                    "details": {
                        "blocker_codes": list(item.get("blocker_codes") or []),
                        "delivery_status_counts": dict(item.get("delivery_status_counts") or {}),
                    },
                }
            )
        by_runtime_path = [
            {
                "key": "channel_ingress",
                "label": "channel_ingress",
                "sample_size": sum(item["sample_size"] for item in by_channel if item["key"] != "local"),
                "mainline_rates": {
                    "turn_created_rate": self._phase114_rate_value(None, 0),
                    "turn_queued_rate": self._phase114_rate_value(None, 0),
                    "turn_completed_rate": self._phase114_rate_value(None, 0),
                    "approval_resolution_rate": self._phase114_rate_value(None, 0),
                    "final_deliverable_rate": self._phase114_rate_value(None, 0),
                },
                "details": {
                    "channels": ["wechat", "feishu"],
                },
            },
            {
                "key": "task_closure",
                "label": "task_closure",
                "sample_size": int(dict(phase103_summary.get("overall_metrics") or {}).get("total_tasks") or 0),
                "mainline_rates": {
                    "turn_created_rate": self._phase114_rate_value(None, 0),
                    "turn_queued_rate": self._phase114_rate_value(None, 0),
                    "turn_completed_rate": self._phase114_rate_value(None, 0),
                    "approval_resolution_rate": self._phase114_rate_value(None, 0),
                    "final_deliverable_rate": self._phase114_rate_value(
                        float(
                            dict(phase103_summary.get("overall_metrics") or {}).get(
                                "final_deliverable_rate"
                            )
                            or 0.0
                        )
                        if int(
                            dict(phase103_summary.get("overall_metrics") or {}).get("total_tasks")
                            or 0
                        )
                        > 0
                        else None,
                        int(dict(phase103_summary.get("overall_metrics") or {}).get("total_tasks") or 0),
                    ),
                },
                "details": {
                    "source": "phase103_task_closure_gate",
                },
            },
        ]
        return {
            "by_channel": by_channel,
            "by_domain": by_domain,
            "by_runtime_path": by_runtime_path,
        }

    def _phase114_top_blockers(
        self,
        *,
        phase109_details: dict[str, Any],
        phase110_details: dict[str, Any],
        phase103_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        likely_primary_causes = list(
            dict(phase109_details.get("no_turn_diagnostics") or {}).get("likely_primary_causes") or []
        )
        for item in likely_primary_causes:
            cause_code = str(item.get("cause_code") or "")
            classification = str(item.get("classification") or "runtime_gap")
            blockers.append(
                {
                    "blocker_code": cause_code,
                    "source": "phase109_real_world_maturity_recheck",
                    "impacted_segment": self._phase114_segment_for_blocker(cause_code, classification),
                    "count": int(item.get("count") or 0),
                    "severity": str(item.get("priority") or "p1"),
                    "replay_ref": {
                        "phase": "phase109_real_world_maturity_recheck",
                        "kind": "evidence_bundle",
                    },
                    "evidence_ref": {
                        "phase": "phase109_real_world_maturity_recheck",
                        "classification": classification,
                    },
                    "recommended_next_step": str(item.get("recommended_next_step") or ""),
                }
            )
        for group, count in dict(phase110_details.get("runtime_no_turn_reason_group_counts") or {}).items():
            if int(count or 0) <= 0:
                continue
            blockers.append(
                {
                    "blocker_code": f"{group}_runtime_no_turn",
                    "source": "phase110_channel_routing_stability",
                    "impacted_segment": self._phase114_segment_for_blocker(group, "runtime_gap"),
                    "count": int(count or 0),
                    "severity": "p0" if group == "routing" else "p1",
                    "replay_ref": {
                        "phase": "phase110_channel_routing_stability",
                        "group": group,
                    },
                    "evidence_ref": {
                        "phase": "phase110_channel_routing_stability",
                        "group": group,
                    },
                    "recommended_next_step": self._phase114_recommended_step(
                        group, "runtime_gap"
                    ),
                }
            )
        for item in list(phase103_summary.get("blocking_reasons") or []):
            blocker_code = str(item.get("code") or item.get("metric") or "phase103_blocker")
            blockers.append(
                {
                    "blocker_code": blocker_code,
                    "source": "phase103_task_closure_gate",
                    "impacted_segment": self._phase114_segment_for_blocker(
                        blocker_code, "delivery_gap"
                    ),
                    "count": int(item.get("count") or 1),
                    "severity": "p1",
                    "replay_ref": {
                        "phase": "phase103_task_closure_gate",
                        "domain": item.get("domain"),
                    },
                    "evidence_ref": {
                        "phase": "phase103_task_closure_gate",
                        "domain": item.get("domain"),
                    },
                    "recommended_next_step": self._phase114_recommended_step(
                        blocker_code, "delivery_gap"
                    ),
                }
            )
        blockers.sort(
            key=lambda item: (
                0 if item.get("severity") == "p0" else 1,
                -int(item.get("count") or 0),
                str(item.get("blocker_code") or ""),
            )
        )
        return blockers[:8]

    async def _phase114_count_rows(
        self,
        repo: Any,
        table_names: set[str],
        table: str,
        where: str | None = None,
        params: tuple[Any, ...] = (),
    ) -> int:
        if table not in table_names:
            return 0
        return int(await repo.count_rows(table, where, params))

    def _phase114_rate(self, numerator: int, denominator: int) -> dict[str, Any]:
        rate = None if denominator <= 0 else round(numerator / denominator, 4)
        return {
            "rate": rate,
            "numerator": int(numerator),
            "denominator": int(denominator),
            "sample_size": int(denominator),
        }

    def _phase114_rate_value(self, rate: float | None, sample_size: int) -> dict[str, Any]:
        denominator = int(sample_size)
        numerator = 0 if rate is None else int(round(rate * denominator))
        return {
            "rate": None if denominator <= 0 or rate is None else round(float(rate), 4),
            "numerator": numerator,
            "denominator": denominator,
            "sample_size": denominator,
        }

    def _phase114_segment_for_blocker(self, code: str, classification: str) -> str:
        key = str(code or "")
        if key in {"routing", "routing_path_not_stable"} or key.startswith("routing_"):
            return "routing"
        if key in {"worker", "worker_not_running_or_disabled"} or key.startswith("worker_"):
            return "worker"
        if "approval" in key or key == "pending_approval":
            return "approval"
        if "deliver" in key or "publish" in key or "verification" in key:
            return "delivery"
        if classification == "evidence_gap":
            return "ingress"
        return "ingress"

    def _phase114_recommended_step(self, code: str, classification: str) -> str:
        segment = self._phase114_segment_for_blocker(code, classification)
        if segment == "routing":
            return "replay routing path using channel diagnostics and route identity fields"
        if segment == "worker":
            return "verify worker health and resume handoff path from gateway snapshot"
        if segment == "approval":
            return "trace approval creation and resolution events to clear pending approval debt"
        if segment == "delivery":
            return "reconcile deliverable proof, verification, and visible delivery evidence"
        return "inspect ingress evidence bundle and trace alignment for the failing path"

    def _latest_root_check_report(self, *, profile: str | None = None) -> dict[str, Any]:
        report_dir = self._root_dir / "data" / "check-reports"
        if not report_dir.exists():
            return {}
        reports = sorted(report_dir.glob("check-*.json"), key=lambda path: path.stat().st_mtime)
        for path in reversed(reports):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if profile is not None and str(payload.get("profile") or "") != profile:
                continue
            return payload if isinstance(payload, dict) else {}
        return {}

    def _phase109_evidence_bundle(
        self,
        *,
        bundle_id: str,
        summary_path: str,
        gap_path: str,
    ) -> dict[str, Any]:
        summary = self._read_json(summary_path)
        gap_payload = self._read_json(gap_path)
        gaps = list(summary.get("gaps") or [])
        if isinstance(gap_payload.get("items"), list):
            gaps = list(gap_payload.get("items") or gaps)
        gap_items = [item for item in gaps if isinstance(item, dict)]
        category_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        no_turn_group_counts: dict[str, int] = {}
        no_turn_count = 0
        p0_gap_count = 0
        for item in gap_items:
            category = str(item.get("category") or "unknown")
            severity = str(item.get("severity") or "unknown")
            category_counts[category] = category_counts.get(category, 0) + 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            if category == "no_turn":
                no_turn_count += 1
                group = str(item.get("group") or "unknown")
                no_turn_group_counts[group] = no_turn_group_counts.get(group, 0) + 1
            if severity == "P0":
                p0_gap_count += 1
        quality = dict(summary.get("quality") or {})
        return {
            "bundle_id": bundle_id,
            "summary_path": summary_path,
            "gap_path": gap_path,
            "summary_present": bool(summary),
            "gap_report_present": bool(gap_payload),
            "case_count": int(summary.get("case_count") or 0),
            "natural_reply_count": int(quality.get("natural_reply_count") or 0),
            "gap_count": len(gap_items),
            "p0_gap_count": p0_gap_count,
            "no_turn_count": no_turn_count,
            "no_turn_group_counts": no_turn_group_counts,
            "category_counts": category_counts,
            "severity_counts": severity_counts,
        }

    def _merge_phase109_counts(self, count_sets: list[dict[str, Any]]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for counts in count_sets:
            for key, value in counts.items():
                merged[str(key)] = int(merged.get(str(key)) or 0) + int(value or 0)
        return merged

    def _phase109_runtime_no_turn_reason_counts(
        self,
        gateway_snapshots: dict[str, Any],
    ) -> dict[str, int]:
        relevant_reasons = {
            "pairing_rejected_or_missing",
            "ingress_policy_blocked",
            "worker_not_running_or_disabled",
            "conversation_bootstrap_failed",
            "channel_ingress_submit_failed",
            "turn_not_created",
            "turn_created_but_not_queued",
            "turn_created_but_runtime_missing",
        }
        counts: dict[str, int] = {}
        for snapshot in gateway_snapshots.values():
            failure_reason_counts = dict(snapshot.get("failure_reason_counts") or {})
            for reason_code in relevant_reasons:
                counts[reason_code] = int(counts.get(reason_code) or 0) + int(
                    failure_reason_counts.get(reason_code) or 0
                )
        return counts

    def _phase109_top_counts(
        self,
        counts: dict[str, int],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            (
                {"name": str(name), "count": int(count or 0)}
                for name, count in counts.items()
                if int(count or 0) > 0
            ),
            key=lambda item: (-int(item["count"]), str(item["name"])),
        )
        return ranked[:limit]

    def _phase109_likely_primary_causes(
        self,
        *,
        top_evidence_no_turn_groups: list[dict[str, Any]],
        top_runtime_no_turn_reasons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        causes: list[dict[str, Any]] = []
        group_to_cause = {
            "routing": {
                "cause_code": "routing_path_not_stable",
                "classification": "evidence_gap",
                "priority": "p0",
                "recommended_next_step": "stabilize channel routing path and re-run long-run smoke bundles",
            },
        }
        reason_to_cause = {
            "worker_not_running_or_disabled": {
                "cause_code": "worker_automation_not_stable",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "restore background worker automation health before next long-run validation",
            },
            "conversation_bootstrap_failed": {
                "cause_code": "conversation_bootstrap_not_stable",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "repair conversation bootstrap path and confirm session creation in smoke traffic",
            },
            "turn_created_but_runtime_missing": {
                "cause_code": "channel_ingress_runtime_binding_missing",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "rebind channel ingress runtime before accepting live channel traffic",
            },
            "turn_created_but_not_queued": {
                "cause_code": "turn_queue_handoff_not_stable",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "repair handoff from turn creation into queued execution",
            },
            "turn_not_created": {
                "cause_code": "turn_creation_not_stable",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "trace channel ingestion path until chat turn creation succeeds consistently",
            },
            "channel_ingress_submit_failed": {
                "cause_code": "channel_ingress_submit_not_stable",
                "classification": "runtime_fix",
                "priority": "p0",
                "recommended_next_step": "debug submit_channel_turn failures and confirm ingress runtime contract health",
            },
            "pairing_rejected_or_missing": {
                "cause_code": "pairing_state_not_ready",
                "classification": "governance_gap",
                "priority": "p1",
                "recommended_next_step": "close pairing-state gaps or narrow smoke scope to paired traffic only",
            },
            "ingress_policy_blocked": {
                "cause_code": "ingress_policy_not_ready",
                "classification": "governance_gap",
                "priority": "p1",
                "recommended_next_step": "reconcile ingress policy with expected long-run traffic profile",
            },
        }
        for item in top_evidence_no_turn_groups:
            name = str(item.get("name") or "")
            mapped = dict(group_to_cause.get(name) or {})
            if mapped and not any(entry["cause_code"] == mapped["cause_code"] for entry in causes):
                mapped["source"] = "evidence_group"
                mapped["source_name"] = name
                mapped["count"] = int(item.get("count") or 0)
                causes.append(mapped)
        for item in top_runtime_no_turn_reasons:
            name = str(item.get("name") or "")
            mapped = dict(reason_to_cause.get(name) or {})
            if mapped and not any(entry["cause_code"] == mapped["cause_code"] for entry in causes):
                mapped["source"] = "runtime_reason"
                mapped["source_name"] = name
                mapped["count"] = int(item.get("count") or 0)
                causes.append(mapped)
        causes.sort(
            key=lambda item: (
                0 if str(item.get("priority")) == "p0" else 1,
                0 if str(item.get("classification")) == "runtime_fix" else 1,
                -int(item.get("count") or 0),
                str(item.get("cause_code") or ""),
            )
        )
        return causes

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
