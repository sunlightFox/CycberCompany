from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import (
    BrainDecisionBundle,
    ContextDecision,
    DialogueState,
    IntentDecision,
    ModeDecision,
    PrivacyLevel,
    SemanticIntentCandidate,
    TaskMode,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.design_alignment_repo import DesignAlignmentRepository
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.services.chat_intent_router import (
    is_explicit_download_request,
    is_file_mutation_request,
    is_host_filesystem_list_request,
    is_host_software_install_request,
    is_office_document_request,
    is_webpage_read_request,
)
from app.services.brain_clarification_decider import clarification_decision as _clarification_decision
from app.services.brain_context_decider import context_decision as _context_decision
from app.services.brain_decision_support import summary as _summary
from app.services.brain_mode_decider import mode_decision as _mode_decision
from app.services.brain_route_decider import intent_decision as _intent_decision
from app.services.failure_experience import FailureExperienceService
from app.services.dialogue_semantics import (
    DialogueStateService,
    LowConfidenceDecisionReviewer,
    SemanticIntentAnalyzer,
)


@dataclass(frozen=True)
class BrainDecisionPreviewRequest:
    text: str
    member_id: str = "mem_xiaoyao"
    conversation_id: str | None = None
    privacy_level: str = "medium"


class BrainDecisionService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        design_repo: DesignAlignmentRepository,
        skill_mcp_repo: SkillMcpRepository | None = None,
        trace_service: TraceService,
        failure_experience_service: FailureExperienceService | None = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._design_repo = design_repo
        self._skill_mcp_repo = skill_mcp_repo
        self._trace = trace_service
        self._failure_experience = failure_experience_service
        self._dialogue_states = DialogueStateService(repo=chat_repo, trace_service=trace_service)
        self._semantic_analyzer = SemanticIntentAnalyzer(trace_service=trace_service)
        self._low_confidence = LowConfidenceDecisionReviewer(trace_service=trace_service)

    async def decide(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None = None,
        privacy_level: str = "medium",
        trace_id: str | None = None,
        root_span_id: str | None = None,
        persist: bool = True,
    ) -> BrainDecisionBundle:
        decision_id = new_id("bd")
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_DECISION_CHAIN,
                name="brain decision chain",
                parent_span_id=root_span_id,
                input_data={"text": redact(text), "member_id": member_id},
            )
            if trace_id
            else None
        )
        capability_snapshot = await self._capability_snapshot(text=text)
        failure_advisories = (
            await self._failure_experience.recall_advisories(
                member_id=member_id,
                query=text,
                limit=2,
            )
            if self._failure_experience is not None and text.strip()
            else []
        )
        if failure_advisories:
            capability_snapshot = {
                **capability_snapshot,
                "failure_advisories": [
                    {
                        "failure_id": item.failure_id,
                        "failure_class": item.failure_class,
                        "reason_code": item.reason_code,
                        "summary_text": item.summary_text,
                    }
                    for item in failure_advisories
                ],
            }
        working_state = (
            await self._chat_repo.get_working_state(conversation_id)
            if conversation_id
            else None
        )
        dialogue_state = await self._dialogue_states.derive(
            text=text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            working_state=working_state,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
            persist=persist,
        )
        semantic = await self._semantic_analyzer.analyze(
            text=text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            dialogue_state=dialogue_state,
            capability_snapshot=capability_snapshot,
            privacy_level=privacy_level,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
        )
        semantic = semantic.model_copy(update={"brain_decision_id": decision_id})
        intent = _intent_decision(
            text,
            privacy_level,
            capability_snapshot,
            working_state=working_state,
            dialogue_state=dialogue_state,
            semantic=semantic,
        )
        mode = _mode_decision(intent, capability_snapshot)
        clarification = _clarification_decision(text, intent, mode, semantic)
        if clarification["needs_clarification"]:
            mode = mode.model_copy(
                update={
                    "mode": "ask_clarification",
                    "submode": clarification["blocking_level"],
                    "fallback_mode": "direct",
                    "reason_codes": [*mode.reason_codes, clarification["reason"]],
                }
            )
            intent = intent.model_copy(update={"needs_clarification": True})
        context = _context_decision(
            intent,
            mode,
            bool(conversation_id),
            working_state=working_state,
            dialogue_state=dialogue_state,
            semantic=semantic,
        )
        if failure_advisories:
            context = context.model_copy(
                update={
                    "selection_reason": [
                        *list(context.selection_reason),
                        "failure_advisory_present",
                    ]
                }
            )
        review_outcome = await self._low_confidence.review(
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            brain_decision_id=decision_id,
            intent=intent,
            mode=mode,
            context=context,
            semantic=semantic,
            dialogue_state=dialogue_state,
            clarification=clarification,
            capability_snapshot=capability_snapshot,
            privacy_level=privacy_level,
            text=text,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
        )
        review = None
        semantic_review = None
        if review_outcome is not None:
            intent = review_outcome.intent
            mode = review_outcome.mode
            context = review_outcome.context
            clarification = review_outcome.clarification
            review = review_outcome.review
            semantic_review = review_outcome.semantic_review
        if intent.direct_only_requested and intent.execution_policy == "no_task":
            intent = intent.model_copy(
                update={
                    "primary_intent": (
                        intent.primary_intent
                        if intent.primary_intent
                        not in {"task_request", "tool_request", "skill_request", "mcp_request"}
                        else "simple_question"
                    ),
                    "needs_tool": False,
                    "needs_task": False,
                    "needs_skill": False,
                    "needs_mcp": False,
                    "interaction_class": "direct_explanation",
                    "execution_policy": "no_task",
                    "reason_codes": [
                        *list(intent.reason_codes),
                        "direct_only_fail_closed",
                    ],
                }
            )
            if mode.mode not in {
                TaskMode.DIRECT.value,
                TaskMode.DIRECT_WITH_MEMORY.value,
                "ask_clarification",
            }:
                mode = mode.model_copy(
                    update={
                        "mode": TaskMode.DIRECT.value,
                        "submode": "simple_answer",
                        "planner_hint": None,
                        "requires_approval_before_execute": False,
                        "reason_codes": [
                            *list(mode.reason_codes),
                            "direct_only_fail_closed",
                        ],
                    }
                )
        confidence = round(min(intent.confidence, mode.confidence), 2)
        bundle = BrainDecisionBundle(
            brain_decision_id=decision_id,
            intent=intent,
            mode=mode,
            context=context,
            clarification=clarification,
            dialogue_state=dialogue_state.model_dump(mode="json") if dialogue_state else None,
            semantic_intent_candidates=[semantic.model_dump(mode="json")],
            low_confidence_review=review.model_dump(mode="json") if review else None,
            semantic_review=semantic_review.model_dump(mode="json") if semantic_review else None,
            capability_snapshot=capability_snapshot,
            confidence=confidence,
            status="completed" if confidence >= 0.45 else "low_confidence",
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        if persist:
            await self._chat_repo.insert_brain_decision(
                {
                    **bundle.model_dump(mode="json"),
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "member_id": member_id,
                    "input_summary": _summary(text),
                }
            )
            await self._chat_repo.insert_semantic_intent_candidate(
                semantic.model_copy(
                    update={"brain_decision_id": decision_id}
                ).model_dump(mode="json")
            )
            if review is not None:
                await self._chat_repo.insert_low_confidence_review(
                    review.model_dump(mode="json")
                )
            if semantic_review is not None:
                await self._persist_semantic_review(
                    semantic_review.model_dump(mode="json")
                )
            if turn_id:
                await self._chat_repo.update_turn(
                    turn_id,
                    brain_decision_id=decision_id,
                    updated_at=utc_now_iso(),
                )
        if trace_id:
            context_span = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_CONTEXT_DECISION,
                name="brain context decision",
                parent_span_id=span_id or root_span_id,
                metadata={"brain_decision_id": decision_id},
            )
            await self._trace.end_span(
                context_span,
                output_data=redact(context.model_dump(mode="json")),
            )
            clarification_span = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_CLARIFICATION_DECISION,
                name="brain clarification decision",
                parent_span_id=span_id or root_span_id,
                metadata={"brain_decision_id": decision_id},
            )
            await self._trace.end_span(
                clarification_span,
                output_data=redact(clarification),
            )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "brain_decision_id": decision_id,
                    "primary_intent": intent.primary_intent,
                    "mode": mode.mode,
                    "confidence": confidence,
                    "reason_codes": intent.reason_codes + mode.reason_codes,
                },
            )
        return bundle

    async def preview(
        self,
        request: BrainDecisionPreviewRequest,
        *,
        trace_id: str | None = None,
    ) -> BrainDecisionBundle:
        return await self.decide(
            text=request.text,
            member_id=request.member_id,
            conversation_id=request.conversation_id,
            privacy_level=request.privacy_level,
            trace_id=trace_id,
            persist=False,
        )

    async def get_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._chat_repo.get_brain_decision_by_turn(turn_id)
        return await self._attach_phase18_evidence(row)

    async def get(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._chat_repo.get_brain_decision(decision_id)
        return await self._attach_phase18_evidence(row)

    async def get_dialogue_state(self, conversation_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_dialogue_state(conversation_id)

    async def list_semantic_intents(self, turn_id: str) -> list[dict[str, Any]]:
        return await self._chat_repo.list_semantic_intents_by_turn(turn_id)

    async def get_low_confidence_review(self, turn_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_low_confidence_review_by_turn(turn_id)

    async def get_semantic_review(self, turn_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_semantic_review_by_turn(turn_id)

    async def list_semantic_review_events(self, turn_id: str) -> list[dict[str, Any]]:
        return await self._chat_repo.list_semantic_review_events_by_turn(turn_id)

    async def _attach_phase18_evidence(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        decision_id = str(row["brain_decision_id"])
        row["semantic_intent_candidates"] = (
            await self._chat_repo.list_semantic_intents_by_decision(decision_id)
        )
        row["low_confidence_review"] = (
            await self._chat_repo.get_low_confidence_review_by_decision(decision_id)
        )
        row["semantic_review"] = await self._chat_repo.get_semantic_review_by_decision(
            decision_id
        )
        conversation_id = row.get("conversation_id")
        row["dialogue_state"] = (
            await self._chat_repo.get_dialogue_state(str(conversation_id))
            if conversation_id
            else None
        )
        return row

    async def _persist_semantic_review(self, semantic_review: dict[str, Any]) -> None:
        request = dict(semantic_review["request"])
        request["status"] = "completed"
        await self._chat_repo.insert_semantic_review_request(request)
        suggestion = semantic_review.get("suggestion")
        if suggestion is not None:
            await self._chat_repo.insert_semantic_review_suggestion(
                {
                    "suggestion_id": new_id("semsug"),
                    "semantic_review_id": semantic_review["semantic_review_id"],
                    "source": "model"
                    if semantic_review.get("model_assist_attempted")
                    and not semantic_review.get("fallback_used")
                    else "rule_fallback",
                    "suggestion": suggestion,
                    "confidence": suggestion.get("confidence", 0.0),
                    "schema_valid": bool(semantic_review.get("schema_valid")),
                    "rejected_reasons": [],
                    "created_at": utc_now_iso(),
                }
            )
        model_call = dict(semantic_review.get("model_call") or {})
        if model_call:
            await self._chat_repo.insert_semantic_review_model_call(model_call)
        merge = semantic_review.get("merge_result")
        if merge is not None:
            await self._chat_repo.insert_semantic_review_merge_result(merge)

    async def _capability_snapshot(self, *, text: str = "") -> dict[str, Any]:
        rows = await self._design_repo.list_runtime_contracts()
        contracts = {
            str(row["name"]): {
                "status": row["status"],
                "implemented": row["implemented"],
                "blocker_level": row["blocker_level"],
            }
            for row in rows
        }
        enabled_skill_count = 0
        ready_mcp_server_count = 0
        active_mcp_tool_count = 0
        live_skill_mcp_snapshot = _needs_live_skill_mcp_snapshot(text)
        if self._skill_mcp_repo is not None and live_skill_mcp_snapshot:
            enabled_skill_count = len(await self._skill_mcp_repo.list_skills(status="enabled"))
            for server in await self._skill_mcp_repo.list_mcp_servers():
                if server.get("status") != "ready":
                    continue
                ready_mcp_server_count += 1
                active_mcp_tool_count += sum(
                    1
                    for tool in await self._skill_mcp_repo.list_mcp_tools(
                        str(server["server_id"])
                    )
                    if tool.get("status") == "active"
                )
        skill_status = contracts.get("SkillEngine", {}).get("status", "not_started")
        mcp_status = contracts.get("MCPConnectionManager", {}).get("status", "not_started")
        return {
            "runtime_contracts": contracts,
            "tool_runtime": contracts.get("ToolRuntime", {}).get("status", "not_started"),
            "skill_engine": skill_status,
            "mcp": mcp_status,
            "skill": {
                "status": skill_status,
                "enabled_count": enabled_skill_count,
                "available": _status_available(skill_status) and enabled_skill_count > 0,
            },
            "mcp_runtime": {
                "status": mcp_status,
                "ready_server_count": ready_mcp_server_count,
                "active_tool_count": active_mcp_tool_count,
                "available": (
                    _status_available(mcp_status)
                    and ready_mcp_server_count > 0
                    and active_mcp_tool_count > 0
                ),
            },
            "model_assist": {
                "enabled": True,
                "real_model_call": False,
                "status": "implemented_with_fallback",
                "reason": "phase24_model_semantic_verifier_fallback_contract",
            },
            "snapshot_scope": "live_skill_mcp" if live_skill_mcp_snapshot else "base_runtime",
        }



def _clarify(
    reason: str,
    questions: list[str],
    *,
    clarification_type: str,
) -> dict[str, Any]:
    return {
        "needs_clarification": True,
        "needed": True,
        "reason": reason,
        "clarification_type": clarification_type,
        "blocking_level": "blocks_execution",
        "questions": questions[:3],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": reason != "high_risk_without_confirmation",
    }


def _no_clarification() -> dict[str, Any]:
    return {
        "needs_clarification": False,
        "needed": False,
        "reason": "safe_to_continue",
        "clarification_type": "none",
        "blocking_level": "none",
        "questions": [],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": True,
    }


def _summary(text: str) -> str:
    clean = str(redact(text)).strip().replace("\n", " ")
    return clean if len(clean) <= 160 else f"{clean[:160]}..."


def _complexity(text: str) -> float:
    score = min(len(text.strip()) / 260, 0.45)
    score += 0.08 * sum(
        1
        for marker in ["方案", "架构", "对比", "权衡", "排查", "继续", "长期", "多步骤"]
        if marker in text
    )
    return round(min(score, 1.0), 2)


def _confidence(primary: str, rule_hits: list[str], risks: list[str], text: str) -> float:
    if not text.strip():
        return 0.2
    if primary == "unknown":
        return 0.28
    score = 0.58 + min(len(rule_hits) * 0.08, 0.24)
    if risks:
        score += 0.05
    if primary == "casual_chat" and len(text.strip()) > 80:
        score -= 0.18
    return round(max(0.25, min(score, 0.95)), 2)


def _safe_plan_only(text: str) -> bool:
    if _explicit_task_creation(text):
        return False
    real_execution_markers = [
        "调研",
        "检查",
        "整理",
        "基于当前仓库",
        "基于这个仓库",
    ]
    real_deliverable_markers = [
        "任务报告",
        "生成报告",
        "输出报告",
        "验收证据",
        "测试日志",
        "执行报告",
        "回归报告",
    ]
    if any(marker in text for marker in real_execution_markers) and any(
        marker in text for marker in real_deliverable_markers
    ):
        return False
    return any(
        marker in text
        for marker in [
            "不要执行",
            "不执行",
            "别执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不能点击",
            "不能提交",
            "不点击",
            "不提交",
            "不要点击",
            "不要提交",
            "不要创建任务",
            "不要使用工具",
            "不要使用浏览器",
            "不要使用浏览器或工具",
            "不要调用工具",
            "不使用工具",
            "不用工具",
            "不要联网",
            "不浏览",
            "只分析",
            "只解释",
            "请解释",
            "解释",
            "只要方案",
            "只给方案",
            "只生成方案",
            "只输出",
            "先给方案",
            "先写方案",
            "生成草稿",
            "只写草稿",
            "总结",
            "严格 JSON",
            "只用 JSON",
            "术语表",
            "科普",
            "学习路线",
            "路线图",
            "翻译",
            "表格比较",
            "用表格",
            "设计原则",
            "五条原则",
            "知识总结",
            "知识",
            "概念",
            "区别",
            "压缩成",
            "压缩为",
            "归纳为",
            "原则",
            "验收原则",
            "应如何记录",
            "不要打开浏览器",
            "不要安装",
            "不要匹配",
            "不要运行",
        ]
    )


def _multimodal_attachment_context(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "图片内容线索：",
            "语音内容线索：",
            "语音转成文字：",
            "文件内容摘录：",
            "用户还附带了一个文件：",
            "用户还附带了一段语音：",
            "用户还附带了一张图片：",
        ]
    )


def _log_data_extraction(text: str) -> bool:
    return (
        "日志片段" in text
        or "最慢接口" in text
        or ("500" in text and "错误" in text and "几次" in text)
    )


def _unknown_input(text: str) -> bool:
    clean = text.strip()
    if not clean:
        return True
    without_punctuation = clean.strip(" ?？.!！。…~、，,;；:")
    return not without_punctuation


def _memory_query(text: str) -> bool:
    lowered = text.lower()
    explicit_markers = [
        "记得",
        "memory",
        "历史记忆",
        "长期记忆",
        "我说过",
        "之前说过",
        "上次说过",
        "偏好",
    ]
    return any(marker in lowered for marker in explicit_markers)


def _memory_write(text: str) -> bool:
    return any(marker in text for marker in ["记住：", "记住:", "请记住", "帮我记住"])


def _memory_correction(text: str) -> bool:
    lowered = text.lower()
    if "纠正记忆" in text or "记错" in text or "memory correction" in lowered:
        return True
    memory_markers = ["记忆", "记得", "我说过", "之前说过", "偏好"]
    correction_markers = ["不是", "改成", "换成", "以后不"]
    return any(marker in text for marker in memory_markers) and any(
        marker in text for marker in correction_markers
    )


def _system_settings(text: str) -> bool:
    if _office_document_request(text):
        return False
    action_markers = ["设置", "配置", "切换", "修改", "调整", "启用", "关闭", "禁用"]
    target_markers = [
        "模型设置",
        "模型配置",
        "切换模型",
        "修改模型",
        "大脑设置",
        "大脑配置",
        "记忆设置",
        "安全策略",
        "mcp",
        "插件",
        "技能包",
        "开发者设置",
        "日志与审计",
        "备份恢复",
        "壳与外观",
    ]
    return any(marker in text for marker in action_markers) and any(
        marker in text.lower() for marker in target_markers
    )


def _approval_response(text: str) -> bool:
    return text.strip() in {"同意", "批准", "拒绝", "deny", "approve"} or any(
        marker in text for marker in ["我批准", "我拒绝", "确认执行"]
    )


def _cancel_or_retry(text: str) -> bool:
    return any(marker in text for marker in ["取消", "停止", "重试", "再试一次"])


def _skill_request(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    lowered = text.lower()
    return "skill" in lowered or "技能" in text


def _office_document_request(text: str) -> bool:
    return is_office_document_request(text)


def _mcp_request(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    lowered = text.lower()
    return "mcp" in lowered or "外部服务" in text


def _needs_live_skill_mcp_snapshot(text: str) -> bool:
    clean = text.strip()
    if not clean:
        return False
    if is_office_document_request(clean):
        return True
    return _skill_request(clean) or _mcp_request(clean)


def _persona_boundary_question(text: str) -> bool:
    lowered = text.lower()
    identity_markers = [
        "你是真人",
        "你是人类",
        "你是不是人",
        "假装真人",
        "真实主观意识",
        "主观意识",
        "pretend human",
        "are you human",
        "real person",
    ]
    hidden_capability_markers = [
        "隐藏账号",
        "隐藏账户",
        "能直接帮我登录",
        "直接帮我登录",
        "直接替我登录",
        "替我登录",
        "绕过系统",
        "绕过安全",
        "绕过审批",
        "绕过权限",
        "越权审批",
        "忽略审批",
        "不要审批",
        "无视安全",
        "直接操作",
        "偷偷",
        "后台偷偷",
        "登录账号",
        "后台账号",
        "系统管理员",
        "系统提示",
        "内部提示",
        "internal prompt",
        "system prompt",
        "root权限",
        "系统密钥",
        "私钥",
        "private key",
    ]
    return any(marker in lowered or marker in text for marker in identity_markers) or any(
        marker in text for marker in hidden_capability_markers
    )


def _explicit_task_creation(text: str) -> bool:
    if "不要创建任务" in text or "不创建任务" in text:
        return False
    return any(
        marker in text
        for marker in [
            "请创建一个任务",
            "创建一个任务",
            "请创建任务",
            "创建任务",
            "新建任务",
        ]
    )


def _real_task_request(text: str) -> bool:
    if _safe_plan_only(text) or _persona_boundary_question(text) or _advice_strategy_direct(text):
        return False
    if _explicit_task_creation(text):
        return True
    action_markers = [
        "调研",
        "研究",
        "检查",
        "整理",
        "汇总",
        "分析这些",
        "基于当前仓库",
        "基于这个仓库",
        "读取这些",
        "处理这些",
    ]
    deliverable_markers = [
        "任务报告",
        "生成报告",
        "输出报告",
        "验收证据",
        "测试日志",
        "执行报告",
        "回归报告",
    ]
    if any(marker in text for marker in action_markers) and any(
        marker in text for marker in deliverable_markers
    ):
        return True
    return any(marker in text for marker in ["请调研", "帮我整理这些测试日志"])


def _tool_request(text: str) -> bool:
    if _safe_plan_only(text) or _persona_boundary_question(text) or _advice_strategy_direct(text):
        return False
    if is_host_filesystem_list_request(text):
        return False
    if is_webpage_read_request(text):
        return False
    if ("下载" in text or "download" in text.lower()) and not is_explicit_download_request(text):
        text = text.replace("下载", "").replace("download", "")
    return any(
        marker in text
        for marker in [
            "打开",
            "运行",
            "执行",
            "发送",
            "登录",
            "下载",
            "截图",
            "浏览器",
            "文件夹",
            "删除",
            "清空",
            "覆盖",
            "移动",
            "发帖",
            "发布",
            "购买",
            "下单",
            "转账",
            "支付",
            "签名",
        ]
    )


def _advice_strategy_direct(text: str) -> bool:
    if _explicit_task_creation(text):
        return False
    hard_execution = [
        "运行命令",
        "执行命令",
        "打开网页",
        "打开浏览器",
        "下载",
        "删除",
        "登录",
        "截图",
        "发帖",
        "发布",
        "转账",
        "支付",
        "签名",
        "基于当前仓库",
        "基于这个仓库",
        "读取文件",
        "写文件",
    ]
    if any(marker in text for marker in hard_execution):
        return False
    advice_markers = [
        "建议",
        "取舍",
        "策略",
        "对比",
        "方案",
        "解释",
        "总结",
        "优缺点",
        "利弊",
        "权衡",
        "成本",
        "覆盖率",
        "速度",
        "如何选择",
        "怎么选",
        "医疗建议",
        "金融建议",
    ]
    return any(marker in text for marker in advice_markers)


def _filesystem_scope_action(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    if is_host_filesystem_list_request(text):
        return False
    return any(marker in text for marker in ["文件夹", "目录", "文件", "移动"]) or any(
        marker in text for marker in ["整理文件", "整理目录", "整理这些测试日志"]
    )


def _ambiguous_scope(text: str) -> bool:
    if any(marker in text for marker in ["那个", "这个", "某个", "一些", "全部", "所有"]):
        return True
    return not any(
        marker in text
        for marker in ["/", "\\", ".md", ".txt", ".json", "当前目录", "当前项目", "data/"]
    )


def _domain(text: str) -> str:
    if any(marker in text for marker in ["代码", "后端", "API", "数据库", "架构", "报错"]):
        return "software_product"
    if any(marker in text for marker in ["文案", "文章", "报告"]):
        return "writing"
    if any(marker in text for marker in ["钱包", "支付", "转账"]):
        return "finance_or_wallet"
    return "general"


def _capability_available(snapshot: dict[str, Any], key: str) -> bool:
    if key == "skill_engine" and isinstance(snapshot.get("skill"), dict):
        return bool(snapshot["skill"].get("available"))
    if key == "mcp" and isinstance(snapshot.get("mcp_runtime"), dict):
        return bool(snapshot["mcp_runtime"].get("available"))
    status = str(snapshot.get(key) or "")
    return _status_available(status)


def _status_available(status: str) -> bool:
    return status in {"implemented", "degraded"}


def _execution_risks(risks: list[str]) -> list[str]:
    return [risk for risk in risks if risk != "secret_or_sensitive"]


def _continuation_reference(text: str) -> bool:
    return any(
        marker in text
        for marker in ["继续", "刚才", "刚刚", "上一版", "上一个", "按之前", "之前方案", "上次方案"]
    )


def _skill_unavailable_reason(snapshot: dict[str, Any]) -> str:
    raw_skill = snapshot.get("skill")
    skill = raw_skill if isinstance(raw_skill, dict) else {}
    if not _status_available(str(skill.get("status") or snapshot.get("skill_engine") or "")):
        return "skill_runtime_unavailable"
    if int(skill.get("enabled_count") or 0) <= 0:
        return "skill_no_enabled_skill"
    return "skill_unavailable"


def _mcp_unavailable_reason(snapshot: dict[str, Any]) -> str:
    raw_mcp = snapshot.get("mcp_runtime")
    mcp = raw_mcp if isinstance(raw_mcp, dict) else {}
    if not _status_available(str(mcp.get("status") or snapshot.get("mcp") or "")):
        return "mcp_runtime_unavailable"
    if int(mcp.get("ready_server_count") or 0) <= 0:
        return "mcp_no_ready_server"
    if int(mcp.get("active_tool_count") or 0) <= 0:
        return "mcp_no_active_tool"
    return "mcp_unavailable"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
