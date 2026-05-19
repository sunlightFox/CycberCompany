from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

THIS_FILE = Path(__file__).resolve()
TEST_DIR = THIS_FILE.parent
ROOT = THIS_FILE.parents[4]

RUN_LABEL = "CHAT-KNOWLEDGE-SUMMARY-20"
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
RUN_TAG = f"{RUN_LABEL}-{RUN_ID}"
SESSION_ID = f"knowledge_summary_session_{RUN_ID.lower()}"

REPORT_PATH = TEST_DIR / "02-20轮知识总结结构质量测试执行报告.md"
ISSUES_PATH = TEST_DIR / "03-20轮知识总结低分问题与优化建议.md"

PYTHONPATHS = [
    "apps/local-api",
    "packages/core-types",
    "services/asset-broker",
    "services/brain",
    "services/capability-graph",
    "services/chat-runtime",
    "services/context-gateway",
    "services/heart",
    "services/memory",
    "services/persona-engine",
    "services/response-composer",
    "services/safety",
    "services/shell-runtime",
    "services/skill-engine",
    "services/task-engine",
    "services/tools",
    "services/trace",
]

for rel in reversed(PYTHONPATHS):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from app.main import create_app  # noqa: E402


SOURCE_A = (
    "素材：本周 API 稳定性回顾。"
    "上线后 3 天内出现 2 次 500 错误，主要集中在订单查询。"
    "修复动作包括补充超时保护、增加索引、补齐回归用例。"
    "当前风险是夜间流量峰值还没复测。"
)
SOURCE_B = (
    "素材：会议纪要。"
    "前端需要更清楚的错误提示；"
    "后端本周先修分页查询慢；"
    "测试补一组导出链路回归；"
    "负责人分别是 Lin、Qiao、Ming；"
    "目标是周五前完成。"
)
SOURCE_C = (
    "素材：REST 适合通用 CRUD；GraphQL 适合客户端按需取字段；"
    "gRPC 适合高吞吐内部服务调用。"
    "GraphQL 的风险是缓存和权限治理更复杂；"
    "gRPC 的门槛是调试与浏览器直连不友好。"
)
SOURCE_D = (
    "素材：事故记录。"
    "09:10 开始报警；09:18 确认订单接口抖动；"
    "09:32 回滚新索引；09:47 错误率回落；"
    "10:05 补充监控阈值。"
)
SOURCE_E = (
    "素材：候选方案比较。"
    "方案一是继续堆缓存，见效快但会掩盖查询设计问题；"
    "方案二是重写 SQL 并补索引，收益更稳但改动更大；"
    "方案三是拆分读写流量，适合中期扩展但本周落地成本最高。"
)


@dataclass
class Issue:
    issue_id: str
    case_id: str
    severity: str
    title: str
    reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    case_id: str
    title: str
    prompt: str
    reply: str = ""
    status: str = "PASS"
    score_total: int = 0
    score_breakdown: dict[str, int] = field(default_factory=dict)
    system_quality_score: float | None = None
    tone_mode: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    issue_ids: list[str] = field(default_factory=list)


CASES: list[dict[str, Any]] = [
    {
        "case_id": "KS-001",
        "title": "只输出标题",
        "prompt": f"{RUN_TAG}：请把下面素材总结成一个简短标题，只输出标题，不要表格、不要段落、不要解释。\n{SOURCE_A}",
        "max_lines": 1,
        "forbid_heading": True,
        "forbid_table": True,
        "forbid_bullets": True,
        "expected_any": [r"API", r"稳定", r"回顾|复盘"],
    },
    {
        "case_id": "KS-002",
        "title": "一级标题加一段总结",
        "prompt": f"{RUN_TAG}：用 Markdown 一级标题加一段总结概括下面内容，不要列表。\n{SOURCE_A}",
        "expect_heading_level": 1,
        "expect_paragraphs": 1,
        "forbid_table": True,
        "forbid_bullets": True,
        "expected_any": [r"500", r"订单", r"风险"],
    },
    {
        "case_id": "KS-003",
        "title": "二级标题分段",
        "prompt": (
            f"{RUN_TAG}：把下面素材整理成三个 Markdown 二级标题：`结论`、`依据`、`风险`。"
            "每个标题下用一小段，不要表格。\n"
            f"{SOURCE_A}"
        ),
        "expect_heading_level": 2,
        "expect_section_headers": ["结论", "依据", "风险"],
        "expect_paragraphs": 3,
        "forbid_table": True,
        "expected_any": [r"超时保护|索引|回归用例"],
    },
    {
        "case_id": "KS-004",
        "title": "对比表格",
        "prompt": f"{RUN_TAG}：用 Markdown 表格比较下面三种接口风格，列至少包含“方式”“适用场景”“风险”。\n{SOURCE_C}",
        "expect_table": True,
        "expected_any": [r"REST", r"GraphQL", r"gRPC"],
    },
    {
        "case_id": "KS-005",
        "title": "禁止表格只用两段",
        "prompt": f"{RUN_TAG}：同样总结下面素材，但这次明确不要表格，用两段自然段写清区别与风险。\n{SOURCE_C}",
        "expect_paragraphs": 2,
        "forbid_table": True,
        "forbid_bullets": True,
        "expected_any": [r"REST", r"GraphQL", r"gRPC"],
    },
    {
        "case_id": "KS-006",
        "title": "标题加行动项列表",
        "prompt": f"{RUN_TAG}：把下面会议纪要整理成“一级标题 + 4 条行动项列表”。\n{SOURCE_B}",
        "expect_heading_level": 1,
        "expect_bullets": 4,
        "forbid_table": True,
        "expected_any": [r"Lin|Qiao|Ming|周五"],
    },
    {
        "case_id": "KS-007",
        "title": "时间线表格",
        "prompt": f"{RUN_TAG}：把下面事故记录整理成 Markdown 表格，列名至少有“时间”“事件”“结果”。\n{SOURCE_D}",
        "expect_table": True,
        "expected_any": [r"09:10", r"09:47", r"10:05"],
    },
    {
        "case_id": "KS-008",
        "title": "已知未知下一步",
        "prompt": (
            f"{RUN_TAG}：把下面事故内容总结成三个 Markdown 二级标题：`已知`、`未知`、`下一步`，"
            "每段都要短。\n"
            f"{SOURCE_A}"
        ),
        "expect_heading_level": 2,
        "expect_section_headers": ["已知", "未知", "下一步"],
        "expect_paragraphs": 3,
        "expected_any": [r"夜间流量峰值|复测"],
    },
    {
        "case_id": "KS-009",
        "title": "高管摘要三条",
        "prompt": f"{RUN_TAG}：面向负责人，把下面内容压缩成 3 条要点，每条不超过一行。\n{SOURCE_E}",
        "expect_bullets": 3,
        "max_lines": 3,
        "forbid_table": True,
        "expected_any": [r"缓存|SQL|读写"],
    },
    {
        "case_id": "KS-010",
        "title": "记住结构偏好",
        "prompt": f"{RUN_TAG}：记住这轮对话里的总结偏好：先标题，再表格，最后一段结论。",
        "expected_any": [r"记住|这轮", r"先标题", r"再表格", r"最后一段结论"],
    },
    {
        "case_id": "KS-011",
        "title": "按记住的偏好输出",
        "prompt": f"{RUN_TAG}：按我刚刚设定的结构偏好，总结下面素材。\n{SOURCE_C}",
        "expect_recall": "先标题，再表格，最后一段结论",
        "expect_heading_level": 1,
        "expect_table": True,
        "expect_paragraphs": 1,
        "expected_any": [r"REST", r"GraphQL", r"gRPC"],
    },
    {
        "case_id": "KS-012",
        "title": "修正偏好为纯段落",
        "prompt": f"{RUN_TAG}：修正一下，这轮接下来的总结不要表格了，改成“标题 + 两段段落”。",
        "expected_any": [r"修正", r"不要表格", r"标题", r"两段"],
    },
    {
        "case_id": "KS-013",
        "title": "按修正后的偏好输出",
        "prompt": f"{RUN_TAG}：现在按修正后的偏好，总结下面素材。\n{SOURCE_E}",
        "expect_recall": "不要表格",
        "expect_heading_level": 1,
        "expect_paragraphs": 2,
        "forbid_table": True,
        "expected_any": [r"缓存|SQL|读写"],
    },
    {
        "case_id": "KS-014",
        "title": "小标题加短段落",
        "prompt": (
            f"{RUN_TAG}：把下面素材整理成“背景 / 现状 / 风险”三个小标题，"
            "每个小标题下一段话，不要列表。\n"
            f"{SOURCE_B}"
        ),
        "expect_section_headers": ["背景", "现状", "风险"],
        "expect_paragraphs": 3,
        "forbid_bullets": True,
        "forbid_table": True,
        "expected_any": [r"错误提示|分页查询|导出链路"],
    },
    {
        "case_id": "KS-015",
        "title": "结论后附简表",
        "prompt": (
            f"{RUN_TAG}：先用一句话给结论，再附一个两列表格，"
            "列出下面三种方案的“方案”和“取舍”。\n"
            f"{SOURCE_E}"
        ),
        "expect_table": True,
        "expect_direct": True,
        "expected_any": [r"缓存|SQL|读写"],
    },
    {
        "case_id": "KS-016",
        "title": "严格两段不分点",
        "prompt": f"{RUN_TAG}：把下面会议纪要改写成严格两段，不要标题、不要列表、不要表格。\n{SOURCE_B}",
        "expect_paragraphs": 2,
        "forbid_heading": True,
        "forbid_table": True,
        "forbid_bullets": True,
        "expected_any": [r"Lin|Qiao|Ming|周五"],
    },
    {
        "case_id": "KS-017",
        "title": "标题加编号列表",
        "prompt": f"{RUN_TAG}：给下面内容写一个标题，并用编号列表列出 3 个关键观察。\n{SOURCE_A}",
        "expect_heading_level": 1,
        "expect_numbered": 3,
        "forbid_table": True,
        "expected_any": [r"500|索引|回归用例|复测"],
    },
    {
        "case_id": "KS-018",
        "title": "表格转自然段",
        "prompt": (
            f"{RUN_TAG}：假设读者不喜欢表格，请把下面比较信息写成一段完整段落，"
            "强调适用场景和代价。\n"
            f"{SOURCE_C}"
        ),
        "expect_paragraphs": 1,
        "forbid_table": True,
        "forbid_bullets": True,
        "expected_any": [r"适合|风险|复杂|门槛"],
    },
    {
        "case_id": "KS-019",
        "title": "总结并给标题表格段落混合",
        "prompt": (
            f"{RUN_TAG}：请严格按“一级标题 + 表格 + 结论段落”输出下面素材，"
            "表格列至少包含“项”“状态”“备注”。\n"
            f"{SOURCE_B}"
        ),
        "expect_heading_level": 1,
        "expect_table": True,
        "expect_paragraphs": 1,
        "expected_any": [r"前端|后端|测试|周五"],
    },
    {
        "case_id": "KS-020",
        "title": "全局收尾分析",
        "prompt": (
            f"{RUN_TAG}：结合这 20 轮里的结构偏好测试，"
            "给我一个“标题 + 三条观察 + 一段结论”的质量分析，"
            "重点说标题、表格、段落的服从度。"
        ),
        "expect_heading_level": 1,
        "expect_bullets": 3,
        "expect_paragraphs": 1,
        "expected_any": [r"标题", r"表格", r"段落"],
    },
]

MECHANICAL_OPENERS = ("好的", "当然", "可以的", "没问题", "下面是", "我来")
FORMAT_LEAK_WORDS = ("trace_id", "approval_id", "tool_call_id", "system prompt", "developer")


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        data_lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        if payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def extract_reply(events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("event") == "response.completed":
            payload = event.get("payload", {})
            plan = payload.get("response_plan", {}) if isinstance(payload, dict) else {}
            if isinstance(plan, dict):
                return str(plan.get("plain_text") or plan.get("summary") or "")
    return "".join(
        str(event.get("payload", {}).get("text", ""))
        for event in events
        if event.get("event") == "response.delta"
    )


def find_event(events: list[dict[str, Any]], event_name: str) -> dict[str, Any] | None:
    for event in events:
        if event.get("event") == event_name:
            return event
    return None


def first_line(text: str) -> str:
    return next((line.strip() for line in str(text).splitlines() if line.strip()), "")


def line_count(text: str) -> int:
    return len([line for line in str(text).splitlines() if line.strip()])


def has_heading(text: str, level: int | None = None) -> bool:
    if level is None:
        return bool(re.search(r"(?m)^#{1,3}\s+\S+", str(text)))
    return bool(re.search(rf"(?m)^#{{{level}}}\s+\S+", str(text)))


def table_row_count(text: str) -> int:
    rows = 0
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped.count("|") >= 2 and not re.fullmatch(r"[:\-\|\s]+", stripped):
            rows += 1
    return rows


def bullet_count(text: str) -> int:
    return len(re.findall(r"(?m)^[-*]\s+\S+", str(text)))


def numbered_count(text: str) -> int:
    return len(re.findall(r"(?m)^\d+\.\s+\S+", str(text)))


def paragraph_count(text: str) -> int:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", str(text).strip()) if block.strip()]
    paragraphs = 0
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(re.match(r"^#{1,3}\s+\S+", line) for line in lines):
            continue
        if all(line.count("|") >= 2 for line in lines):
            continue
        if all(re.match(r"^([-*]|\d+\.)\s+\S+", line) for line in lines):
            continue
        paragraphs += 1
    return paragraphs


def has_internal_leakage(text: str) -> bool:
    lowered = str(text).lower()
    return any(word in lowered for word in FORMAT_LEAK_WORDS)


def is_mechanical(text: str) -> bool:
    opening = first_line(text)
    return any(opening.startswith(item) for item in MECHANICAL_OPENERS) and len(opening) <= 10


class KnowledgeSummaryRunner:
    def __init__(self) -> None:
        self.results: list[CaseResult] = []
        self.issues: list[Issue] = []
        self.issue_index = 0
        self.compiled_soul: dict[str, Any] = {}
        self.persona_profile: dict[str, Any] = {}
        self.conversation_id: str | None = None

    def run(self) -> None:
        app = create_app()
        with TestClient(app) as client:
            self._load_baseline(client)
            for case in CASES:
                result = self._run_case(client, case)
                self.results.append(result)
        REPORT_PATH.write_text(self._render_report(), encoding="utf-8")
        ISSUES_PATH.write_text(self._render_issues(), encoding="utf-8")

    def _load_baseline(self, client: TestClient) -> None:
        soul = client.get("/api/persona/mem_xiaoyao/soul/compiled")
        if soul.status_code == 200:
            self.compiled_soul = soul.json()
        profiles = client.get("/api/persona/profiles")
        if profiles.status_code == 200:
            for item in profiles.json().get("items") or []:
                if str(item.get("persona_profile_id")) == str(self.compiled_soul.get("persona_profile_id", "")):
                    self.persona_profile = item
                    break

    def _run_case(self, client: TestClient, case: dict[str, Any]) -> CaseResult:
        result = CaseResult(case_id=case["case_id"], title=case["title"], prompt=str(case["prompt"]))
        response = client.post(
            "/api/chat/turn",
            json={
                "session_id": SESSION_ID,
                "conversation_id": self.conversation_id,
                "member_id": "mem_xiaoyao",
                "input": {"type": "text", "text": result.prompt},
                "client_context": {"timezone": "Asia/Shanghai", "locale": "zh-CN"},
            },
        )
        if response.status_code != 200:
            result.status = "FAIL"
            result.reply = response.text
            result.reasons = ["turn_create_failed"]
            return result

        created = response.json()
        self.conversation_id = created["conversation_id"]
        result.turn_id = created["turn_id"]
        result.trace_id = created["trace_id"]
        events = parse_sse(client.get(created["stream_url"]).text)
        detail = client.get(f"/api/chat/turns/{created['turn_id']}").json()
        tone = client.get(f"/api/chat/turns/{created['turn_id']}/tone-policy").json()
        quality = client.get(f"/api/chat/turns/{created['turn_id']}/response-quality").json()
        failed_event = find_event(events, "turn.failed")
        result.reply = extract_reply(events)
        if not result.reply and isinstance(detail, dict):
            result.reply = str(detail.get("error_message") or "")
        result.tone_mode = tone.get("tone_mode")
        result.system_quality_score = float(quality.get("score") or 0.0) * 10
        result.evidence = {
            "event_sequence": [event.get("event") for event in events],
            "turn_status": detail.get("status"),
            "intent": detail.get("intent"),
            "mode": detail.get("mode"),
            "error_code": detail.get("error_code"),
            "error_message": detail.get("error_message"),
            "failed_event": failed_event,
            "last_event": events[-1] if events else None,
            "tone_policy": {
                "tone_mode": tone.get("tone_mode"),
                "reason_codes": tone.get("reason_codes"),
            },
            "response_quality": {
                "score": quality.get("score"),
                "passed": quality.get("passed"),
                "quality_markers": quality.get("quality_markers"),
                "violations": quality.get("violations"),
            },
        }
        self._score_case(result, case)
        return result

    def _score_case(self, result: CaseResult, case: dict[str, Any]) -> None:
        text = result.reply.strip()
        score = {
            "格式服从": 3,
            "总结覆盖": 3,
            "清晰度": 2,
            "自然度": 1,
            "边界诚实": 1,
        }
        reasons: list[str] = []

        if "turn.failed" in (result.evidence.get("event_sequence") or []):
            result.score_breakdown = {
                "格式服从": 0,
                "总结覆盖": 0,
                "清晰度": 1,
                "自然度": 1,
                "边界诚实": 1,
            }
            result.score_total = sum(result.score_breakdown.values())
            result.reasons = ["turn_failed"]
            result.status = "FAIL"
            self._append_issue(result, result.reasons)
            return

        expected_any = [re.compile(item) for item in case.get("expected_any") or []]
        matched = sum(1 for pattern in expected_any if pattern.search(text))
        if expected_any and matched < max(1, len(expected_any) // 2):
            score["总结覆盖"] -= 2
            reasons.append("summary_signal_missing")
        elif expected_any and matched < len(expected_any):
            score["总结覆盖"] -= 1
            reasons.append("summary_partial_coverage")

        if case.get("expect_recall") and str(case["expect_recall"]) not in text:
            score["总结覆盖"] -= 1
            score["格式服从"] -= 1
            reasons.append("structure_preference_not_recalled")

        if case.get("expect_heading_level") and not has_heading(text, int(case["expect_heading_level"])):
            score["格式服从"] -= 1
            reasons.append("missing_required_heading")

        if case.get("expect_section_headers"):
            missing = [
                header for header in case["expect_section_headers"]
                if str(header) not in text
            ]
            if missing:
                score["格式服从"] -= 1
                score["总结覆盖"] -= 1
                reasons.append("missing_section_headers")

        if case.get("expect_table") and table_row_count(text) < 2:
            score["格式服从"] -= 2
            reasons.append("missing_table")

        if case.get("expect_bullets") and bullet_count(text) < int(case["expect_bullets"]):
            score["格式服从"] -= 1
            reasons.append("missing_bullets")

        if case.get("expect_numbered") and numbered_count(text) < int(case["expect_numbered"]):
            score["格式服从"] -= 1
            reasons.append("missing_numbered_list")

        if case.get("expect_paragraphs") and paragraph_count(text) < int(case["expect_paragraphs"]):
            score["格式服从"] -= 1
            score["清晰度"] -= 1
            reasons.append("missing_paragraphs")

        if case.get("forbid_table") and table_row_count(text) > 0:
            score["格式服从"] -= 2
            reasons.append("unexpected_table")

        if case.get("forbid_heading") and has_heading(text):
            score["格式服从"] -= 1
            reasons.append("unexpected_heading")

        if case.get("forbid_bullets") and (bullet_count(text) > 0 or numbered_count(text) > 0):
            score["格式服从"] -= 1
            reasons.append("unexpected_list")

        if case.get("expect_direct"):
            opening = first_line(text)
            if len(opening) > 90 or opening.startswith("下面"):
                score["清晰度"] -= 1
                reasons.append("not_direct_enough")

        if case.get("max_lines") and line_count(text) > int(case["max_lines"]):
            score["清晰度"] -= 1
            reasons.append("line_count_exceeded")

        if is_mechanical(text):
            score["自然度"] = 0
            reasons.append("mechanical_opening")

        if has_internal_leakage(text):
            score["边界诚实"] = 0
            reasons.append("internal_leakage")

        violations = result.evidence.get("response_quality", {}).get("violations") or []
        if violations:
            categories = {
                str(item.get("category") or item.get("type") or "")
                for item in violations
                if isinstance(item, dict)
            }
            if {"weak_structure", "too_short"} & categories:
                score["格式服从"] = max(score["格式服从"] - 1, 0)
                reasons.append("quality_guard_structure_warning")
            if {"too_stiff", "systemic_tone"} & categories:
                score["自然度"] = max(score["自然度"] - 1, 0)
                reasons.append("quality_guard_tone_warning")

        score = {key: max(value, 0) for key, value in score.items()}
        result.score_breakdown = score
        result.score_total = sum(score.values())
        result.reasons = reasons
        if result.score_total < 8:
            result.status = "FAIL"
            self._append_issue(result, reasons)

    def _append_issue(self, result: CaseResult, reasons: list[str]) -> None:
        self.issue_index += 1
        severity = "P2" if result.score_total >= 6 else "P1"
        self.issues.append(
            Issue(
                issue_id=f"KNOWLEDGE-SUMMARY-FIX-{self.issue_index:03d}",
                case_id=result.case_id,
                severity=severity,
                title="知识总结结构质量低分",
                reasons=reasons,
                suggestions=self._suggestions_for(reasons),
                evidence={
                    "reply": result.reply,
                    "score_breakdown": result.score_breakdown,
                    "system_quality_score": result.system_quality_score,
                    "tone_mode": result.tone_mode,
                    "evidence": result.evidence,
                },
            )
        )
        result.issue_ids.append(self.issues[-1].issue_id)

    def _suggestions_for(self, reasons: list[str]) -> list[str]:
        mapping = {
            "turn_failed": "把 turn.failed 的错误码、错误消息和失败事件 payload 直接落到报告里，并继续追到具体失败环节，避免把执行失败误记成结构不达标。",
            "summary_signal_missing": "补强总结抽取模板，确保素材里的核心实体、风险和动作至少出现一半以上。",
            "summary_partial_coverage": "在压缩总结时保留关键名词，不要只剩抽象判断。",
            "structure_preference_not_recalled": "加强同会话结构偏好记忆，后续轮次优先继承最近一次格式指令。",
            "missing_required_heading": "给标题类任务增加更强的 Markdown heading 骨架提示。",
            "missing_section_headers": "对指定章节标题启用固定锚点，不要让模型自由改写标题名。",
            "missing_table": "比较与时间线场景优先套表格骨架，先产列名再填内容。",
            "missing_bullets": "行动项和观察类任务保持列表化输出，不要自动改成散文。",
            "missing_numbered_list": "编号列表任务增加强格式约束，减少普通项目符号替代。",
            "missing_paragraphs": "段落型总结至少保留最小段落数，避免整段挤成一句或全变列表。",
            "unexpected_table": "明确禁止表格时要降级到段落模板，不要沿用上一轮比较表惯性。",
            "unexpected_heading": "仅标题或纯段落场景下收紧 Markdown heading 触发条件。",
            "unexpected_list": "纯段落任务禁用 bullet 骨架，改用连接词组织信息。",
            "not_direct_enough": "先给结论句，再展开说明，减少‘下面是’之类铺垫。",
            "line_count_exceeded": "压缩输出长度，优先删修饰语和重复过渡句。",
            "mechanical_opening": "弱化固定套话开头，让总结直接进入信息本体。",
            "internal_leakage": "继续保持可见层过滤，避免内部术语出现在总结结果里。",
            "quality_guard_structure_warning": "结合 response quality guard 的结构告警，补足最小结构骨架。",
            "quality_guard_tone_warning": "结构总结也要保留自然语气，避免变成僵硬模板。",
        }
        suggestions = [mapping[item] for item in reasons if item in mapping]
        return suggestions or ["复核该 case 的结构要求和关键词抽取规则，收紧格式与覆盖率判分。"]

    def _render_report(self) -> str:
        avg_score = round(sum(item.score_total for item in self.results) / max(len(self.results), 1), 2)
        avg_system = round(sum(item.system_quality_score or 0.0 for item in self.results) / max(len(self.results), 1), 2)
        pass_count = sum(1 for item in self.results if item.status == "PASS")
        fail_count = len(self.results) - pass_count
        lines = [
            "# 02 20轮知识总结结构质量测试执行报告",
            "",
            f"- 测试批次：`{RUN_LABEL}`",
            f"- 运行 ID：`{RUN_ID}`",
            f"- 会话 ID：`{SESSION_ID}`",
            f"- 总轮数：`{len(self.results)}`",
            f"- 通过数：`{pass_count}`",
            f"- 失败数：`{fail_count}`",
            f"- 平均规则分：`{avg_score}/10`",
            f"- 平均系统质量分：`{avg_system}/10`",
            "",
            "## 基线",
            "",
            "```json",
            json.dumps(
                {
                    "compiled_soul": {
                        "persona_profile_id": self.compiled_soul.get("persona_profile_id"),
                        "display_name": self.compiled_soul.get("display_name"),
                        "summary": self.compiled_soul.get("summary"),
                        "tone_policy": self.compiled_soul.get("tone_policy"),
                    },
                    "profile": {
                        "display_name": self.persona_profile.get("display_name"),
                        "summary": self.persona_profile.get("summary"),
                        "default_mode": self.persona_profile.get("default_mode"),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## 总表",
            "",
            "| Case ID | 标题 | 规则分 | 系统分 | tone_mode | 结果 | 原因 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in self.results:
            reason_text = "<br>".join(item.reasons) if item.reasons else "—"
            lines.append(
                f"| `{item.case_id}` | {item.title} | `{item.score_total}` | "
                f"`{round(item.system_quality_score or 0.0, 2)}` | `{item.tone_mode or 'n/a'}` | "
                f"`{item.status}` | {reason_text} |"
            )
        lines.extend(["", "## 分轮详情", ""])
        for item in self.results:
            lines.extend(
                [
                    f"### {item.case_id} {item.title}",
                    "",
                    f"- 规则分：`{item.score_total}/10`",
                    f"- 系统质量分：`{round(item.system_quality_score or 0.0, 2)}/10`",
                    f"- tone_mode：`{item.tone_mode}`",
                    f"- issue：`{', '.join(item.issue_ids) if item.issue_ids else '无'}`",
                    "",
                    "**输入**",
                    "",
                    "```text",
                    item.prompt,
                    "```",
                    "",
                    "**回复**",
                    "",
                    "```text",
                    item.reply.strip() or "(空回复)",
                    "```",
                    "",
                    "**评分拆解**",
                    "",
                    "```json",
                    json.dumps(item.score_breakdown, ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "**诊断**",
                    "",
                    "```json",
                    json.dumps(item.evidence, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)

    def _render_issues(self) -> str:
        lines = [
            "# 03 20轮知识总结低分问题与优化建议",
            "",
            f"- 测试批次：`{RUN_LABEL}`",
            f"- 运行 ID：`{RUN_ID}`",
            f"- 问题总数：`{len(self.issues)}`",
            "",
        ]
        if not self.issues:
            lines.append("本轮未发现低于 8 分的知识总结结构质量问题。")
            return "\n".join(lines)
        for issue in self.issues:
            related = next(item for item in self.results if item.case_id == issue.case_id)
            lines.extend(
                [
                    f"## {issue.issue_id} {issue.case_id} {related.title}",
                    "",
                    f"- 严重级别：`{issue.severity}`",
                    f"- 规则分：`{related.score_total}/10`",
                    f"- 系统质量分：`{round(related.system_quality_score or 0.0, 2)}/10`",
                    "",
                    "**失分原因**",
                    "",
                ]
            )
            for reason in issue.reasons:
                lines.append(f"- {reason}")
            lines.extend(["", "**优化建议**", ""])
            for suggestion in issue.suggestions:
                lines.append(f"- {suggestion}")
            lines.extend(
                [
                    "",
                    "**回复摘录**",
                    "",
                    "```text",
                    related.reply.strip() or "(空回复)",
                    "```",
                    "",
                    "**证据**",
                    "",
                    "```json",
                    json.dumps(issue.evidence, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)


def main() -> None:
    runner = KnowledgeSummaryRunner()
    runner.run()
    print(f"Report: {REPORT_PATH}")
    print(f"Issues: {ISSUES_PATH}")
    print(
        "Summary:",
        f"PASS {sum(1 for item in runner.results if item.status == 'PASS')}",
        f"FAIL {sum(1 for item in runner.results if item.status == 'FAIL')}",
        f"issues {len(runner.issues)}",
    )


if __name__ == "__main__":
    main()
