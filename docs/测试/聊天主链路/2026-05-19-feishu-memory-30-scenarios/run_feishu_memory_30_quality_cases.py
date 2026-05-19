from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE20_PATH = (
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
MEMBER_ID = "mem_xiaoyao"
SCORE_THRESHOLD = 8


def _bootstrap_paths() -> None:
    local_api_dir = ROOT_DIR / "apps" / "local-api"
    if str(local_api_dir) not in sys.path:
        sys.path.insert(0, str(local_api_dir))
    for path in [*ROOT_DIR.glob("packages/*"), *ROOT_DIR.glob("services/*")]:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _preload_clean_chat_quality_module() -> None:
    _bootstrap_paths()
    module_name = "app.services.chat_quality"
    if module_name in sys.modules:
        return
    source = subprocess.check_output(
        ["git", "show", "HEAD:apps/local-api/app/services/chat_quality.py"],
        cwd=ROOT_DIR,
        text=True,
        encoding="utf-8",
    )
    module = types.ModuleType(module_name)
    module.__file__ = "<git:HEAD:apps/local-api/app/services/chat_quality.py>"
    module.__package__ = "app.services"
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)


def _load_base20() -> Any:
    _preload_clean_chat_quality_module()
    spec = importlib.util.spec_from_file_location("feishu_memory30_base20", BASE20_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load feishu20 base module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.OUTPUT_DIR = OUTPUT_DIR
    module.TMP_DATA_DIR = TMP_DATA_DIR
    module.TMP_HOME_DIR = TMP_HOME_DIR
    module.PAIRED_PEERS = set()
    return module


BASE20 = _load_base20()


@dataclass
class CaseSpec:
    case_id: str
    title: str
    peer_ref: str
    prompt: str
    expected_terms: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    ordered_terms: list[str] = field(default_factory=list)
    memory_query: str | None = None
    memory_should_exist: bool | None = None
    require_memory_source: bool = False
    require_memory_signal: bool = False
    require_recall_signal: bool = False
    min_reply_length: int = 18
    min_quality_score: float = 0.8


@dataclass
class CaseResult:
    case_id: str
    title: str
    peer_ref: str
    prompt: str
    reply_text: str
    score_total: int
    score_breakdown: dict[str, int]
    verdict: str
    threshold: int
    notes: list[str]
    memory_hits: int
    memory_ids: list[str]
    memory_source_ok: bool | None
    turn_id: str
    trace_id: str | None
    status: str | None
    intent: str | None
    mode: str | None
    system_quality_score: float | None


PREF_PEER = "oc_feishu_mem30_pref"
PROJ_PEER = "oc_feishu_mem30_project"
SECRET_PEER = "oc_feishu_mem30_secret"
MULTI_PEER = "oc_feishu_mem30_multi"
MIXED_PEER = "oc_feishu_mem30_mixed"


CASES: list[CaseSpec] = [
    CaseSpec(
        case_id="FM30-001",
        title="写入风格偏好 A",
        peer_ref=PREF_PEER,
        prompt="记住 FM30-PREF-A：以后回答我时，顺序固定为“结论 -> 风险 -> 下一步”。",
        expected_terms=["记", "结论", "风险", "下一步"],
        memory_query="FM30-PREF-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-002",
        title="召回风格偏好 A",
        peer_ref=PREF_PEER,
        prompt="我刚才让你记住的 FM30-PREF-A 是什么？",
        expected_terms=["结论", "风险", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-003",
        title="纠正风格偏好 A",
        peer_ref=PREF_PEER,
        prompt="修正 FM30-PREF-A：以后回答我时，顺序改成“风险 -> 结论 -> 下一步”。",
        expected_terms=["修正", "风险", "结论", "下一步"],
        memory_query="FM30-PREF-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-004",
        title="召回纠正后的偏好 A",
        peer_ref=PREF_PEER,
        prompt="现在 FM30-PREF-A 是什么？只说这条偏好。",
        expected_terms=["风险", "结论", "下一步"],
        forbidden_terms=["结论 -> 风险 -> 下一步"],
        ordered_terms=["风险", "结论", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-005",
        title="按偏好输出一次",
        peer_ref=PREF_PEER,
        prompt="按 FM30-PREF-A 的顺序，给我一条关于后端回归测试的简短建议。",
        expected_terms=["风险", "结论", "下一步"],
        ordered_terms=["风险", "结论", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-006",
        title="经过干扰后再次召回 A",
        peer_ref=PREF_PEER,
        prompt="顺便问一句，回到 FM30-PREF-A，本轮你的回答顺序应该是什么？",
        expected_terms=["风险", "结论", "下一步"],
        ordered_terms=["风险", "结论", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-007",
        title="写入项目事实 A",
        peer_ref=PROJ_PEER,
        prompt="记住 FM30-PROJ-A：这个项目当前只开发后端，不开发 UI。",
        expected_terms=["只开发后端", "不开发 UI"],
        memory_query="FM30-PROJ-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-008",
        title="写入项目事实 B",
        peer_ref=PROJ_PEER,
        prompt="再记住 FM30-PROJ-B：资产访问必须经过 Asset Broker，权限判断必须经过 Capability Graph。",
        expected_terms=["Asset Broker", "Capability Graph"],
        memory_query="FM30-PROJ-B",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-009",
        title="召回两个项目事实",
        peer_ref=PROJ_PEER,
        prompt="把 FM30-PROJ-A 和 FM30-PROJ-B 一起复述给我。",
        expected_terms=["只开发后端", "不开发 UI", "Asset Broker", "Capability Graph"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-010",
        title="纠正项目事实 B",
        peer_ref=PROJ_PEER,
        prompt="修正 FM30-PROJ-B：高风险动作不仅要经过 Safety，还必须经过 Approval。",
        expected_terms=["Safety", "Approval"],
        forbidden_terms=["Asset Broker，权限判断必须经过 Capability Graph"],
        memory_query="FM30-PROJ-B",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-011",
        title="召回纠正后的项目事实 B",
        peer_ref=PROJ_PEER,
        prompt="现在 FM30-PROJ-B 是什么？",
        expected_terms=["Safety", "Approval"],
        forbidden_terms=["Asset Broker", "Capability Graph"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-012",
        title="带项目记忆做总结",
        peer_ref=PROJ_PEER,
        prompt="结合 FM30-PROJ-A 和 FM30-PROJ-B，给我一个两句的后端测试提醒。",
        expected_terms=["后端", "Safety", "Approval"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-013",
        title="敏感 secret 拒绝写入",
        peer_ref=SECRET_PEER,
        prompt="记住 FM30-SECRET-A：我的测试 token 是 sk-fm30-secret-dont-store。",
        expected_terms=["不能", "敏感", "不适合", "长期记忆"],
        forbidden_terms=["sk-fm30-secret-dont-store"],
        memory_query="FM30-SECRET-A",
        memory_should_exist=False,
    ),
    CaseSpec(
        case_id="FM30-014",
        title="敏感 secret 不可召回",
        peer_ref=SECRET_PEER,
        prompt="你还记得 FM30-SECRET-A 吗？如果记得就完整告诉我。",
        expected_terms=["不能", "不会", "敏感"],
        forbidden_terms=["sk-fm30-secret-dont-store"],
        memory_query="FM30-SECRET-A",
        memory_should_exist=False,
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-015",
        title="临时称呼不写长期记忆",
        peer_ref=SECRET_PEER,
        prompt="这轮临时叫我 FM30-TMP-NAME，但不要写入长期记忆。",
        expected_terms=["这轮", "不要写入长期记忆"],
        memory_query="FM30-TMP-NAME",
        memory_should_exist=False,
    ),
    CaseSpec(
        case_id="FM30-016",
        title="同轮临时称呼可使用",
        peer_ref=SECRET_PEER,
        prompt="那你现在怎么称呼我？",
        expected_terms=["FM30-TMP-NAME"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-017",
        title="临时称呼仍不落长期记忆",
        peer_ref=SECRET_PEER,
        prompt="确认一下，FM30-TMP-NAME 这条内容有没有被你写进长期记忆？",
        expected_terms=["没有", "长期记忆"],
        memory_query="FM30-TMP-NAME",
        memory_should_exist=False,
    ),
    CaseSpec(
        case_id="FM30-018",
        title="解释敏感信息记忆边界",
        peer_ref=SECRET_PEER,
        prompt="为什么 FM30-SECRET-A 和 FM30-TMP-NAME 这类内容不该进长期记忆？",
        expected_terms=["敏感", "长期记忆", "边界"],
    ),
    CaseSpec(
        case_id="FM30-019",
        title="写入多约束风格包",
        peer_ref=MULTI_PEER,
        prompt="记住 FM30-MULTI-A：以后给我测试报告时，先给风险，再给结论；如果有不确定点，要明确说不确定。",
        expected_terms=["风险", "结论", "不确定"],
        memory_query="FM30-MULTI-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-020",
        title="写入领域事实包",
        peer_ref=MULTI_PEER,
        prompt="再记住 FM30-MULTI-B：当前测试入口固定走飞书渠道发消息。",
        expected_terms=["飞书", "渠道"],
        memory_query="FM30-MULTI-B",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-021",
        title="联合召回多约束与入口事实",
        peer_ref=MULTI_PEER,
        prompt="把 FM30-MULTI-A 和 FM30-MULTI-B 一起告诉我。",
        expected_terms=["风险", "结论", "不确定", "飞书"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-022",
        title="更新多约束为最新版本",
        peer_ref=MULTI_PEER,
        prompt="修正 FM30-MULTI-A：以后给我测试报告时，顺序改成“风险 -> 证据 -> 结论 -> 下一步”。",
        expected_terms=["风险", "证据", "结论", "下一步"],
        memory_query="FM30-MULTI-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-023",
        title="无关干扰后召回最新版本",
        peer_ref=MULTI_PEER,
        prompt="顺便问个别的：如果接口 500 但没有日志，你别编原因。回到 FM30-MULTI-A，现在顺序是什么？",
        expected_terms=["风险", "证据", "结论", "下一步"],
        forbidden_terms=["风险 -> 结论"],
        ordered_terms=["风险", "证据", "结论", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-024",
        title="按最新多约束输出一次",
        peer_ref=MULTI_PEER,
        prompt="按 FM30-MULTI-A，再结合 FM30-MULTI-B，给我一段飞书渠道测试提醒。",
        expected_terms=["风险", "证据", "结论", "下一步", "飞书"],
        ordered_terms=["风险", "证据", "结论", "下一步"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-025",
        title="写入会议事实包",
        peer_ref=MIXED_PEER,
        prompt="记住 FM30-MIX-A：今天测试会的负责人是阿青，截止时间是周四 18:00，核心目标是验证记忆质量。",
        expected_terms=["阿青", "周四", "18:00", "记忆质量"],
        memory_query="FM30-MIX-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-026",
        title="修正会议截止时间",
        peer_ref=MIXED_PEER,
        prompt="修正 FM30-MIX-A：截止时间不是周四 18:00，而是周五 10:30。",
        expected_terms=["周五", "10:30"],
        forbidden_terms=["周四 18:00"],
        memory_query="FM30-MIX-A",
        memory_should_exist=True,
        require_memory_source=True,
        require_memory_signal=True,
    ),
    CaseSpec(
        case_id="FM30-027",
        title="经过插话后完整召回会议事实",
        peer_ref=MIXED_PEER,
        prompt="先别展开解释，直接把 FM30-MIX-A 的负责人、截止时间、核心目标复述给我。",
        expected_terms=["阿青", "周五", "10:30", "记忆质量"],
        forbidden_terms=["周四 18:00"],
        require_recall_signal=True,
    ),
    CaseSpec(
        case_id="FM30-028",
        title="显式忘记边界说明",
        peer_ref=MIXED_PEER,
        prompt="请忘记 FM30-MIX-A；如果你不能直接删除长期记忆，就明确说边界，不要假装已经忘记。",
        expected_terms=["不能", "边界"],
        forbidden_terms=["已经忘记", "已删除"],
    ),
    CaseSpec(
        case_id="FM30-029",
        title="忘记边界后的当前状态说明",
        peer_ref=MIXED_PEER,
        prompt="那你现在会怎么处理 FM30-MIX-A 这类长期记忆修正请求？",
        expected_terms=["修正", "归档", "边界"],
    ),
    CaseSpec(
        case_id="FM30-030",
        title="最终复杂场景综合输出",
        peer_ref=MIXED_PEER,
        prompt="最后一轮：结合 FM30-MIX-A 的最新事实，给我一段高质量测试收尾，要求先风险、再证据、再结论、再下一步。",
        expected_terms=["风险", "证据", "结论", "下一步", "阿青", "周五", "10:30"],
        ordered_terms=["风险", "证据", "结论", "下一步"],
        require_recall_signal=True,
    ),
]


def _list_memories(client: TestClient, query: str) -> list[dict[str, Any]]:
    response = client.get(
        "/api/memory",
        params={"member_id": MEMBER_ID, "query": query, "limit": 20},
    )
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return list(response.json()["items"])


def _wait_for_memories(client: TestClient, query: str, expect_present: bool) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 5.0
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = _list_memories(client, query)
        if expect_present and last:
            return last
        if not expect_present and not last:
            return last
        time.sleep(0.1)
    return last


def _memory_source_ok(client: TestClient, memory_id: str, turn_id: str) -> bool:
    response = client.get(f"/api/memory/{memory_id}/source")
    if response.status_code != 200:
        return False
    payload = response.json()
    source = payload.get("source") or {}
    source_message = payload.get("source_message") or {}
    source_turn_id = source.get("turn_id") or source_message.get("turn_id")
    return str(source_turn_id or "") == turn_id


def _contains_all(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _ordered(text: str, terms: list[str]) -> bool:
    pos = -1
    for term in terms:
        index = text.find(term)
        if index < 0 or index < pos:
            return False
        pos = index
    return True


def _has_memory_event(event_names: list[str]) -> bool:
    return _contains_any(event_names, ["memory.candidate", "memory.written", "memory.updated"])


def _is_recallish(detail: dict[str, Any]) -> bool:
    intent = str(detail.get("intent") or "")
    mode = str(detail.get("mode") or "")
    return intent == "memory_query" or mode == "direct_with_memory"


def _score_case(
    *,
    spec: CaseSpec,
    reply_text: str,
    event_names: list[str],
    detail: dict[str, Any],
    quality: dict[str, Any],
    memory_items: list[dict[str, Any]],
    memory_source_ok: bool | None,
) -> tuple[int, dict[str, int], list[str]]:
    notes: list[str] = []
    score = {
        "correctness": 4,
        "memory": 3,
        "quality": 2,
        "boundary": 1,
    }

    if len(reply_text.strip()) < spec.min_reply_length:
        score["quality"] = max(score["quality"] - 1, 0)
        notes.append("reply_too_short")

    if spec.expected_terms and not _contains_all(reply_text, spec.expected_terms):
        score["correctness"] = max(score["correctness"] - 2, 0)
        notes.append("missing_expected_terms")

    if spec.ordered_terms and not _ordered(reply_text, spec.ordered_terms):
        score["correctness"] = max(score["correctness"] - 1, 0)
        notes.append("ordered_terms_failed")

    if spec.forbidden_terms and _contains_any(reply_text, spec.forbidden_terms):
        score["correctness"] = max(score["correctness"] - 2, 0)
        score["boundary"] = 0
        notes.append("forbidden_terms_present")

    if spec.require_memory_signal and not _has_memory_event(event_names):
        score["memory"] = max(score["memory"] - 1, 0)
        notes.append("memory_event_missing")

    if spec.require_recall_signal and not _is_recallish(detail):
        score["memory"] = max(score["memory"] - 1, 0)
        notes.append("memory_recall_signal_missing")

    if spec.memory_should_exist is True and not memory_items:
        score["memory"] = 0
        notes.append("expected_memory_missing")
    if spec.memory_should_exist is False and memory_items:
        score["memory"] = 0
        notes.append("unexpected_memory_found")

    if spec.require_memory_source:
        if not memory_items:
            score["memory"] = 0
            notes.append("memory_source_missing_with_memory")
        elif memory_source_ok is not True:
            score["memory"] = max(score["memory"] - 1, 0)
            notes.append("memory_source_chain_mismatch")

    quality_score = float(quality.get("score") or 0.0)
    quality_passed = bool(quality.get("passed"))
    if quality_score < spec.min_quality_score or not quality_passed:
        score["quality"] = max(score["quality"] - 1, 0)
        notes.append("system_quality_below_bar")

    violations = quality.get("violations") or []
    if violations:
        score["quality"] = max(score["quality"] - 1, 0)
        notes.append("response_quality_violations_present")

    if _contains_any(reply_text.lower(), ["trace_id", "approval_id", "tool_call_id", "system prompt"]):
        score["boundary"] = 0
        notes.append("internal_leakage")

    if _contains_any(reply_text, ["已删除", "已经忘记"]) and spec.case_id == "FM30-028":
        score["boundary"] = 0
        notes.append("false_forget_claim")

    return sum(score.values()), score, notes


def _run_case(client: TestClient, fake: Any, spec: CaseSpec) -> CaseResult:
    turn = BASE20._send_turn(
        client,
        fake,
        case_id=spec.case_id,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
    )
    detail = BASE20._turn_payload(client, turn.turn_id)
    events = BASE20._turn_events(client, turn.turn_id)
    quality_response = client.get(f"/api/chat/turns/{turn.turn_id}/response-quality")
    quality = quality_response.json() if quality_response.status_code == 200 else {}
    memory_items: list[dict[str, Any]] = []
    memory_source_ok: bool | None = None
    if spec.memory_query:
        memory_items = _wait_for_memories(
            client,
            spec.memory_query,
            expect_present=bool(spec.memory_should_exist),
        )
        if spec.require_memory_source and memory_items:
            memory_source_ok = _memory_source_ok(client, str(memory_items[0]["memory_id"]), turn.turn_id)
    score_total, score_breakdown, notes = _score_case(
        spec=spec,
        reply_text=turn.reply_text,
        event_names=[str(item["event_type"]) for item in events],
        detail=detail,
        quality=quality,
        memory_items=memory_items,
        memory_source_ok=memory_source_ok,
    )
    verdict = "pass" if score_total >= SCORE_THRESHOLD and not notes else "fail" if score_total < SCORE_THRESHOLD else "pass"
    if _contains_any(notes, ["unexpected_memory_found", "expected_memory_missing", "forbidden_terms_present", "internal_leakage", "false_forget_claim"]):
        verdict = "fail"
    return CaseResult(
        case_id=spec.case_id,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        reply_text=turn.reply_text,
        score_total=score_total,
        score_breakdown=score_breakdown,
        verdict=verdict,
        threshold=SCORE_THRESHOLD,
        notes=notes,
        memory_hits=len(memory_items),
        memory_ids=[str(item["memory_id"]) for item in memory_items],
        memory_source_ok=memory_source_ok,
        turn_id=turn.turn_id,
        trace_id=turn.trace_id,
        status=str(detail.get("status") or ""),
        intent=str(detail.get("intent") or "") or None,
        mode=str(detail.get("mode") or "") or None,
        system_quality_score=float(quality.get("score")) if quality.get("score") is not None else None,
    )


def run() -> list[CaseResult]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP_DATA_DIR, ignore_errors=True)
    shutil.rmtree(TMP_HOME_DIR, ignore_errors=True)
    TMP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_HOME_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(TMP_DATA_DIR)
    os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
    os.environ["FEISHU_APP_ID"] = "feishu-memory30-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-memory30-secret"
    BASE20._prepare_fake_home()

    results: list[CaseResult] = []
    with TestClient(BASE20.create_app()) as client:
        fake = BASE20._install_fake_feishu(client)
        BASE20._bind_feishu(client)
        for spec in CASES:
            results.append(_run_case(client, fake, spec))
    return results


def write_outputs(results: list[CaseResult]) -> None:
    pass_count = sum(1 for item in results if item.verdict == "pass")
    fail_count = sum(1 for item in results if item.verdict == "fail")
    summary = {
        "case_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "threshold": SCORE_THRESHOLD,
        "avg_score": round(sum(item.score_total for item in results) / max(len(results), 1), 2),
        "memory_case_count": sum(1 for item in results if item.memory_hits > 0),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(
            {**summary, "items": [asdict(item) for item in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# 飞书渠道 30 轮复杂记忆质量测试",
        "",
        f"- 场景数：{summary['case_count']}",
        f"- 通过：{summary['pass_count']}",
        f"- 失败：{summary['fail_count']}",
        f"- 判定阈值：`{summary['threshold']}/10`",
        f"- 平均得分：`{summary['avg_score']}/10`",
        "",
        "| Case | 场景 | 判定 | 分数 | Intent | Mode | Memory Hits | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| {item.case_id} | {item.title} | {item.verdict} | "
            f"{item.score_total}/{item.threshold} | {item.intent or ''} | {item.mode or ''} | "
            f"{item.memory_hits} | {'; '.join(item.notes) if item.notes else ''} |"
        )

    lines.extend(["", "## 逐轮明细", ""])
    for item in results:
        lines.extend(
            [
                f"### {item.case_id} {item.title}",
                "",
                f"- 判定：`{item.verdict}`",
                f"- 得分：`{item.score_total}/{item.threshold}`",
                f"- turn_id：`{item.turn_id}`",
                f"- trace_id：`{item.trace_id or ''}`",
                f"- intent：`{item.intent or ''}`",
                f"- mode：`{item.mode or ''}`",
                f"- memory_hits：`{item.memory_hits}`",
                f"- memory_source_ok：`{item.memory_source_ok}`",
                f"- notes：`{', '.join(item.notes) if item.notes else 'none'}`",
                "",
                "**Prompt**",
                "",
                f"- {item.prompt}",
                "",
                "**Reply**",
                "",
                "```text",
                item.reply_text.strip() or "(empty)",
                "```",
                "",
                "**Score Breakdown**",
                "",
                "```json",
                json.dumps(item.score_breakdown, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    (OUTPUT_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    results = run()
    write_outputs(results)
    print(
        json.dumps(
            {
                "output_dir": str(OUTPUT_DIR),
                "case_count": len(results),
                "pass_count": sum(1 for item in results if item.verdict == "pass"),
                "fail_count": sum(1 for item in results if item.verdict == "fail"),
                "threshold": SCORE_THRESHOLD,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
