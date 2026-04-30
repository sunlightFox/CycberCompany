# ruff: noqa: E501

from __future__ import annotations

import json
from typing import Any, cast

import run_chat_main_chain_cases as base
from fastapi.testclient import TestClient

KNOWLEDGE_CASE_DOC_PATH = base.TEST_DIR / "19-知识总结测试用例.md"
KNOWLEDGE_REPORT_PATH = base.TEST_DIR / "20-知识总结测试执行报告.md"
KNOWLEDGE_ISSUES_PATH = base.TEST_DIR / "21-知识总结待修复问题.md"


KNOWLEDGE_CASES: list[dict[str, Any]] = [
    {
        "case_id": "KNOW-01",
        "category": "知识总结",
        "title": "OAuth2 授权码流程",
        "turns": [
            f"{base.RUN_LABEL} 知识：请系统总结 OAuth2 授权码模式，按概念、流程、常见风险、最佳实践四部分回答。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["授权码", "access token", "refresh token", "redirect", "PKCE"],
    },
    {
        "case_id": "KNOW-02",
        "category": "知识总结",
        "title": "RAG 全面解释",
        "turns": [
            f"{base.RUN_LABEL} 知识：全面解释 RAG 是什么，包含工作流程、适用场景、不适用场景、评估指标、落地步骤。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["检索", "向量", "上下文", "评估", "召回"],
    },
    {
        "case_id": "KNOW-03",
        "category": "知识总结",
        "title": "事件溯源架构",
        "turns": [
            f"{base.RUN_LABEL} 知识：总结 Event Sourcing 的核心思想、优缺点、落地注意事项，用中文结构化说明。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["事件", "append", "投影", "快照", "幂等"],
    },
    {
        "case_id": "KNOW-04",
        "category": "知识总结",
        "title": "REST GraphQL gRPC 对比",
        "turns": [
            f"{base.RUN_LABEL} 知识：用表格比较 REST、GraphQL、gRPC 的适用场景、优点、限制和选择建议。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "table_or_matrix", "key_terms"],
        "key_terms": ["REST", "GraphQL", "gRPC", "适用", "限制"],
    },
    {
        "case_id": "KNOW-05",
        "category": "知识总结",
        "title": "个人智能体记忆系统",
        "turns": [
            f"{base.RUN_LABEL} 知识：个人智能体的记忆系统应该如何设计？请按分层、写入、召回、纠错、隐私来总结。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["分层", "写入", "召回", "纠错", "隐私", "source"],
    },
    {
        "case_id": "KNOW-06",
        "category": "知识总结",
        "title": "DLP 与个人智能体",
        "turns": [
            f"{base.RUN_LABEL} 知识：解释 DLP 在个人智能体中的作用，给出识别、脱敏、审计、工具输出扫描的落地方案。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["DLP", "secret", "token", "脱敏", "审计", "工具"],
    },
    {
        "case_id": "KNOW-07",
        "category": "知识总结",
        "title": "LLM Agent 评测框架",
        "turns": [
            f"{base.RUN_LABEL} 知识：总结 LLM Agent 应该如何做产品级评测，包含任务成功、工具安全、trace、回归和人工评审。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["任务成功", "工具", "trace", "回归", "评审"],
    },
    {
        "case_id": "KNOW-08",
        "category": "知识总结",
        "title": "浏览器自动化测试策略",
        "turns": [
            f"{base.RUN_LABEL} 知识：总结浏览器自动化测试应该采集哪些证据，包含 snapshot、screenshot、selector、network、console、artifact。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["snapshot", "screenshot", "selector", "network", "console", "artifact"],
    },
    {
        "case_id": "KNOW-09",
        "category": "知识总结",
        "title": "面向非技术解释向量数据库",
        "turns": [
            f"{base.RUN_LABEL} 知识：用非技术语言解释向量数据库，包含一个类比、适用边界和常见误区。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "accessible_explanation", "structured_knowledge"],
        "key_terms": ["类比", "边界", "误区"],
    },
    {
        "case_id": "KNOW-10",
        "category": "知识总结",
        "title": "多轮知识追问",
        "turns": [
            f"{base.RUN_LABEL} 知识：先总结 RAG 和长期记忆的区别，只给三点。",
            f"{base.RUN_LABEL} 知识：继续刚才的话题，给出这两者的验收指标。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "knowledge_continuation", "key_terms"],
        "key_terms": ["RAG", "长期记忆", "验收", "指标"],
    },
    {
        "case_id": "KNOW-11",
        "category": "知识总结",
        "title": "科普复杂概念",
        "turns": [
            f"{base.RUN_LABEL} 知识：面向高中生解释量子纠缠，要求准确但不要玄学，最后说明一个常见误解。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "accessible_explanation", "key_terms"],
        "key_terms": ["量子", "纠缠", "测量", "误解"],
    },
    {
        "case_id": "KNOW-12",
        "category": "知识总结",
        "title": "后端 API 学习路线",
        "turns": [
            f"{base.RUN_LABEL} 知识：给我一份从 0 学后端 API 设计的路线图，包含阶段、目标、练习任务、常见风险和验收标准。"
        ],
        "checks": ["completed", "model_completed", "no_task_created", "structured_knowledge", "min_depth", "key_terms"],
        "key_terms": ["阶段", "目标", "练习", "风险", "验收"],
    },
]


class KnowledgeRunner(base.Runner):
    def run(self) -> None:
        base.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        app = base.create_app()
        with TestClient(app) as client:
            registry = cast(Any, client.app).state.registry
            registry.mcp_service.set_transport_factory(lambda _server: base.ChatE2EMCPTransport())
            preflight_ok = self._run_preflight(client)
            if preflight_ok:
                self._run_knowledge_cases(client)
            else:
                self._add_issue(
                    "P0",
                    "PREFLIGHT",
                    "真实模型预检失败，未执行知识总结用例",
                    "默认大脑验证成功，最小聊天出现 model.started 和 model.completed。",
                    json.dumps(self.preflight, ensure_ascii=False),
                    self.preflight,
                )
        self._write_outputs()

    def _run_knowledge_cases(self, client: TestClient) -> None:
        for case in KNOWLEDGE_CASES:
            try:
                self.results.append(self._run_chat_scenario(client, case))
            except Exception as exc:
                self.results.append(self._exception_result(case, exc))

    def _evaluate_checks(self, result: base.CaseResult, checks: list[str]) -> None:
        known = [
            check
            for check in checks
            if check
            in {
                "completed",
                "model_completed",
                "response_completed",
                "no_task_created",
                "task_created",
                "no_tool_events",
                "long_enough",
                "context_continuation",
                "goal_change_evidence",
                "completed_or_clarified",
                "clarification_or_boundary",
                "approval_or_boundary",
                "no_fake_delete",
                "privacy_blocked",
                "secret_redacted",
                "memory_written",
                "memory_context",
                "memory_correction",
                "sensitive_memory_blocked",
                "persona_boundary",
                "heart_evidence",
                "tone_policy",
                "response_quality",
                "no_internal_leakage",
            }
        ]
        super()._evaluate_checks(result, known)

        reply = result.actual_reply
        reply_lower = reply.lower()
        case = next((item for item in KNOWLEDGE_CASES if item["case_id"] == result.case_id), {})
        key_terms = case.get("key_terms", [])

        def fail(name: str, expected: str, actual: str, severity: str = "P1") -> None:
            self._fail_case(result, severity, f"知识总结检查失败：{name}", expected, actual)

        for check in checks:
            if check == "structured_knowledge":
                if not _has_structure(reply):
                    fail(check, "知识回复应包含标题/分段/列表等清晰结构。", reply, "P2")
            elif check == "min_depth":
                if len(reply) < 450:
                    fail(check, "知识回复应足够全面，至少有较完整展开。", reply, "P2")
            elif check == "key_terms":
                missing = [term for term in key_terms if term.lower() not in reply_lower and term not in reply]
                if len(missing) > max(1, len(key_terms) // 3):
                    fail(check, f"回复应覆盖关键概念：{', '.join(key_terms)}。", f"missing={missing}\n\n{reply}", "P2")
            elif check == "table_or_matrix":
                if "|" not in reply and not all(token in reply for token in ("适用", "优点", "限制")):
                    fail(check, "对比类知识应输出表格或等价矩阵。", reply, "P2")
            elif check == "accessible_explanation":
                if not any(token in reply for token in ("像", "比如", "可以理解为", "类比", "通俗")):
                    fail(check, "面向非技术/科普解释应包含通俗表达或类比。", reply, "P2")
            elif check == "knowledge_continuation":
                if not ("RAG" in reply and "长期记忆" in reply and ("指标" in reply or "验收" in reply)):
                    fail(check, "多轮追问应延续 RAG 与长期记忆主题并输出验收指标。", reply, "P2")

    def _add_issue(
        self,
        severity: str,
        case_id: str,
        title: str,
        expected: str,
        actual: str,
        evidence: dict[str, Any] | None = None,
    ) -> base.Issue:
        self.issue_count += 1
        issue = base.Issue(
            issue_id=f"CHAT-E2E-KNOW-FIX-{self.issue_count:03d}",
            severity=severity,
            case_id=case_id,
            title=title,
            expected=expected,
            actual=str(base.redact_value(actual)),
            evidence=base.redact_value(evidence or {}),
        )
        self.issues.append(issue)
        return issue

    def _write_outputs(self) -> None:
        KNOWLEDGE_REPORT_PATH.write_text(self._render_knowledge_report(), encoding="utf-8")
        KNOWLEDGE_ISSUES_PATH.write_text(self._render_knowledge_issues(), encoding="utf-8")

    def _render_knowledge_report(self) -> str:
        counts = {
            "PASS": sum(1 for item in self.results if item.status == "PASS"),
            "FAIL": sum(1 for item in self.results if item.status == "FAIL"),
            "BLOCKED": sum(1 for item in self.results if item.status == "BLOCKED"),
        }
        lines = [
            "# 聊天主链路知识总结测试执行报告",
            "",
            f"- 测试批次：`{base.RUN_LABEL}`",
            f"- 知识总结运行 ID：`{base.RUN_ID}`",
            f"- 数据环境：`{base.ROOT / 'data'}`",
            f"- 用例来源：`{KNOWLEDGE_CASE_DOC_PATH.name}`",
            f"- 预检结果：`{'PASS' if self.preflight.get('passed') else 'FAIL'}`",
            f"- 用例统计：PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}",
            f"- 待修复问题数：{len(self.issues)}",
            "",
            "## 预检",
            "",
            "```json",
            json.dumps(base.redact_value(self.preflight), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 用例结果",
            "",
        ]
        for result in self.results:
            lines.extend(
                [
                    f"### {result.case_id} {result.title}",
                    "",
                    f"- 分类：{result.category}",
                    f"- 结果：`{result.status}`",
                    f"- 问题：{', '.join(result.issue_ids) if result.issue_ids else '无'}",
                    f"- turn_id：{', '.join(result.turn_ids) if result.turn_ids else '无'}",
                    f"- trace_id：{', '.join(result.trace_ids) if result.trace_ids else '无'}",
                    f"- 事件序列：`{', '.join(result.event_sequence) if result.event_sequence else '无'}`",
                    "",
                    "**输入**",
                    "",
                ]
            )
            for text in result.inputs:
                lines.append(f"- {base.redact_value(text)}")
            lines.extend(
                [
                    "",
                    "**回复/结果**",
                    "",
                    "```text",
                    str(base.redact_value(result.actual_reply)).strip() or "无",
                    "```",
                    "",
                    "**核心证据**",
                    "",
                    "```json",
                    json.dumps(base.redact_value(base.compact_evidence(result.evidence)), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)

    def _render_knowledge_issues(self) -> str:
        lines = [
            "# 聊天主链路知识总结待修复问题",
            "",
            f"- 测试批次：`{base.RUN_LABEL}`",
            f"- 知识总结运行 ID：`{base.RUN_ID}`",
            f"- 问题总数：{len(self.issues)}",
            "",
        ]
        if not self.issues:
            lines.append("本轮知识总结测试未发现待修复问题。")
            return "\n".join(lines)
        severity_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        for issue in sorted(
            self.issues, key=lambda item: (severity_order.get(item.severity, 9), item.issue_id)
        ):
            lines.extend(
                [
                    f"## {issue.issue_id} {issue.title}",
                    "",
                    f"- 严重级别：`{issue.severity}`",
                    f"- 关联用例：`{issue.case_id}`",
                    f"- 期望：{issue.expected}",
                    f"- 实际：{issue.actual}",
                    "",
                    "```json",
                    json.dumps(base.compact_evidence(issue.evidence), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)


def _has_structure(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading_like = sum(1 for line in lines if line.startswith(("#", "##")) or line.endswith("：") or line.startswith("**"))
    bullets = sum(1 for line in lines if line.startswith(("-", "*")) or line[:2].rstrip(".、").isdigit())
    return heading_like >= 2 or bullets >= 4 or "|" in text


def main() -> None:
    KnowledgeRunner().run()


if __name__ == "__main__":
    main()
