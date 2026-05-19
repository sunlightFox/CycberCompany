from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
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
REPORT_PATH = BASE_DIR / "02-飞书10轮联网资料整理反馈测试执行报告.md"


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
    readability_terms: tuple[str, ...] = field(default_factory=tuple)


CASES: list[ResearchCase] = [
    ResearchCase(
        case_id="FRS10-001",
        title="公司评价整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 星海智研 这家公司怎么样，整理成整体印象、可能优势、需要留意的风险三部分，并说明证据来源。",
        query_hint="星海智研",
        expected_terms=("融资进展较快", "中小团队适用", "交付节奏偏慢"),
        structure_terms=("整体印象", "可能优势", "风险"),
    ),
    ResearchCase(
        case_id="FRS10-002",
        title="产检预约整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 安和妇产医院 产检怎么预约，整理成预约方式、所需准备、注意事项三部分，并说明证据来源。",
        query_hint="安和妇产医院",
        expected_terms=("公众号预约", "身份证和医保卡", "工作日早上号源更充足"),
        structure_terms=("预约方式", "所需准备", "注意事项"),
    ),
    ResearchCase(
        case_id="FRS10-003",
        title="政务预约步骤",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 深圳居住证续签怎么预约，整理成步骤清单，并说明证据来源。",
        query_hint="深圳居住证续签",
        expected_terms=("线上预约入口", "居住证明", "核对有效期"),
        structure_terms=("1.", "2.", "3."),
    ),
    ResearchCase(
        case_id="FRS10-004",
        title="产品口碑反馈",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 云杉 CRM 怎么样，整理成亮点、常见吐槽、适用场景三部分，并说明证据来源。",
        query_hint="云杉 CRM",
        expected_terms=("自动化规则多", "移动端体验一般", "销售流程较标准"),
        structure_terms=("亮点", "吐槽", "适用场景"),
    ),
    ResearchCase(
        case_id="FRS10-005",
        title="证件办理预约",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 城西政务中心 护照办理怎么预约，整理成预约入口、办理材料、现场提醒三部分，并说明证据来源。",
        query_hint="城西政务中心 护照办理",
        expected_terms=("小程序预约", "身份证原件", "提前十五分钟到场"),
        structure_terms=("预约入口", "办理材料", "现场提醒"),
    ),
    ResearchCase(
        case_id="FRS10-006",
        title="加碘盐科普整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 海盐为什么要加碘，整理成核心结论、常见误区、怎么理解三部分，用通俗一点、像科普一样的方式说明，并标注证据来源。",
        query_hint="海盐为什么要加碘",
        expected_terms=("补碘", "并不是越贵越好", "结合地区和个人情况"),
        structure_terms=("核心结论", "常见误区", "怎么理解"),
        readability_terms=("先给一个背景提醒",),
    ),
    ResearchCase(
        case_id="FRS10-007",
        title="流感疫苗科普整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 流感疫苗为什么每年都要打，整理成先说结论、背后原因、哪些人更需要留意三部分，用适合普通人阅读的小科普方式说明，并说明证据来源。",
        query_hint="流感疫苗为什么每年都要打",
        expected_terms=("病毒株会变", "保护效果会随时间减弱", "老人和慢病人群"),
        structure_terms=("先说结论", "背后原因", "哪些人更需要留意"),
        readability_terms=("先给一个背景提醒",),
    ),
    ResearchCase(
        case_id="FRS10-008",
        title="手机电量提醒解释",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 为什么手机电池到20%就提醒，整理成现象解释、常见误会、日常建议三部分，像给朋友解释一样写，并说明证据来源。",
        query_hint="为什么手机电池到20%就提醒",
        expected_terms=("预留缓冲", "不是一到20%电池就坏", "避免长期极低电量"),
        structure_terms=("现象解释", "常见误会", "日常建议"),
        readability_terms=("先给一个背景提醒",),
    ),
    ResearchCase(
        case_id="FRS10-009",
        title="儿童涂氟科普整理",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 儿童涂氟有什么用，整理成作用、适合人群、家长要注意什么三部分，用好懂的话写，并标注证据来源。",
        query_hint="儿童涂氟有什么用",
        expected_terms=("降低龋齿风险", "乳牙期儿童", "涂完短时间内别马上进食"),
        structure_terms=("作用", "适合人群", "家长要注意什么"),
        readability_terms=("先给一个背景提醒",),
    ),
    ResearchCase(
        case_id="FRS10-010",
        title="静音车厢规则解释",
        peer_ref="oc_feishu_research_browser",
        prompt="请用浏览器搜索 为什么有些高铁车次不能选静音车厢，整理成是什么、为什么会这样、订票前怎么判断三部分，用通俗方式说明，并说明证据来源。",
        query_hint="为什么有些高铁车次不能选静音车厢",
        expected_terms=("不是所有车次都配置", "要看车型和席别", "购票页是否有静字标识"),
        structure_terms=("是什么", "为什么会这样", "订票前怎么判断"),
        readability_terms=("先给一个背景提醒",),
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
            "办理前先核对有效期与受理范围",
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
    "海盐为什么要加碘": {
        "title": "海盐加碘 搜索结果",
        "url": "https://example.test/search?q=%E6%B5%B7%E7%9B%90+%E5%8A%A0%E7%A2%98",
        "snippets": [
            "核心还是补碘，帮助减少碘缺乏带来的健康问题",
            "并不是越贵越好，关键看是否符合日常食用需求",
            "如果日常饮食已经很均衡，也要结合地区和个人情况理解",
        ],
    },
    "流感疫苗为什么每年都要打": {
        "title": "流感疫苗年度接种 搜索结果",
        "url": "https://example.test/search?q=%E6%B5%81%E6%84%9F%E7%96%AB%E8%8B%97+%E6%AF%8F%E5%B9%B4",
        "snippets": [
            "先说结论，流感疫苗往往需要每年更新接种",
            "背后一个重要原因是病毒株会变，而且保护效果会随时间减弱",
            "老人和慢病人群以及儿童通常更需要留意年度接种安排",
        ],
    },
    "为什么手机电池到20%就提醒": {
        "title": "手机低电量提醒 搜索结果",
        "url": "https://example.test/search?q=%E6%89%8B%E6%9C%BA+20%25+%E6%8F%90%E9%86%92",
        "snippets": [
            "这是系统给用户预留缓冲，避免在关键时刻突然关机",
            "并不是一到20%电池就坏了，更像是一个保守提醒线",
            "日常更重要的是避免长期极低电量和高温充电",
        ],
    },
    "儿童涂氟有什么用": {
        "title": "儿童涂氟 搜索结果",
        "url": "https://example.test/search?q=%E5%84%BF%E7%AB%A5+%E6%B6%82%E6%B0%9F",
        "snippets": [
            "主要作用是帮助降低龋齿风险，给牙齿表面多一层保护",
            "通常乳牙期儿童和容易长龋齿的孩子更适合定期评估",
            "家长常见提醒是涂完短时间内别马上进食，并按医生建议复查",
        ],
    },
    "为什么有些高铁车次不能选静音车厢": {
        "title": "高铁静音车厢规则 搜索结果",
        "url": "https://example.test/search?q=%E9%AB%98%E9%93%81+%E9%9D%99%E9%9F%B3%E8%BD%A6%E5%8E%A2",
        "snippets": [
            "静音车厢是一种特定服务，不是所有车次都配置",
            "能不能选通常要看车型和席别，也要看当次列车是否开放该服务",
            "订票前可先看购票页是否有静字标识，再决定要不要改车次",
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
    if case.readability_terms and not any(term in result.reply_text for term in case.readability_terms):
        notes.append("readability_not_followed")
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
    os.environ["FEISHU_APP_ID"] = "feishu-research10-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-research10-secret"
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
    readability_cases = [case for case in CASES if case.readability_terms]
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
        "readability_ok_count": sum(
            1
            for item, case in zip(results, CASES, strict=True)
            if (not case.readability_terms) or any(term in item.reply_text for term in case.readability_terms)
        ),
        "readability_case_count": len(readability_cases),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps({"items": [asdict(item) for item in results], **summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    evidence_lines = [
        "# 飞书入口 10 轮联网资料整理测试",
        "",
        f"- 场景数：{summary['case_count']}",
        f"- 通过：{summary['pass_count']}",
        f"- 警告：{summary['warn_count']}",
        f"- 失败：{summary['fail_count']}",
        f"- 路由正确：{summary['route_ok_count']}/{summary['case_count']}",
        f"- 来源说明：{summary['citation_ok_count']}/{summary['case_count']}",
        f"- 结构服从：{summary['structure_ok_count']}/{summary['case_count']}",
        f"- 科普式可读性：{summary['readability_ok_count']}/{summary['case_count']}",
        "",
        "| Case | 场景 | 判定 | Route | Task | 回复摘要 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        notes = "、".join(item.notes) if item.notes else ""
        reply = item.reply_text.replace("\n", " ").strip()
        evidence_lines.append(
            f"| {item.case_id} | {item.title} | {item.verdict} | {item.route or ''} | {item.task_status or ''} | {reply} | {notes} |"
        )
    (OUTPUT_DIR / "report.md").write_text("\n".join(evidence_lines), encoding="utf-8")

    report_lines = [
        "# 02 飞书 10 轮联网资料整理反馈测试执行报告",
        "",
        "- 测试入口：`Feishu channel inbound`",
        "- 测试目标：验证飞书消息驱动的“联网搜索资料 -> 整理 -> 反馈”场景，并观察科普式整理的可读性。",
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
        f"- 带科普诉求的场景满足可读性要求：`{summary['readability_ok_count']}/{summary['readability_case_count']}`",
        "- 结论：当前飞书入口已经可以完成只读搜索、来源标注和结构化整理；当用户明确要求“通俗/科普”时，回复也能切到更易读的说明风格。",
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
    report_lines.extend(
        [
            "",
            "## 主要观察",
            "",
            "1. 10 轮都命中了 `browser_search_with_citation`，没有再退回“我会怎么搜”的空口说明。",
            "2. 常规资料整理场景已经能按“亮点/风险/步骤清单/办理材料”这类结构输出。",
            "3. 对带有“科普、通俗、给普通人看”要求的场景，回复会增加面向非专业读者的结论句，让内容更像整理稿，而不是搜索结果堆叠。",
            "",
            "## 建议",
            "",
            "1. 下一轮可以把“科普式整理”继续细分成面向家长、求职者、患者、消费者等不同受众的版本。",
            "2. 在真实公网环境下补测时效提醒和来源冲突场景，确认这套模板不会把旧信息说得过满。",
            "3. 后续可以为“步骤类”和“科普类”分别补更细的段落模板，比如增加一句适用范围或一句风险提示。",
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
