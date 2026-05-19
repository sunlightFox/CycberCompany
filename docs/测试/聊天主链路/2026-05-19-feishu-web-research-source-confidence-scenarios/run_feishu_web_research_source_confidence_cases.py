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
REPORT_PATH = BASE_DIR / "02-飞书联网资料来源优先级与可信度测试执行报告.md"


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
    prompt: str
    expected_terms: tuple[str, ...]
    expected_confidence: str
    expected_source_note: str


CASES: list[ResearchCase] = [
    ResearchCase(
        case_id="FSC-001",
        title="政务入口优先",
        prompt="请用浏览器搜索 某市人才补贴申请入口，整理成入口、材料、办理提醒三部分，并说明证据来源。",
        expected_terms=("申请入口已经统一迁移到政务服务网", "现场办理时需携带身份证原件"),
        expected_confidence="较高，可优先参考当前结果",
        expected_source_note="偏官方口径",
    ),
    ResearchCase(
        case_id="FSC-002",
        title="学校公告优先",
        prompt="请用浏览器搜索 某校研究生报名要求，整理成要求、补充说明、注意事项三部分，并说明证据来源。",
        expected_terms=("报名系统以学校公告为准", "学院补充通知"),
        expected_confidence="较高，可优先参考当前结果",
        expected_source_note="偏官方口径",
    ),
    ResearchCase(
        case_id="FSC-003",
        title="论坛经验降级",
        prompt="请用浏览器搜索 某医院挂号经验，整理成现有说法、风险点、怎么核对三部分，并说明证据来源。",
        expected_terms=("楼主说可以现场加号", "也有人说被要求先预约"),
        expected_confidence="中等偏谨慎，适合先当线索再交叉核对",
        expected_source_note="整理页或社区口径",
    ),
    ResearchCase(
        case_id="FSC-004",
        title="普通资讯站中等",
        prompt="请用浏览器搜索 某产品价格趋势，整理成看到的情况、可参考点、下单前确认三部分，并说明证据来源。",
        expected_terms=("近期价格波动不大", "促销节点会短时下探"),
        expected_confidence="中等，可作为初步整理参考",
        expected_source_note="可信度：中等，可作为初步整理参考",
    ),
]


SEARCH_FIXTURES: dict[str, dict[str, Any]] = {
    "某市人才补贴申请入口": {
        "title": "某市政务服务网 公告",
        "url": "https://service.example.gov.cn/notice",
        "snippets": [
            "申请入口已经统一迁移到政务服务网",
            "现场办理时需携带身份证原件",
            "工作时间以窗口公告为准",
        ],
    },
    "某校研究生报名要求": {
        "title": "某校研究生院 官网通知",
        "url": "https://yz.example.edu.cn/notice",
        "snippets": [
            "报名系统以学校公告为准",
            "也要同步查看学院补充通知",
            "报名时间节点以最新通知页为准",
        ],
    },
    "某医院挂号经验": {
        "title": "论坛经验帖",
        "url": "https://forum.example.com/thread/123",
        "snippets": [
            "楼主说可以现场加号",
            "也有人说被要求先预约",
            "更多像个人经验整理",
        ],
    },
    "某产品价格趋势": {
        "title": "资讯站行情总结",
        "url": "https://news.example.com/price-summary",
        "snippets": [
            "近期价格波动不大",
            "促销节点会短时下探",
            "下单前还是建议再看一次当天价格",
        ],
    },
}


def _match_fixture(query: str) -> dict[str, Any]:
    for key, fixture in SEARCH_FIXTURES.items():
        if key in query:
            return fixture
    raise AssertionError(f"no fixture for query: {query}")


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


def _check_case(result: Any, case: ResearchCase) -> list[str]:
    notes = BASE20._base_notes(result)
    for term in case.expected_terms:
        if term not in result.reply_text:
            notes.append(f"content_missing:{term}")
            break
    if case.expected_confidence not in result.reply_text:
        notes.append(f"confidence_missing:{case.expected_confidence}")
    if case.expected_source_note not in result.reply_text:
        notes.append(f"source_note_missing:{case.expected_source_note}")
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
    os.environ["FEISHU_APP_ID"] = "feishu-research-sc-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-research-sc-secret"
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
                    peer_ref="oc_feishu_research_browser",
                    prompt=case.prompt,
                )
                results.append(BASE20._finalize(turn, _check_case(turn, case)))
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
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# 02 飞书联网资料来源优先级与可信度测试执行报告",
                "",
                f"- 总轮数：`{summary['case_count']}`",
                f"- 通过：`{summary['pass_count']}`",
                f"- 警告：`{summary['warn_count']}`",
                f"- 失败：`{summary['fail_count']}`",
            ]
        ),
        encoding="utf-8",
    )


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
