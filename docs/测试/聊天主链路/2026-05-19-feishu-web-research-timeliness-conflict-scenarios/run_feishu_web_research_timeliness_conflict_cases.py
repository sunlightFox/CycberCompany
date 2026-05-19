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
REPORT_PATH = BASE_DIR / "02-飞书联网资料时效与来源冲突测试执行报告.md"


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
    required_notes: tuple[str, ...]
    expected_confidence: str | None = None


CASES: list[ResearchCase] = [
    ResearchCase(
        case_id="FRT-001",
        title="最新门诊安排",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 最新门诊安排，整理成主要变化、需要注意、出发前确认三部分，并说明证据来源。",
        query_hint="最新门诊安排",
        expected_terms=("本周门诊安排有调整", "部分科室周末停诊"),
        required_notes=("时效提醒", "最新的官方页面或公告"),
        expected_confidence="中等",
    ),
    ResearchCase(
        case_id="FRT-002",
        title="近期签证要求",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 近期日本签证材料要求，整理成主要变化、常见材料、提交前确认三部分，并说明证据来源。",
        query_hint="近期日本签证材料要求",
        expected_terms=("近期材料口径有更新", "在职证明和资产材料", "送签前再核对"),
        required_notes=("时效提醒",),
        expected_confidence="中等",
    ),
    ResearchCase(
        case_id="FRT-003",
        title="预约要求口径冲突",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 某地居住证续签预约要求，整理成现有说法、可能差异、怎么处理三部分，并说明证据来源。",
        query_hint="某地居住证续签预约要求",
        expected_terms=("有的资料写现场取号即可", "也有资料写必须先线上预约"),
        required_notes=("说法不完全一致", "以权威来源为准"),
        expected_confidence="中等偏谨慎",
    ),
    ResearchCase(
        case_id="FRT-004",
        title="招生简章版本差异",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 某校研究生招生简章报名要求，整理成当前看到的要求、可能差异、建议做法三部分，并说明证据来源。",
        query_hint="某校研究生招生简章报名要求",
        expected_terms=("不同来源给出的专业目录版本不同", "学院补充通知"),
        required_notes=("说法不完全一致",),
        expected_confidence="中等偏谨慎",
    ),
    ResearchCase(
        case_id="FRT-005",
        title="最新演出入场规则",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 今天演唱会入场规则，整理成当前规则、可能变化、出发前确认三部分，并说明证据来源。",
        query_hint="今天演唱会入场规则",
        expected_terms=("今天场次的入场时间可能调整", "证件和票务信息"),
        required_notes=("时效提醒", "最新的官方页面或公告"),
        expected_confidence="中等",
    ),
    ResearchCase(
        case_id="FRT-006",
        title="最新政策且来源冲突",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 最新落户政策办理条件，整理成当前公开说法、可能分歧、办理前确认三部分，并说明证据来源。",
        query_hint="最新落户政策办理条件",
        expected_terms=("有的资料按旧门槛说明", "也有资料引用新通知"),
        required_notes=("时效提醒", "说法不完全一致", "以权威来源为准"),
        expected_confidence="中等偏谨慎",
    ),
]


SEARCH_FIXTURES: dict[str, dict[str, Any]] = {
    "最新门诊安排": {
        "title": "最新门诊安排 搜索结果",
        "url": "https://example.test/search?q=latest+clinic+schedule",
        "snippets": [
            "本周门诊安排有调整，部分专家出诊时间顺延",
            "部分科室周末停诊，节假日前后变化更明显",
            "建议出发前再次确认公告和挂号页",
        ],
    },
    "近期日本签证材料要求": {
        "title": "近期日本签证材料要求 搜索结果",
        "url": "https://example.test/search?q=recent+japan+visa+docs",
        "snippets": [
            "近期材料口径有更新，不同领区提示略有差异",
            "常见材料仍包括在职证明和资产材料",
            "送签前再核对代办机构和领区最新说明",
        ],
    },
    "某地居住证续签预约要求": {
        "title": "居住证续签预约 搜索结果",
        "url": "https://example.test/search?q=residence+permit+renewal",
        "snippets": [
            "有的资料写现场取号即可",
            "也有资料写必须先线上预约",
            "不同来源提醒以当地窗口最新通知为准",
        ],
    },
    "某校研究生招生简章报名要求": {
        "title": "研究生招生简章 搜索结果",
        "url": "https://yz.example.edu.cn/notice",
        "snippets": [
            "不同来源给出的专业目录版本不同",
            "也有资料提示要同时看学院补充通知",
            "最终报名口径通常以学校最新公告为准",
        ],
    },
    "今天演唱会入场规则": {
        "title": "今天演唱会入场规则 搜索结果",
        "url": "https://example.test/search?q=today+concert+entry",
        "snippets": [
            "今天场次的入场时间可能调整，最好提前查看通知",
            "入场通常需要证件和票务信息同时匹配",
            "部分场馆会临时调整安检节奏",
        ],
    },
    "最新落户政策办理条件": {
        "title": "最新落户政策 搜索结果",
        "url": "https://example.test/search?q=latest+settlement+policy",
        "snippets": [
            "有的资料按旧门槛说明，需要一定社保年限",
            "也有资料引用新通知，条件有所放宽",
            "不同来源提醒最终以当地政务最新公告为准",
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
        query = str((getattr(request, "args", {}) or {}).get("query") or "")
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
    for term in case.expected_terms:
        if term not in result.reply_text:
            notes.append(f"content_missing:{term}")
            break
    for term in case.required_notes:
        if term not in result.reply_text:
            notes.append(f"note_missing:{term}")
            break
    if case.expected_confidence and case.expected_confidence not in result.reply_text:
        notes.append(f"confidence_missing:{case.expected_confidence}")
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
    os.environ["FEISHU_APP_ID"] = "feishu-research-tc-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-research-tc-secret"
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
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps({"items": [asdict(item) for item in results], **summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_lines = [
        "# 02 飞书联网资料时效与来源冲突测试执行报告",
        "",
        f"- 总轮数：`{summary['case_count']}`",
        f"- 通过：`{summary['pass_count']}`",
        f"- 警告：`{summary['warn_count']}`",
        f"- 失败：`{summary['fail_count']}`",
        "",
        "| Case ID | 标题 | 判定 | 备注 |",
        "| --- | --- | --- | --- |",
    ]
    for item in results:
        report_lines.append(
            f"| `{item.case_id}` | {item.title} | `{item.verdict.upper()}` | {'<br>'.join(item.notes) if item.notes else '无'} |"
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
