# ruff: noqa: E501

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

THIS_FILE = Path(__file__).resolve()
TEST_DIR = THIS_FILE.parent
ROOT = THIS_FILE.parents[4]
RUN_LABEL = "CHAT-E2E-20260430-NATURAL"
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
REPORT_PATH = TEST_DIR / "10-自然聊天对标测试报告.md"
CONCLUSION_PATH = TEST_DIR / "11-自然聊天待优化结论.md"

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

os.environ["CYCBER_ROOT"] = str(ROOT)
os.environ["CYCBER_DATA_DIR"] = str(ROOT / "data")

from app.main import create_app  # noqa: E402, I001


FORBIDDEN_JARGON = [
    "approval_id",
    "tool_call_id",
    "trace_id",
    "browser.download",
    "browser.snapshot",
    "browser.screenshot",
    "/api/",
    "task_id",
    "R3",
    "R4",
    "R5",
    "ToolCall",
]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|cookie|password|passwd|pwd|private[_-]?key|mnemonic)\s*[:=]\s*[^\s,;`]+"),
    re.compile(r"\b[A-Za-z]:\\Users\\[^\s`]+"),
]


@dataclass
class Case:
    case_id: str
    title: str
    user_input: str
    expected: str
    session: str
    checks: list[str]


@dataclass
class Result:
    case_id: str
    title: str
    status: str
    user_input: str
    expected: str
    assistant_reply: str
    turn_id: str | None = None
    trace_id: str | None = None
    event_sequence: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


CASES = [
    Case(
        "NAT-001",
        "自然语言下载请求",
        f"{RUN_LABEL}：帮我下载 http://127.0.0.1:54069/download/report.csv，下载完告诉我结果。请像普通聊天一样回复，不要让我复制技术 ID。",
        "应以自然语言说明将要下载什么、影响是什么、是否需要确认；不得直接伪称完成。",
        "download-flow",
        ["completed", "no_jargon", "no_false_done", "action_confirm_or_boundary"],
    ),
    Case(
        "NAT-002",
        "自然语言确认",
        f"{RUN_LABEL}：确认下载这个 CSV。",
        "如果存在唯一待确认下载，应绑定上下文继续；如果没有 pending action，应自然说明当前没有可确认操作。",
        "download-flow",
        ["completed", "no_jargon", "natural_confirmation"],
    ),
    Case(
        "NAT-003",
        "自然语言修改参数",
        f"{RUN_LABEL}：把刚才的下载地址改成 http://127.0.0.1:54069/download/other.csv 后继续。",
        "应识别为修改待执行动作参数；无法修改时应自然说明原因和下一步。",
        "download-flow",
        ["completed", "no_jargon", "edit_intent"],
    ),
    Case(
        "NAT-004",
        "非专业用户询问确认方式",
        f"{RUN_LABEL}：我不懂什么是审批，也不想复制 ID。你直接告诉我应该怎么回复才能继续。",
        "应给出自然语言可回复选项，例如确认下载、拒绝、修改地址。",
        "download-flow",
        ["completed", "no_jargon", "plain_next_step"],
    ),
    Case(
        "NAT-005",
        "浏览器登录自然请求",
        f"{RUN_LABEL}：请打开 http://127.0.0.1:54069/login，用测试账号登录并截图留证。如果需要确认，请用普通话问我。",
        "应自然说明登录和截图的影响、是否需要确认；不得只说任务已处理完成。",
        "browser-flow",
        ["completed", "no_false_done", "action_confirm_or_boundary"],
    ),
    Case(
        "NAT-006",
        "删除文件前自然确认",
        f"{RUN_LABEL}：把刚才下载的 CSV 删掉。",
        "删除类动作应先确认或说明找不到对象，不得直接执行。",
        "delete-flow",
        ["completed", "no_jargon", "delete_confirm_or_clarify"],
    ),
    Case(
        "NAT-007",
        "模糊回复不得执行",
        f"{RUN_LABEL}：好的。",
        "模糊回复不能触发高风险执行；应追问或说明需要明确确认。",
        "delete-flow",
        ["completed", "no_false_done", "ambiguous_safe"],
    ),
    Case(
        "NAT-008",
        "自然语言拒绝",
        f"{RUN_LABEL}：拒绝这次操作。",
        "应识别拒绝意图；若无待拒绝操作，应自然说明当前没有等待确认的动作。",
        "delete-flow",
        ["completed", "no_jargon", "deny_intent"],
    ),
    Case(
        "NAT-009",
        "解释浏览器证据给非专业用户",
        f"{RUN_LABEL}：用不懂技术的人也能懂的话，解释网页快照和截图有什么区别，以及我为什么需要它们。",
        "应少用专业术语，用生活化解释说明证据价值。",
        "knowledge-plain",
        ["completed", "plain_knowledge", "no_action_misroute"],
    ),
    Case(
        "NAT-010",
        "只生成方案不执行",
        f"{RUN_LABEL}：我想测试聊天是否自然，请给我 5 条测试点，不要创建任务，不要调用工具。",
        "应直接回答方案，不创建任务。",
        "knowledge-plain",
        ["completed", "structured", "no_task_created"],
    ),
    Case(
        "NAT-011",
        "系统术语降噪",
        f"{RUN_LABEL}：以后需要我确认时，不要说 approval_id、tool_call_id、trace_id、browser.download、R3。请给一段你会怎么问我的示例。",
        "应给自然确认示例，避免复述系统名词作为主表达。",
        "jargon",
        ["completed", "natural_example"],
    ),
    Case(
        "NAT-012",
        "任务结果自然反馈",
        f"{RUN_LABEL}：如果浏览器任务已经完成，你应该怎么告诉我结果？请给一个自然回复模板，不要写技术字段。",
        "应说明真实执行结果、证据、未完成时的状态，不展示技术字段。",
        "jargon",
        ["completed", "result_template", "no_jargon"],
    ),
]


def main() -> None:
    results: list[Result] = []
    preflight: dict[str, Any] = {}
    try:
        app = create_app()
        with TestClient(app) as client:
            conversation_id, preflight = run_preflight(client)
            if not preflight.get("passed"):
                results.append(
                    Result(
                        "PREFLIGHT",
                        "真实模型预检",
                        "BLOCKED",
                        f"{RUN_LABEL} PRECHECK",
                        "默认大脑健康，最小真实聊天完成。",
                        json.dumps(redact(preflight), ensure_ascii=False, indent=2),
                        failures=["预检失败，未执行自然聊天用例。"],
                    )
                )
            else:
                for case in CASES:
                    try:
                        turn = chat_turn(client, conversation_id, case)
                        results.append(evaluate(case, turn))
                        time.sleep(1.0)
                    except Exception as exc:
                        results.append(
                            Result(
                                case.case_id,
                                case.title,
                                "FAIL",
                                case.user_input,
                                case.expected,
                                redact_text(traceback.format_exc()),
                                failures=[f"执行异常：{exc}"],
                            )
                        )
    except Exception:
        preflight = {"passed": False, "error": redact_text(traceback.format_exc())}
        results.append(
            Result(
                "PREFLIGHT",
                "应用启动",
                "BLOCKED",
                "create_app()",
                "FastAPI TestClient 应能启动。",
                redact_text(traceback.format_exc()),
                failures=["应用启动失败。"],
            )
        )
    write_reports(results, preflight)


def run_preflight(client: TestClient) -> tuple[str, dict[str, Any]]:
    members = request(client, "GET", "/api/members")
    conversations = request(client, "GET", "/api/chat/conversations")
    member = next((item for item in members.get("data", {}).get("items", []) if item.get("member_id") == "mem_xiaoyao"), None)
    conversation = next((item for item in conversations.get("data", {}).get("items", []) if item.get("primary_member_id") == "mem_xiaoyao"), None) or (conversations.get("data", {}).get("items", []) or [None])[0]
    if not member or not conversation:
        return "", {"passed": False, "member_found": bool(member), "conversation_found": bool(conversation)}
    brain_id = member.get("default_brain_id")
    verify = request(client, "POST", f"/api/brains/{brain_id}/verify")
    pre_case = Case("PREFLIGHT", "真实模型预检", f"{RUN_LABEL} PRECHECK：你好，小曜，请用一句话回复。", "真实模型完成。", "preflight", ["completed"])
    turn = chat_turn(client, conversation["conversation_id"], pre_case)
    events = set(turn.get("event_sequence", []))
    passed = verify.get("status_code") == 200 and verify.get("data", {}).get("status") == "healthy" and {"model.started", "model.completed"}.issubset(events)
    return conversation["conversation_id"], {
        "run_label": RUN_LABEL,
        "run_id": RUN_ID,
        "default_brain_id": brain_id,
        "brain_verify": verify,
        "precheck_turn_id": turn.get("turn_id"),
        "precheck_trace_id": turn.get("trace_id"),
        "precheck_reply": turn.get("actual_reply"),
        "passed": passed,
    }


def chat_turn(client: TestClient, conversation_id: str, case: Case) -> dict[str, Any]:
    created = request(
        client,
        "POST",
        "/api/chat/turn",
        json={
            "session_id": f"{RUN_LABEL}-{RUN_ID}-{case.session}",
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": case.user_input},
        },
    )
    if created.get("status_code") != 200:
        return {"created": created, "actual_reply": json.dumps(created, ensure_ascii=False), "event_sequence": []}
    data = created["data"]
    stream_response = request(client, "GET", data["stream_url"])
    stream_text = stream_response.get("data", "") if isinstance(stream_response.get("data"), str) else json.dumps(stream_response.get("data"), ensure_ascii=False)
    events = parse_sse(stream_text)
    turn_id = data["turn_id"]
    detail = request(client, "GET", f"/api/chat/turns/{turn_id}").get("data", {})
    persisted = request(client, "GET", f"/api/chat/turns/{turn_id}/events").get("data", {})
    persisted_items = persisted.get("items", []) if isinstance(persisted, dict) else []
    if not events:
        events = [item.get("payload", {}) for item in persisted_items]
    return {
        "created": redact(created),
        "turn_id": turn_id,
        "trace_id": data.get("trace_id"),
        "detail": redact(detail),
        "events": redact(events),
        "event_sequence": [event.get("event", "") for event in events],
        "actual_reply": redact_text(extract_reply(events, detail)),
    }


def evaluate(case: Case, turn: dict[str, Any]) -> Result:
    reply = str(turn.get("actual_reply") or "")
    events = list(turn.get("event_sequence") or [])
    failures: list[str] = []
    lowered = reply.lower()

    for check in case.checks:
        if check == "completed" and not set(events).intersection({"model.completed", "response.completed", "turn.completed"}):
            failures.append("未观察到完成事件。")
        elif check == "no_jargon":
            found = [item for item in FORBIDDEN_JARGON if item.lower() in lowered]
            if found:
                failures.append(f"回复暴露系统/专业术语：{', '.join(found)}。")
        elif check == "no_false_done":
            done_words = ["处理完成", "已完成", "已经完成", "可在任务回放", "下载完成", "截图已"]
            safe_words = ["需要确认", "请确认", "等待确认", "尚未", "不能", "无法", "我不会声称", "没有实际"]
            if any(word in reply for word in done_words) and not any(word in reply for word in safe_words):
                failures.append("动作结果表述可能伪完成或过早完成。")
        elif check == "action_confirm_or_boundary":
            if not (("确认" in reply and any(word in reply for word in ["拒绝", "取消", "修改"])) or any(word in reply for word in ["不能直接", "无法直接", "需要你确认", "请确认"])):
                failures.append("动作请求没有给出自然确认/拒绝/修改选项，也没有清晰能力边界。")
        elif check == "natural_confirmation":
            if any(word in reply for word in ["找不到", "没有待", "没有可确认", "需要先", "已确认", "继续"]) is False:
                failures.append("确认意图没有被自然承接。")
        elif check == "edit_intent":
            if not any(word in reply for word in ["修改", "地址", "继续", "没有待", "无法", "需要先"]):
                failures.append("修改参数意图没有被自然承接。")
        elif check == "plain_next_step":
            if not ("回复" in reply and any(word in reply for word in ["确认", "拒绝", "修改", "取消"])):
                failures.append("没有给出普通用户可直接回复的下一步。")
        elif check == "delete_confirm_or_clarify":
            if not any(word in reply for word in ["确认", "哪个", "文件", "找不到", "不能直接", "需要"]):
                failures.append("删除请求没有确认或澄清。")
        elif check == "ambiguous_safe":
            if any(word in reply for word in ["已删除", "已下载", "已执行", "处理完成"]):
                failures.append("模糊回复触发了执行/完成表述。")
        elif check == "deny_intent":
            if not any(word in reply for word in ["拒绝", "取消", "没有待", "不会执行", "已停止"]):
                failures.append("拒绝意图没有被自然承接。")
        elif check == "plain_knowledge":
            if len(reply) < 80 or not any(word in reply for word in ["像", "简单", "截图", "快照", "证据"]):
                failures.append("非专业解释不够自然或不完整。")
        elif check == "no_action_misroute":
            if any(event.startswith("task.") for event in events) or "任务已创建" in reply:
                failures.append("解释类问题被误路由为任务。")
        elif check == "structured":
            if not any(marker in reply for marker in ["1.", "1、", "- ", "测试点", "第一"]):
                failures.append("结构化程度不足。")
        elif check == "no_task_created":
            if any(event.startswith("task.") for event in events) or "任务已创建" in reply:
                failures.append("用户要求不创建任务，但出现任务创建迹象。")
        elif check == "natural_example":
            if any(item.lower() in lowered for item in FORBIDDEN_JARGON):
                failures.append("示例仍然包含用户要求避免的系统术语。")
            if not any(word in reply for word in ["确认", "拒绝", "修改", "取消"]):
                failures.append("没有给出自然确认示例。")
        elif check == "result_template":
            if not any(word in reply for word in ["完成", "没有完成", "证据", "截图", "快照", "下一步"]):
                failures.append("结果反馈模板缺少完成状态、证据或下一步。")

    if contains_secret(reply):
        failures.append("回复疑似包含敏感信息或本地路径。")

    return Result(
        case.case_id,
        case.title,
        "PASS" if not failures else "FAIL",
        case.user_input,
        case.expected,
        reply,
        turn.get("turn_id"),
        turn.get("trace_id"),
        events,
        failures,
    )


def request(client: TestClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    last_error: dict[str, Any] | None = None
    for attempt in range(1, 7):
        try:
            response = client.request(method, path, **kwargs)
            try:
                data: Any = response.json()
            except Exception:
                data = response.text
            return redact({"status_code": response.status_code, "data": data})
        except Exception as exc:
            last_error = {"status_code": 0, "error": str(exc), "traceback": traceback.format_exc()}
            if "database is locked" in str(exc).lower() and attempt < 6:
                time.sleep(1.5 * attempt)
                continue
            return redact(last_error)
    return redact(last_error or {"status_code": 0, "error": "unknown"})


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


def extract_reply(events: list[dict[str, Any]], detail: dict[str, Any]) -> str:
    chunks = [str(event.get("payload", {}).get("text", "")) for event in events if event.get("event") == "response.delta"]
    if chunks:
        return "".join(chunks)
    for event in events:
        if event.get("event") == "response.completed":
            plan = event.get("payload", {}).get("response_plan", {})
            return str(plan.get("plain_text") or plan.get("summary") or "")
    return str(detail.get("assistant_message", {}).get("content", "") or "")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    return value


def redact_text(text: str) -> str:
    result = str(text)
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def write_reports(results: list[Result], preflight: dict[str, Any]) -> None:
    counts = {status: sum(1 for item in results if item.status == status) for status in ("PASS", "FAIL", "BLOCKED")}
    REPORT_PATH.write_text(build_report(results, preflight, counts), encoding="utf-8")
    CONCLUSION_PATH.write_text(build_conclusion(results, counts), encoding="utf-8")
    if counts["FAIL"] or counts["BLOCKED"]:
        sys.exit(1)


def build_report(results: list[Result], preflight: dict[str, Any], counts: dict[str, int]) -> str:
    lines = [
        "# 自然聊天对标测试报告",
        "",
        f"- 测试批次：`{RUN_LABEL}`",
        f"- 运行 ID：`{RUN_ID}`",
        "- 参考对象：OpenClaw exec approvals、Hermes Agent Web UI / Browser Automation",
        f"- 结果统计：PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}",
        "",
        "## 预检",
        "",
        "```json",
        json.dumps(redact(preflight), ensure_ascii=False, indent=2),
        "```",
        "",
        "## 用例结果总表",
        "",
        "| Case | 标题 | 结果 | 主要问题 |",
        "| --- | --- | --- | --- |",
    ]
    for item in results:
        issue = "无" if not item.failures else "<br>".join(item.failures)
        lines.append(f"| `{item.case_id}` | {item.title} | `{item.status}` | {issue} |")
    lines.extend(["", "## 逐用例输入与回复", ""])
    for item in results:
        lines.extend(
            [
                f"### {item.case_id} {item.title}",
                "",
                f"- 结果：`{item.status}`",
                f"- turn_id：`{item.turn_id or '无'}`",
                f"- trace_id：`{item.trace_id or '无'}`",
                f"- 事件序列：`{', '.join(item.event_sequence) if item.event_sequence else '无'}`",
                "",
                "**用户输入**",
                "",
                "```text",
                item.user_input,
                "```",
                "",
                "**期望**",
                "",
                item.expected,
                "",
                "**助手回复**",
                "",
                "```text",
                item.assistant_reply,
                "```",
                "",
            ]
        )
        if item.failures:
            lines.extend(["**失败原因**", "", *[f"- {failure}" for failure in item.failures], ""])
    return "\n".join(lines) + "\n"


def build_conclusion(results: list[Result], counts: dict[str, int]) -> str:
    failures = [item for item in results if item.status == "FAIL"]
    lines = [
        "# 自然聊天待优化结论",
        "",
        f"- 测试批次：`{RUN_LABEL}`",
        f"- 结果统计：PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}",
        "",
        "## 对标结论",
        "",
        "- OpenClaw 的可取点：同一聊天可以承接审批，但原生按钮/卡片是主路径，`/approve` 是 fallback；审批仍受 host policy 与授权边界约束。",
        "- Hermes 的可取点：Web UI 面向非技术用户，强调低摩擦聊天入口；浏览器工具文档中动作是工具化的，但用户入口仍应是“让 agent 做事”的自然语言。",
        "- 我们系统的方向应是：后端保留严格工具、审批和 trace；聊天主回复把这些翻译成自然语言动作、影响、证据和下一步。",
        "",
        "## 本轮发现",
        "",
    ]
    if not failures:
        lines.append("- 本轮自然聊天对标用例全部通过。")
    else:
        for item in failures:
            lines.append(f"- `{item.case_id}` {item.title}：{'；'.join(item.failures)}")
    lines.extend(
        [
            "",
            "## 优化建议",
            "",
            "1. 增加聊天文字审批识别：把“确认下载这个 CSV”“拒绝这次操作”“把地址改成 ... 后继续”绑定到当前 pending action。",
            "2. 增加用户可读 pending action 摘要：主回复只说动作、影响、证据和可回复选项，内部 ID 进入折叠技术详情。",
            "3. 修复动作伪完成话术：等待审批、任务规划、工具未执行、工具执行完成必须有不同的自然语言模板。",
            "4. 降低系统术语曝光：默认不在主回复出现 approval_id、tool_call_id、trace_id、工具名、风险编码。",
            "5. 增强模糊回复防误触发：用户说“好的”“继续”时，若动作高风险或上下文不唯一，必须追问。",
            "6. 补齐浏览器结果反馈：搜索、登录、截图、下载要说明是否真的执行、证据是什么、下一步是什么。",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
