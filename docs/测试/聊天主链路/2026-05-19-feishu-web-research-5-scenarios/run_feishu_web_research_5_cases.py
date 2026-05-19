from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_20_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-18-feishu-20-scenarios"
    / "run_feishu_20_quality_cases.py"
)
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "evidence"
TMP_DATA_DIR = OUTPUT_DIR / ".tmp-data"
TMP_HOME_DIR = OUTPUT_DIR / ".tmp-home"
REPORT_PATH = BASE_DIR / "02-飞书5轮联网资料整理反馈测试执行报告.md"


def _load_base20() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_research_base20", BASE_20_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load feishu 20 scenario base module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.OUTPUT_DIR = OUTPUT_DIR
    module.TMP_DATA_DIR = TMP_DATA_DIR
    module.TMP_HOME_DIR = TMP_HOME_DIR
    module.PAIRED_PEERS = set()
    return module


BASE20 = _load_base20()


@dataclass(frozen=True)
class ResearchCase:
    case_id: str
    title: str
    peer_ref: str
    prompt: str
    query_hint: str
    expected_terms: tuple[str, ...]
    structure_terms: tuple[str, ...]


CASES: list[ResearchCase] = [
    ResearchCase(
        case_id="FRS-001",
        title="公司评价整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 星海智研 这家公司怎么样，整理成整体印象、可能优势、需要留意的风险三部分，并说明证据来源。",
        query_hint="星海智研",
        expected_terms=("交付节奏偏慢", "中小团队适用", "融资进展较快"),
        structure_terms=("整体印象", "可能优势", "风险"),
    ),
    ResearchCase(
        case_id="FRS-002",
        title="产检预约整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 安和妇产医院 产检怎么预约，整理成预约方式、所需准备、注意事项三部分，并说明证据来源。",
        query_hint="安和妇产医院",
        expected_terms=("公众号预约", "身份证和医保卡", "工作日早上号源更充足"),
        structure_terms=("预约方式", "所需准备", "注意事项"),
    ),
    ResearchCase(
        case_id="FRS-003",
        title="政务预约步骤",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 深圳居住证续签怎么预约，整理成步骤清单，并说明证据来源。",
        query_hint="深圳居住证续签",
        expected_terms=("线上预约入口", "居住证明", "核对有效期"),
        structure_terms=("1.", "2.", "3."),
    ),
    ResearchCase(
        case_id="FRS-004",
        title="产品口碑反馈",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 云杉 CRM 怎么样，整理成亮点、常见吐槽、适用场景三部分，并说明证据来源。",
        query_hint="云杉 CRM",
        expected_terms=("自动化规则多", "移动端体验一般", "销售流程较标准"),
        structure_terms=("亮点", "吐槽", "适用场景"),
    ),
    ResearchCase(
        case_id="FRS-005",
        title="证件办理预约",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 城西政务中心 护照办理怎么预约，整理成预约入口、办理材料、现场提醒三部分，并说明证据来源。",
        query_hint="城西政务中心 护照办理",
        expected_terms=("小程序预约", "身份证原件", "提前十五分钟到场"),
        structure_terms=("预约入口", "办理材料", "现场提醒"),
    ),
]


SEARCH_FIXTURES: dict[str, dict[str, Any]] = {
    "星海智研": {
        "title": "星海智研 搜索结果",
        "url": "https://example.test/search?q=%E6%98%9F%E6%B5%B7%E6%99%BA%E7%A0%94",
        "snippets": [
            "融资进展较快，近两年扩张明显",
            "中小团队适用，实施周期相对短",
            "交付节奏偏慢，售后响应两极分化",
        ],
    },
    "安和妇产医院": {
        "title": "安和妇产医院 产检预约搜索结果",
        "url": "https://example.test/search?q=%E5%AE%89%E5%92%8C%E5%A6%87%E4%BA%A7%E5%8C%BB%E9%99%A2+%E4%BA%A7%E6%A3%80",
        "snippets": [
            "公众号预约为主，也可电话咨询号源",
            "首次建档通常需身份证和医保卡",
            "工作日早上号源更充足，建议提前到院",
        ],
    },
    "深圳居住证续签": {
        "title": "深圳居住证续签预约搜索结果",
        "url": "https://example.test/search?q=%E6%B7%B1%E5%9C%B3%E5%B1%85%E4%BD%8F%E8%AF%81%E7%BB%AD%E7%AD%BE",
        "snippets": [
            "线上预约入口在本地政务服务平台",
            "常见材料包括身份证和居住证明",
            "办理前先核对证件有效期与受理范围",
        ],
    },
    "云杉 CRM": {
        "title": "云杉 CRM 搜索结果",
        "url": "https://example.test/search?q=%E4%BA%91%E6%9D%89+CRM",
        "snippets": [
            "自动化规则多，销售漏斗可视化清楚",
            "移动端体验一般，复杂审批不够顺手",
            "销售流程较标准的团队更容易落地",
        ],
    },
    "城西政务中心 护照办理": {
        "title": "城西政务中心 护照办理预约搜索结果",
        "url": "https://example.test/search?q=%E5%9F%8E%E8%A5%BF%E6%94%BF%E5%8A%A1%E4%B8%AD%E5%BF%83+%E6%8A%A4%E7%85%A7",
        "snippets": [
            "小程序预约是主要入口，也支持窗口咨询",
            "办理时通常需身份证原件和照片回执",
            "建议提前十五分钟到场并核对取号时间",
        ],
    },
}


def _match_fixture(query: str) -> dict[str, Any]:
    for key, fixture in SEARCH_FIXTURES.items():
        if key in query:
            return fixture
    return {
        "title": "通用搜索结果",
        "url": "https://example.test/search",
        "snippets": ["找到了一些公开资料线索", "需要进一步整理", "当前只做只读搜索"],
    }


@contextlib.contextmanager
def _patched_browser_search(client: TestClient) -> Iterator[None]:
    registry = client.app.state.registry
    original_execute = registry.tool_runtime.execute

    async def fake_execute(request: Any, trace_id: str | None = None) -> Any:
        if request.tool_name != "browser.search":
            return await original_execute(request, trace_id=trace_id)
        args = getattr(request, "args", {}) or {}
        query = str(args.get("query") or "")
        fixture = _match_fixture(query)
        snippet_html = "".join(f"<li>{item}</li>" for item in fixture["snippets"])
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "title": fixture["title"],
                    "url": fixture["url"],
                    "http_status": 200,
                    "browser_evidence_id": f"bev_{abs(hash(query)) % 100000}",
                    "content_preview": f"<html><body>{snippet_html}</body></html>",
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": f"call_{abs(hash(query)) % 100000}",
                        "risk_level": type("Risk", (), {"value": "R2"})(),
                    },
                )(),
            },
        )()

    registry.tool_runtime.execute = fake_execute
    try:
        yield
    finally:
        registry.tool_runtime.execute = original_execute


def _route(result: Any) -> str | None:
    semantics = dict(result.structured_payload.get("route_semantics") or {})
    route = semantics.get("route")
    return str(route) if route else None


def _task_status(result: Any) -> str | None:
    payload = dict(result.structured_payload.get("task_status") or {})
    status = payload.get("status")
    return str(status) if status else None


def _check_case(result: Any, case: ResearchCase) -> list[str]:
    notes = BASE20._base_notes(result)
    if _route(result) != "browser_search_with_citation":
        notes.append("wrong_route")
    if _task_status(result) != "not_created":
        notes.append("unexpected_task_state")
    if "证据来源" not in result.reply_text:
        notes.append("citation_missing")
    missing_terms = [term for term in case.expected_terms if term not in result.reply_text]
    if missing_terms:
        notes.append(f"content_missing:{missing_terms[0]}")
    if not any(term in result.reply_text for term in case.structure_terms):
        notes.append("structure_not_followed")
    return notes


def run() -> list[Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP_DATA_DIR, ignore_errors=True)
    shutil.rmtree(TMP_HOME_DIR, ignore_errors=True)
    TMP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_HOME_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(TMP_DATA_DIR)
    os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
    os.environ["FEISHU_APP_ID"] = "feishu-research5-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-research5-secret"
    BASE20._prepare_fake_home()

    results: list[Any] = []
    with TestClient(BASE20.create_app()) as client:
        fake = BASE20._install_fake_feishu(client)
        BASE20._bind_feishu(client)
        with _patched_browser_search(client):
            for case in CASES:
                turn = BASE20._send_turn(
                    client,
                    fake,
                    case_id=case.case_id,
                    title=case.title,
                    peer_ref=case.peer_ref,
                    prompt=case.prompt,
                )
                notes = _check_case(turn, case)
                results.append(BASE20._finalize(turn, notes))
    return results


def write_outputs(results: list[Any]) -> None:
    summary = {
        "case_count": len(results),
        "pass_count": sum(1 for item in results if item.verdict == "pass"),
        "warn_count": sum(1 for item in results if item.verdict == "warn"),
        "fail_count": sum(1 for item in results if item.verdict == "fail"),
        "route_ok_count": sum(1 for item in results if item.route == "browser_search_with_citation"),
        "citation_ok_count": sum(1 for item in results if "证据来源" in item.reply_text),
        "structure_ok_count": sum(
            1
            for item, case in zip(results, CASES, strict=True)
            if any(term in item.reply_text for term in case.structure_terms)
        ),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(
            {
                **summary,
                "items": [asdict(item) for item in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    evidence_lines = [
        "# 飞书入口 5 轮联网资料整理反馈测试",
        "",
        f"- 场景数：{summary['case_count']}",
        f"- 通过：{summary['pass_count']}",
        f"- 警告：{summary['warn_count']}",
        f"- 失败：{summary['fail_count']}",
        f"- 路由正确：{summary['route_ok_count']}/{summary['case_count']}",
        f"- 来源说明：{summary['citation_ok_count']}/{summary['case_count']}",
        f"- 结构服从：{summary['structure_ok_count']}/{summary['case_count']}",
        "",
        "| Case | 场景 | 判定 | Route | Task | 提示词 | 回复摘要 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        notes = "、".join(item.notes) if item.notes else ""
        reply = item.reply_text.replace("\n", " ").strip()
        evidence_lines.append(
            f"| {item.case_id} | {item.title} | {item.verdict} | {item.route or ''} | {item.task_status or ''} | {item.prompt} | {reply} | {notes} |"
        )
    (OUTPUT_DIR / "report.md").write_text("\n".join(evidence_lines), encoding="utf-8")

    report_lines = [
        "# 02 飞书 5 轮联网资料整理反馈测试执行报告",
        "",
        "- 测试入口：`Feishu channel inbound`",
        "- 测试目标：验证飞书消息驱动的“联网搜索资料 -> 整理 -> 反馈”场景",
        f"- 总轮数：`{summary['case_count']}`",
        f"- 通过：`{summary['pass_count']}`",
        f"- 警告：`{summary['warn_count']}`",
        f"- 失败：`{summary['fail_count']}`",
        "",
        "## 结果摘要",
        "",
        f"- 路由命中 `browser_search_with_citation`：`{summary['route_ok_count']}/{summary['case_count']}`",
        f"- 回复包含证据来源说明：`{summary['citation_ok_count']}/{summary['case_count']}`",
        f"- 回复满足用户指定结构：`{summary['structure_ok_count']}/{summary['case_count']}`",
        (
            "- 结论：当前飞书入口下，这类自然资料型提问还没有稳定命中浏览器搜索执行链路，"
            "更多是退化成“我会怎么搜索和整理”的说明型回答。"
            if summary["route_ok_count"] == 0
            else "- 结论：当前飞书入口已经能走到“只读搜索 + 引用来源”链路，但在“把资料整理成用户指定格式”上仍明显偏弱。"
        ),
        "",
        "## 分轮结果",
        "",
        "| Case ID | 标题 | 判定 | Route | Task | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        notes = "<br>".join(item.notes) if item.notes else "无"
        report_lines.append(
            f"| `{item.case_id}` | {item.title} | `{item.verdict.upper()}` | `{item.route or ''}` | `{item.task_status or ''}` | {notes} |"
        )
    report_lines.extend(["", "## 主要观察", ""])
    if summary["route_ok_count"] == 0:
        report_lines.extend(
            [
                "1. 5/5 都没有命中 `browser_search_with_citation`，说明飞书入口下这类自然资料型提问目前仍偏向被识别成“方法说明/计划说明”。",
                "2. 回复内容是“我会如何检索和整理”的泛化话术，不是实际资料收集后的反馈，因此也没有来源引用和事实片段。",
                "3. 这意味着当前首要缺口不在总结模板，而在意图路由：系统还没把“xxx 公司怎么样 / xxx 怎么预约”稳定识别为应立即执行的只读搜索请求。",
            ]
        )
    elif summary["structure_ok_count"] < summary["case_count"]:
        report_lines.extend(
            [
                "1. 搜索与引用链路是通的。",
                "2. 回复能说明来源，也能带出搜索页里的关键片段。",
                "3. 但回复基本还是搜索结果摘要，没有充分重组为“步骤清单 / 三部分反馈 / 亮点与风险”这类用户想要的交付形状。",
            ]
        )
    else:
        report_lines.append("1. 5 轮都完成了搜索、引用和结构化反馈。")
    report_lines.extend(
        [
            "",
            "## 建议",
            "",
            "1. 对 `browser_search_with_citation` 增加轻量整理模板，让“公司评价 / 预约流程 / 办事指南 / 产品口碑”几类常见资料场景能按用户结构直接输出。",
            "2. 保留现有证据来源拼接逻辑，但在其前面先生成结论块，避免回复像搜索页复述。",
            "3. 后续可把这 5 轮扩成 20 轮，并加入真实公网、失败回退、来源冲突、时效提醒等维度。",
        ]
    )
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")


def main() -> None:
    results = run()
    write_outputs(results)
    print(
        json.dumps(
            {
                "output_dir": str(OUTPUT_DIR),
                "report_path": str(REPORT_PATH),
                "case_count": len(results),
                "pass_count": sum(1 for item in results if item.verdict == "pass"),
                "warn_count": sum(1 for item in results if item.verdict == "warn"),
                "fail_count": sum(1 for item in results if item.verdict == "fail"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
