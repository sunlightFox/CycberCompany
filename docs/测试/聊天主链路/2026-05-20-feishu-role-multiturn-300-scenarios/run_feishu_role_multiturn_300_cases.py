from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, cast

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_50_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-18-feishu-50-scenarios"
    / "run_feishu_50_quality_cases.py"
)
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "evidence"
TMP_DATA_DIR = OUTPUT_DIR / ".tmp-data"
TMP_HOME_DIR = OUTPUT_DIR / ".tmp-home"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书300个角色化多轮复杂场景.md"
REPORT_PATH = BASE_DIR / "02-飞书300个角色化多轮复杂场景测试执行报告.md"


def _load_base50() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_role_multiturn_300_base50", BASE_50_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load feishu 50 scenario base module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.OUTPUT_DIR = OUTPUT_DIR
    module.TMP_DATA_DIR = TMP_DATA_DIR
    module.TMP_HOME_DIR = TMP_HOME_DIR
    module.PAIRED_PEERS = set()
    return module


BASE50 = _load_base50()

SUPPORTIVE_WORDS = ("先稳住", "别急", "没关系", "可以", "我来", "一起", "先休息", "陪你")
BOUNDARY_WORDS = ("不能", "不会", "无法", "不可以", "只给方案", "先不执行", "需要确认", "安全")
NEXT_STEP_WORDS = ("下一步", "先做", "先看", "先补", "先确认", "现在", "今天", "今晚")
SUMMARY_WORDS = ("结论", "风险", "下一步", "建议", "安排", "总结")


@dataclass
class ScenarioTurnSpec:
    prompt: str
    checker: Callable[[Any, TestClient, dict[str, Any]], list[str]] | None = None


@dataclass
class ScenarioSpec:
    case_id: str
    role: str
    theme: str
    title: str
    peer_ref: str
    turns: list[ScenarioTurnSpec]
    recall_markers: list[str]
    final_markers: list[str]
    boundary_turns: set[int] = field(default_factory=set)
    tool_turns: set[int] = field(default_factory=set)


@dataclass
class TurnEval:
    turn_no: int
    prompt: str
    reply_text: str
    turn_id: str
    trace_id: str | None
    status: str
    intent: str | None
    mode: str | None
    route: str | None
    task_status: str | None
    tone_mode: str | None
    anthropomorphic_level: float | None
    system_quality_score: float
    notes: list[str]
    quality_markers: dict[str, Any]
    violations: list[dict[str, Any]]


@dataclass
class ScenarioResult:
    case_id: str
    role: str
    theme: str
    title: str
    peer_ref: str
    verdict: str
    total_score: int
    avg_system_quality_score: float
    score_breakdown: dict[str, int]
    reasons: list[str]
    turn_count: int
    turns: list[TurnEval]


SLOTS = [
    {"name": "安宁", "family": "妈妈", "city": "苏州", "day": "周六", "time": "19:00", "budget": "300", "food": "清淡", "movie": "轻松治愈", "owner": "阿泽"},
    {"name": "一诺", "family": "弟弟", "city": "杭州", "day": "周日", "time": "18:30", "budget": "260", "food": "少辣", "movie": "悬疑", "owner": "小林"},
    {"name": "木木", "family": "外婆", "city": "南京", "day": "周五", "time": "20:00", "budget": "420", "food": "热汤", "movie": "爱情", "owner": "阿青"},
    {"name": "可可", "family": "爸爸", "city": "上海", "day": "周二", "time": "17:40", "budget": "180", "food": "低糖", "movie": "喜剧", "owner": "阿周"},
    {"name": "知夏", "family": "表姐", "city": "北京", "day": "周三", "time": "21:10", "budget": "520", "food": "高蛋白", "movie": "科幻", "owner": "Luna"},
    {"name": "阿澈", "family": "室友", "city": "成都", "day": "周四", "time": "18:10", "budget": "240", "food": "少油", "movie": "纪录片", "owner": "阿凯"},
    {"name": "青禾", "family": "孩子", "city": "深圳", "day": "周六", "time": "16:20", "budget": "360", "food": "不吃葱", "movie": "动画", "owner": "Mia"},
    {"name": "晚舟", "family": "奶奶", "city": "武汉", "day": "周一", "time": "19:30", "budget": "280", "food": "热量低", "movie": "文艺", "owner": "阿瑞"},
    {"name": "星野", "family": "朋友", "city": "西安", "day": "周日", "time": "14:50", "budget": "450", "food": "无咖啡因", "movie": "冒险", "owner": "小远"},
    {"name": "小满", "family": "舅舅", "city": "厦门", "day": "周五", "time": "18:00", "budget": "220", "food": "家常", "movie": "家庭", "owner": "阿森"},
]

PREFERENCES = [
    ("先给结论再列三步", "先给结论"),
    ("先说风险再给建议", "先说风险"),
    ("先给清单后解释", "先给清单"),
    ("只说今晚能做的", "今晚能做"),
    ("一句话先定调", "一句话先定调"),
    ("先给时间表", "先给时间表"),
    ("先给预算", "先给预算"),
    ("先给最省事方案", "最省事方案"),
    ("先给保底方案", "保底方案"),
    ("先给老板版再给执行版", "老板版"),
]


def _contains_any(text: str, terms: list[str] | tuple[str, ...]) -> bool:
    raw = str(text or "")
    return any(term in raw for term in terms)


def _turn_diagnostics(client: TestClient, turn_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    tone = client.get(f"/api/chat/turns/{turn_id}/tone-policy")
    if tone.status_code != 200:
        raise RuntimeError(tone.text)
    quality = client.get(f"/api/chat/turns/{turn_id}/response-quality")
    if quality.status_code != 200:
        raise RuntimeError(quality.text)
    return cast(dict[str, Any], tone.json()), cast(dict[str, Any], quality.json())


def _build_turn_eval(
    client: TestClient,
    turn: Any,
    turn_no: int,
    turn_spec: ScenarioTurnSpec,
    ctx: dict[str, Any],
) -> TurnEval:
    notes = list(BASE50._base_notes(turn))
    if turn_spec.checker is not None:
        notes.extend(turn_spec.checker(turn, client, ctx))
    tone, quality = _turn_diagnostics(client, turn.turn_id)
    violations = cast(list[dict[str, Any]], quality.get("violations") or [])
    markers = cast(dict[str, Any], quality.get("quality_markers") or {})
    return TurnEval(
        turn_no=turn_no,
        prompt=turn_spec.prompt,
        reply_text=turn.reply_text,
        turn_id=turn.turn_id,
        trace_id=turn.trace_id,
        status=turn.status,
        intent=turn.intent,
        mode=turn.mode,
        route=BASE50._route(turn),
        task_status=BASE50._task_status(turn),
        tone_mode=tone.get("tone_mode"),
        anthropomorphic_level=tone.get("anthropomorphic_level"),
        system_quality_score=round(float(quality.get("score") or 0.0) * 10, 2),
        notes=sorted(set(notes)),
        quality_markers=markers,
        violations=violations,
    )


def _score_scenario(spec: ScenarioSpec, turns: list[TurnEval]) -> tuple[dict[str, int], list[str]]:
    reasons: list[str] = []
    all_text = "\n".join(turn.reply_text for turn in turns)
    final_text = turns[-1].reply_text if turns else ""
    follow_text = "\n".join(turn.reply_text for turn in turns[1:])
    avg_quality = sum(turn.system_quality_score for turn in turns) / max(len(turns), 1)

    severe_hit = any(
        note.startswith(("empty_reply", "internal_leak", "template_leak"))
        for turn in turns
        for note in turn.notes
    )
    false_done_hit = any(
        "false_done" in note or "ambiguous_false_completion" in note
        for turn in turns
        for note in turn.notes
    )

    score: dict[str, int] = {
        "连续性与记忆": 0,
        "角色一致性": 0,
        "任务完成质量": 0,
        "边界与诚实": 0,
        "语言自然度": 0,
        "收尾质量": 0,
    }

    recall_hits = sum(1 for marker in spec.recall_markers if marker and marker in follow_text)
    follow_ok = all(turn.status == "completed" and turn.reply_text.strip() for turn in turns[1:])
    if recall_hits >= max(1, len(spec.recall_markers)) or follow_ok:
        score["连续性与记忆"] = 2
    elif recall_hits > 0 or any(turn.reply_text.strip() for turn in turns[1:]):
        score["连续性与记忆"] = 1
        reasons.append("memory_partial")
    else:
        reasons.append("memory_missed")

    role_score = 0
    if spec.role == "life_butler":
        if _contains_any(all_text, ("安排", "清单", "时间", "预算")):
            role_score += 1
        if _contains_any(all_text, NEXT_STEP_WORDS):
            role_score += 1
    elif spec.role == "virtual_partner":
        if _contains_any(all_text, SUPPORTIVE_WORDS):
            role_score += 1
        if _contains_any(all_text, ("陪", "一起", "慢一点", "先休息", "别硬扛")):
            role_score += 1
    else:
        if _contains_any(all_text, ("结论", "风险", "下一步", "负责人", "截止")):
            role_score += 1
        if _contains_any(all_text, ("建议", "同步", "汇报", "证据")):
            role_score += 1
    score["角色一致性"] = role_score
    if role_score < 2:
        reasons.append("role_signal_weak")

    tool_turn_notes = []
    for idx in spec.tool_turns:
        if 1 <= idx <= len(turns):
            tool_turn_notes.extend(turns[idx - 1].notes)
    final_hits = sum(1 for marker in spec.final_markers if marker and marker in final_text)
    if not tool_turn_notes and final_hits >= max(1, len(spec.final_markers) - 1):
        score["任务完成质量"] = 2
    elif final_hits > 0:
        score["任务完成质量"] = 1
        reasons.append("task_completion_partial")
    else:
        reasons.append("task_completion_weak")

    boundary_ok = True
    if severe_hit or false_done_hit:
        boundary_ok = False
    for idx in spec.boundary_turns:
        if 1 <= idx <= len(turns):
            turn = turns[idx - 1]
            turn_text = turn.reply_text
            if not (
                _contains_any(turn_text, BOUNDARY_WORDS)
                or turn.tone_mode == "safety_boundary"
                or bool(turn.quality_markers.get("boundary_honesty"))
            ):
                boundary_ok = False
    if boundary_ok:
        score["边界与诚实"] = 2
    elif not severe_hit:
        score["边界与诚实"] = 1
        reasons.append("boundary_partial")
    else:
        reasons.append("boundary_failed")

    if severe_hit:
        score["语言自然度"] = 0
        reasons.append("severe_quality_issue")
    elif avg_quality >= 8.0 and not any(turn.notes for turn in turns):
        score["语言自然度"] = 2
    elif avg_quality >= 6.5:
        score["语言自然度"] = 1
        reasons.append("naturalness_partial")
    else:
        reasons.append("naturalness_low")

    closeout_signals = 0
    if _contains_any(final_text, SUMMARY_WORDS):
        closeout_signals += 1
    if _contains_any(final_text, NEXT_STEP_WORDS):
        closeout_signals += 1
    if len([line for line in final_text.splitlines() if line.strip()]) <= 5:
        closeout_signals += 1
    if closeout_signals >= 3:
        score["收尾质量"] = 2
    elif closeout_signals == 2:
        score["收尾质量"] = 1
        reasons.append("closeout_partial")
    else:
        reasons.append("closeout_weak")

    return score, sorted(set(reasons))


def _verdict(total_score: int, turns: list[TurnEval]) -> str:
    fatal = any(
        note.startswith(("empty_reply", "internal_leak", "template_leak"))
        for turn in turns
        for note in turn.notes
    )
    if fatal or total_score <= 6:
        return "fail"
    if total_score <= 9:
        return "warn"
    return "pass"


def _scenario_result(spec: ScenarioSpec, turns: list[TurnEval]) -> ScenarioResult:
    breakdown, reasons = _score_scenario(spec, turns)
    total_score = sum(breakdown.values())
    avg_system_quality = round(
        sum(turn.system_quality_score for turn in turns) / max(len(turns), 1),
        2,
    )
    return ScenarioResult(
        case_id=spec.case_id,
        role=spec.role,
        theme=spec.theme,
        title=spec.title,
        peer_ref=spec.peer_ref,
        verdict=_verdict(total_score, turns),
        total_score=total_score,
        avg_system_quality_score=avg_system_quality,
        score_breakdown=breakdown,
        reasons=reasons,
        turn_count=len(turns),
        turns=turns,
    )


def _make_title(role_label: str, theme_label: str, slot: dict[str, str], index: int) -> str:
    return f"{role_label}-{theme_label}-{index:02d}-{slot['name']}"


def _life_butler_cases() -> list[ScenarioSpec]:
    cases: list[ScenarioSpec] = []
    themes = [
        ("daily_plan", "生活排程"),
        ("shopping", "采购决策"),
        ("family_schedule", "家庭协调"),
        ("travel_plan", "短途出行"),
        ("home_organize", "居家整理"),
        ("browser_info", "浏览器查页"),
        ("office_brief", "家庭简报"),
        ("system_plan", "系统整理"),
        ("budget_guard", "预算提醒"),
        ("preference_recall", "偏好延续"),
    ]
    for theme_no, (theme_key, theme_label) in enumerate(themes):
        for idx, slot in enumerate(SLOTS, start=1):
            pref_full, pref_marker = PREFERENCES[idx - 1]
            case_no = len(cases) + 1
            case_id = f"FRM300-{case_no:03d}"
            peer_ref = f"oc_feishu_role300_lb_{theme_key}_{idx:02d}"
            if theme_key == "daily_plan":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当我的生活管家。今天我要在{slot['day']}{slot['time']}前去接{slot['family']}，晚饭预算{slot['budget']}元，回复口径记成“{pref_full}”。先帮我排今晚安排。"
                    ),
                    ScenarioTurnSpec(f"我刚才让你按什么口径回答？如果只能保留一个硬约束，应该保留哪一个？"),
                    ScenarioTurnSpec("好，把这轮安排压成三句话：结论、风险、下一步。"),
                ]
                recall = [pref_marker]
            elif theme_key == "shopping":
                turns = [
                    ScenarioTurnSpec(
                        f"你当我的生活管家，帮我定个今晚买菜方案：两个人吃、偏{slot['food']}、预算上限{slot['budget']}元，按“{pref_full}”来答。"
                    ),
                    ScenarioTurnSpec("我刚才给你的预算上限是多少？如果要砍一项非必要采购，你先砍什么？"),
                    ScenarioTurnSpec("最后给我一个能直接发给家里人的采购结论，保留风险和下一步。"),
                ]
                recall = [slot["budget"]]
            elif theme_key == "family_schedule":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家。我家{slot['day']}{slot['time']}要接{slot['family']}，同时还要顺路买药，回复口径用“{pref_full}”。帮我把先后顺序排一下。"
                    ),
                    ScenarioTurnSpec(f"我家里这轮最不能动的时间点是什么？如果迟到 20 分钟，你先调整哪一步？"),
                    ScenarioTurnSpec("收个尾，给我三句版本：先结论，再风险，再今晚的下一步。"),
                ]
                recall = [slot["time"]]
            elif theme_key == "travel_plan":
                turns = [
                    ScenarioTurnSpec(
                        f"你先做生活管家。这个周末我想去{slot['city']}近郊半天，预算{slot['budget']}元，回复口径“{pref_full}”，帮我出一个轻量行程。"
                    ),
                    ScenarioTurnSpec(f"我刚才给你的城市和预算分别是什么？如果天气不好，你给我一个保底方案。"),
                    ScenarioTurnSpec("最后把方案压成适合在飞书里转发的三句总结。"),
                ]
                recall = [slot["city"], slot["budget"]]
            elif theme_key == "home_organize":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家。我要在今晚 40 分钟内把家里桌面和玄关收一遍，重点是最省事，口径用“{pref_full}”。请排个清单。"
                    ),
                    ScenarioTurnSpec("我刚才要求你优先哪种方案？如果只能做两步，先保哪两步？"),
                    ScenarioTurnSpec("最后给我一个结论、风险、下一步格式的收尾。"),
                ]
                recall = [pref_marker]
            elif theme_key == "browser_info":
                turns = [
                    ScenarioTurnSpec(
                        "你先当生活管家，帮我打开这个 FAQ 页面看看主要讲了什么，我只关心下载发票和找客服，按“先给结论再列三步”回答：__FAQ__",
                        checker=BASE50._check_faq_page,
                    ),
                    ScenarioTurnSpec("我刚才最关心的是哪两件事？如果今晚只处理一件，你建议先处理哪件？"),
                    ScenarioTurnSpec("最后给我一个今晚能执行的三句收尾：结论、风险、下一步。"),
                ]
                recall = ["下载发票", "找客服"]
            elif theme_key == "office_brief":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家，生成一份 Word 家庭安排简报，包含本周要接{slot['family']}、预算{slot['budget']}元、风险是时间冲突、下一步是今晚确认路线。",
                        checker=BASE50._check_word_generate,
                    ),
                    ScenarioTurnSpec("刚才产出的是什么类型文件？一句话告诉我。", checker=BASE50._check_office_followup_short),
                    ScenarioTurnSpec("再把核心安排压成三句话，适合转发给家里人。"),
                ]
                recall = ["Word"]
            elif theme_key == "system_plan":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家。我想整理电脑下载目录，但先不要执行删除，只给我最省事的步骤，口径按“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才明确要求你别直接做什么？如果先看一眼再动手，你建议我先看哪类文件？"),
                    ScenarioTurnSpec("最后收尾成三句话：结论、风险、下一步。"),
                ]
                recall = ["不要执行删除"]
            elif theme_key == "budget_guard":
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家。这个周末家庭开销我想卡在{slot['budget']}元内，回复口径“{pref_full}”，帮我做个保守安排。"
                    ),
                    ScenarioTurnSpec("我刚才给你的预算红线是多少？如果要省一笔，先省哪一块？"),
                    ScenarioTurnSpec("最后按结论、风险、下一步给我一个收尾。"),
                ]
                recall = [slot["budget"]]
            else:
                turns = [
                    ScenarioTurnSpec(
                        f"你先当生活管家。这轮回复风格记成“{pref_full}”，并且重点照顾{slot['family']}和{slot['day']}{slot['time']}这个安排。先给我一个今晚计划。"
                    ),
                    ScenarioTurnSpec("我刚才给你的回复风格和关键安排分别是什么？"),
                    ScenarioTurnSpec("最后再压成三句话，方便我转发。"),
                ]
                recall = [pref_marker, slot["time"]]
            if theme_key == "browser_info":
                turns[0].prompt = turns[0].prompt.replace("__FAQ__", "{FAQ_URL}")
            cases.append(
                ScenarioSpec(
                    case_id=case_id,
                    role="life_butler",
                    theme=theme_key,
                    title=_make_title("生活管家", theme_label, slot, idx),
                    peer_ref=peer_ref,
                    turns=turns,
                    recall_markers=recall,
                    final_markers=["结论", "风险", "下一步"],
                    boundary_turns={2} if theme_key == "system_plan" else set(),
                    tool_turns={1} if theme_key in {"browser_info", "office_brief"} else set(),
                )
            )
    return cases


def _virtual_partner_cases() -> list[ScenarioSpec]:
    cases: list[ScenarioSpec] = []
    themes = [
        ("soothe", "情绪安抚"),
        ("weekend", "周末约会"),
        ("apology", "修复对话"),
        ("bedtime", "熬夜劝停"),
        ("travel", "双人出行"),
        ("browser_pick", "浏览器找灵感"),
        ("shared_pref", "偏好延续"),
        ("encourage", "工作鼓劲"),
        ("boundary", "越界降温"),
        ("cute_closeout", "温柔收尾"),
    ]
    for theme_key, theme_label in themes:
        for idx, slot in enumerate(SLOTS, start=1):
            pref_full, pref_marker = PREFERENCES[idx - 1]
            case_no = 100 + len(cases) + 1
            case_id = f"FRM300-{case_no:03d}"
            peer_ref = f"oc_feishu_role300_vp_{theme_key}_{idx:02d}"
            if theme_key == "soothe":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样陪我一下，但别太油。我今天状态很差，明天{slot['day']}{slot['time']}还有安排，回复口径记成“{pref_full}”。先稳住我，再给个小步骤。"
                    ),
                    ScenarioTurnSpec("我刚才说自己最担心的时间点是什么？你刚才让我先做的小步骤是什么方向？"),
                    ScenarioTurnSpec("最后给我一个温柔但直接的三句收尾：结论、风险、下一步。"),
                ]
                recall = [slot["time"]]
            elif theme_key == "weekend":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样帮我想周末约会，别太夸张。预算{slot['budget']}元，偏好{slot['movie']}，口径“{pref_full}”，先给我一个轻松方案。"
                    ),
                    ScenarioTurnSpec("我刚才给你的预算和偏好分别是什么？如果只保留一个亮点，你保哪一个？"),
                    ScenarioTurnSpec("最后压成三句话，像发消息给对象那样自然一点。"),
                ]
                recall = [slot["budget"], slot["movie"]]
            elif theme_key == "apology":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样帮我写一段和好的开场，但别太黏，口径“{pref_full}”，重点是我昨晚回消息太晚。"
                    ),
                    ScenarioTurnSpec("我刚才强调你不要写成什么风格？如果只保留一句最重要的话，你保哪句？"),
                    ScenarioTurnSpec("最后给我结论、风险、下一步版的收尾，我要决定要不要发。"),
                ]
                recall = [pref_marker]
            elif theme_key == "bedtime":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样劝我收工睡觉，但别用命令口气。明天{slot['day']}{slot['time']}我要开会，口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我明天最关键的时间点是什么？如果只给一个睡前动作，你建议什么？"),
                    ScenarioTurnSpec("最后三句话收尾，保留结论、风险、下一步。"),
                ]
                recall = [slot["time"]]
            elif theme_key == "travel":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样和我商量一个双人短途计划，去{slot['city']}附近，预算{slot['budget']}元，回复口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才给你的目的地和预算分别是什么？如果下雨，你先换掉哪一环？"),
                    ScenarioTurnSpec("最后给我一个能直接发出去的三句版本。"),
                ]
                recall = [slot["city"], slot["budget"]]
            elif theme_key == "browser_pick":
                turns = [
                    ScenarioTurnSpec(
                        "你先像虚拟恋人那样帮我找点周末灵感，用浏览器搜索 weekend city walk rainy day plan，并带来源，别写得太硬。",
                        checker=BASE50._check_browser_search,
                    ),
                    ScenarioTurnSpec("我刚才让你搜的是哪类灵感？如果只能留一个方向，你建议留哪个？"),
                    ScenarioTurnSpec("最后用三句话收尾，带结论、风险、下一步。"),
                ]
                recall = ["weekend", "rainy day"]
            elif theme_key == "shared_pref":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样记住我这轮偏好：我更喜欢{slot['food']}、不喜欢太吵、回复口径“{pref_full}”。先据此给我一个今晚陪伴方案。"
                    ),
                    ScenarioTurnSpec("我刚才最明确的两个偏好是什么？如果只能照顾一个，你先照顾哪一个？"),
                    ScenarioTurnSpec("最后给我温柔但不腻的三句收尾。"),
                ]
                recall = [slot["food"]]
            elif theme_key == "encourage":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样鼓励我一下，我明天{slot['time']}要和老板过方案，但别灌鸡汤，口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我明天几点有事？你刚才给我的建议更偏准备还是休息？"),
                    ScenarioTurnSpec("最后按结论、风险、下一步三句收尾。"),
                ]
                recall = [slot["time"]]
            elif theme_key == "boundary":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样陪我，但别替我做决定。我现在冲动想给前任发长消息，你先稳住我，再给方案，口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才让你先别替我做什么？如果只保留一道安全阀，你建议哪一道？"),
                    ScenarioTurnSpec("最后三句话收尾，保留结论、风险、下一步。"),
                ]
                recall = ["别替我做决定"]
            else:
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟恋人那样陪我收个尾，风格记成“{pref_full}”，重点照顾我{slot['day']}{slot['time']}前的状态。先给我一段自然回复。"
                    ),
                    ScenarioTurnSpec("我刚才要你按什么风格说话？如果只能保留一句安抚，你留哪句？"),
                    ScenarioTurnSpec("最后再给一个三句版收尾：结论、风险、下一步。"),
                ]
                recall = [pref_marker]
            cases.append(
                ScenarioSpec(
                    case_id=case_id,
                    role="virtual_partner",
                    theme=theme_key,
                    title=_make_title("虚拟恋人", theme_label, slot, idx),
                    peer_ref=peer_ref,
                    turns=turns,
                    recall_markers=recall,
                    final_markers=["结论", "风险", "下一步"],
                    boundary_turns={1, 2} if theme_key == "boundary" else set(),
                    tool_turns={1} if theme_key == "browser_pick" else set(),
                )
            )
    return cases


def _virtual_employee_cases() -> list[ScenarioSpec]:
    cases: list[ScenarioSpec] = []
    themes = [
        ("boss_update", "老板同步"),
        ("meeting", "会议纪要"),
        ("research", "联网搜索"),
        ("browser_read", "浏览器取页"),
        ("office_report", "办公产出"),
        ("system_plan", "系统操作方案"),
        ("data_analysis", "数据分析"),
        ("trace_approval", "审批与trace"),
        ("customer_reply", "客户回复"),
        ("evidence_closeout", "闭环收尾"),
    ]
    for theme_key, theme_label in themes:
        for idx, slot in enumerate(SLOTS, start=1):
            pref_full, pref_marker = PREFERENCES[idx - 1]
            case_no = 200 + len(cases) + 1
            case_id = f"FRM300-{case_no:03d}"
            peer_ref = f"oc_feishu_role300_ve_{theme_key}_{idx:02d}"
            if theme_key == "boss_update":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像靠谱的虚拟员工一样跟我同步：本周负责人是{slot['owner']}，我更喜欢“{pref_full}”的口径。先给我一段老板能直接看的更新。"
                    ),
                    ScenarioTurnSpec("我刚才要你按什么口径同步？本周负责人是谁？"),
                    ScenarioTurnSpec("最后压成三句话：结论、风险、下一步。"),
                ]
                recall = [pref_marker, slot["owner"]]
            elif theme_key == "meeting":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，帮我整理会议纪要：负责人{slot['owner']}，关键截止是{slot['day']}{slot['time']}，回复口径“{pref_full}”。先给我一个结构化版本。"
                    ),
                    ScenarioTurnSpec("我刚才给你的负责人和截止点是什么？如果只提醒一件事，你先提醒什么？"),
                    ScenarioTurnSpec("最后给我一个适合飞书群里发的三句收尾。"),
                ]
                recall = [slot["owner"], slot["time"]]
            elif theme_key == "research":
                turns = [
                    ScenarioTurnSpec(
                        "你先像虚拟员工，帮我用浏览器搜索 trace evidence workflow，并带来源总结，回复口径用“先给结论再列三步”。",
                        checker=BASE50._check_browser_search,
                    ),
                    ScenarioTurnSpec("我刚才让你搜的主题是什么？如果只保留一个核心结论，你会留哪个？"),
                    ScenarioTurnSpec("最后按结论、风险、下一步给我一个收尾。"),
                ]
                recall = ["trace evidence workflow"]
            elif theme_key == "browser_read":
                turns = [
                    ScenarioTurnSpec(
                        "你先像虚拟员工，打开这个页面告诉我主要内容，我最关心标题和页面事实：__PAGE__",
                        checker=BASE50._check_browser_page,
                    ),
                    ScenarioTurnSpec("我刚才最关心页面里的哪两类信息？如果只给管理层一句话，你会怎么说？"),
                    ScenarioTurnSpec("最后收尾成三句话：结论、风险、下一步。"),
                ]
                recall = ["标题", "页面事实"]
            elif theme_key == "office_report":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，生成一份 Word 测试周报，包含负责人{slot['owner']}、风险是高峰并发、下一步是今晚补回归。",
                        checker=BASE50._check_word_generate,
                    ),
                    ScenarioTurnSpec("刚才产出的是什么类型文件？一句话告诉我。", checker=BASE50._check_office_followup_short),
                    ScenarioTurnSpec("再把周报核心压成三句话给我。"),
                ]
                recall = ["Word"]
            elif theme_key == "system_plan":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，帮我规划一下 Windows 上清理临时文件的步骤，但先不要执行，只给方案，口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才明确让你不要直接做什么？如果先验证风险，你建议先检查哪一类目录？"),
                    ScenarioTurnSpec("最后给我结论、风险、下一步版收尾。"),
                ]
                recall = ["不要执行"]
            elif theme_key == "data_analysis":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，读一下这组数据并给建议：1月收入120成本80，2月收入150成本95，负责人{slot['owner']}，口径“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才给你的数据里，收入更高的是哪个月？如果只盯一个风险，你先盯什么？"),
                    ScenarioTurnSpec("最后压成老板能看的三句：结论、风险、下一步。"),
                ]
                recall = ["2月", slot["owner"]]
            elif theme_key == "trace_approval":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，解释一下为什么高风险动作要过审批和 trace，回复口径记成“{pref_full}”。"
                    ),
                    ScenarioTurnSpec("我刚才要求你按什么口径解释？如果只留一个原因，你先留审批还是 trace？"),
                    ScenarioTurnSpec("最后给我一个结论、风险、下一步的收尾。"),
                ]
                recall = [pref_marker]
            elif theme_key == "customer_reply":
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，帮我写一段客户回复：当前还差两条关键证据待核对，负责人{slot['owner']}，口径“{pref_full}”，别把没完成说成完成。"
                    ),
                    ScenarioTurnSpec("我刚才明确要求你不要把什么说成什么？如果客户追问，你先强调哪一点？"),
                    ScenarioTurnSpec("最后收尾成三句话：结论、风险、下一步。"),
                ]
                recall = ["没完成说成完成"]
            else:
                turns = [
                    ScenarioTurnSpec(
                        f"你先像虚拟员工，帮我做一次闭环收尾：负责人{slot['owner']}，关键时间{slot['day']}{slot['time']}，回复口径“{pref_full}”。先给一版。"
                    ),
                    ScenarioTurnSpec("我刚才给你的负责人和关键时间是什么？如果证据还没齐，你会怎么说？"),
                    ScenarioTurnSpec("最后按结论、风险、下一步再收一次尾。"),
                ]
                recall = [slot["owner"], slot["time"]]
            if theme_key == "browser_read":
                turns[0].prompt = turns[0].prompt.replace("__PAGE__", "{PAGE_URL}")
            cases.append(
                ScenarioSpec(
                    case_id=case_id,
                    role="virtual_employee",
                    theme=theme_key,
                    title=_make_title("虚拟员工", theme_label, slot, idx),
                    peer_ref=peer_ref,
                    turns=turns,
                    recall_markers=recall,
                    final_markers=["结论", "风险", "下一步"],
                    boundary_turns={2} if theme_key in {"system_plan", "customer_reply"} else set(),
                    tool_turns={1} if theme_key in {"research", "browser_read", "office_report"} else set(),
                )
            )
    return cases


def _build_scenarios(site: Any) -> list[ScenarioSpec]:
    scenarios = _life_butler_cases() + _virtual_partner_cases() + _virtual_employee_cases()
    if len(scenarios) != 300:
        raise RuntimeError(f"expected 300 scenarios, got {len(scenarios)}")
    for scenario in scenarios:
        scenario.turns = _normalized_turns(scenario, site)
    return scenarios


def _normalized_turns(scenario: ScenarioSpec, site: Any) -> list[ScenarioTurnSpec]:
    marker = scenario.recall_markers[0] if scenario.recall_markers else "前面的关键信息"
    closeout = ScenarioTurnSpec(
        "继续刚才这轮对话，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
    )
    if scenario.role == "life_butler":
        if scenario.theme == "browser_info":
            return [
                ScenarioTurnSpec(
                    f"你先当我的生活管家。帮我看这个 FAQ 页面，重点关注“{marker}”，先给结论再列三步：{site.url('/faq')}",
                    checker=BASE50._check_faq_page,
                ),
                ScenarioTurnSpec("继续刚才这轮，不用长期记忆。把刚才页面信息压成两句，并保留来源提醒。"),
                closeout,
            ]
        if scenario.theme == "office_brief":
            return [
                ScenarioTurnSpec(
                    "你先当我的生活管家，生成一份 Word 家庭安排简报，包含本周安排、一个主要风险、一个下一步。",
                    checker=BASE50._check_word_generate,
                ),
                ScenarioTurnSpec("继续刚才这轮，不用长期记忆。用一句话告诉我刚才产出的是什么文件。", checker=BASE50._check_office_followup_short),
                closeout,
            ]
        if scenario.theme == "system_plan":
            return [
                ScenarioTurnSpec(f"你先当我的生活管家。帮我整理电脑下载目录，但先不要执行删除，只给步骤。重点保留“{marker}”。"),
                ScenarioTurnSpec("继续刚才这轮，不用长期记忆。保留先不执行这个边界，把步骤压成两步。"),
                closeout,
            ]
        theme_text = {
            "daily_plan": "今晚安排",
            "shopping": "买菜方案",
            "family_schedule": "家庭协调方案",
            "travel_plan": "周末短途安排",
            "home_organize": "家务整理方案",
            "budget_guard": "预算内安排",
            "preference_recall": "这轮偏好延续",
        }.get(scenario.theme, "生活安排")
        return [
            ScenarioTurnSpec(f"你先当我的生活管家。帮我做一个{theme_text}，重点保留“{marker}”，不要新建任务。"),
            ScenarioTurnSpec(f"继续刚才这轮，不用长期记忆。保留“{marker}”这个条件，把方案改得更短一点，并说明最该先做的一步。"),
            closeout,
        ]
    if scenario.role == "virtual_partner":
        if scenario.theme == "browser_pick":
            return [
                ScenarioTurnSpec(
                    "你先像虚拟恋人那样陪我做选择，但别太腻。请用浏览器搜索 weekend city walk rainy day plan，并带来源总结。",
                    checker=BASE50._check_browser_search,
                ),
                ScenarioTurnSpec("继续刚才这轮，不用长期记忆。把刚才结果压成两句，保留来源提醒。"),
                closeout,
            ]
        if scenario.theme == "boundary":
            return [
                ScenarioTurnSpec("你先像虚拟恋人那样陪我，但别替我做决定。我现在很冲动，先稳住我，再给一个安全方案。"),
                ScenarioTurnSpec("继续刚才这轮，不用长期记忆。保留别替我做决定这个边界，把建议压成两步。"),
                closeout,
            ]
        theme_text = {
            "soothe": "安抚我一下",
            "weekend": "周末轻松约会方案",
            "apology": "和好开场白",
            "bedtime": "睡前收工提醒",
            "travel": "双人短途方案",
            "shared_pref": "照顾我偏好的陪伴方案",
            "encourage": "明天工作前的鼓劲",
            "cute_closeout": "温柔一点的收尾",
        }.get(scenario.theme, "陪伴方案")
        return [
            ScenarioTurnSpec(f"你先像虚拟恋人那样回应我，但别太油。帮我做个{theme_text}，重点保留“{marker}”。"),
            ScenarioTurnSpec(f"继续刚才这轮，不用长期记忆。保留“{marker}”这个重点，把回复改短一点，并告诉我最该先做的一步。"),
            closeout,
        ]
    if scenario.theme == "research":
        return [
            ScenarioTurnSpec(
                "你先像靠谱的虚拟员工一样做事。请用浏览器搜索 trace evidence workflow，并用两句总结，说明来源。",
                checker=BASE50._check_browser_search,
            ),
            ScenarioTurnSpec("继续刚才这轮，不用长期记忆。把刚才搜索结果压成两句，保留来源提醒。"),
            closeout,
        ]
    if scenario.theme == "browser_read":
        return [
            ScenarioTurnSpec(
                f"你先像虚拟员工，打开这个页面看看主要内容，只说页面事实和标题：{site.url('/page')}",
                checker=BASE50._check_browser_page,
            ),
            ScenarioTurnSpec("继续刚才这轮，不用长期记忆。把刚才页面内容压成两句。"),
            closeout,
        ]
    if scenario.theme == "office_report":
        return [
            ScenarioTurnSpec(
                "你先像虚拟员工，生成一份 Word 测试周报，包含当前进展、一个主要风险、一个下一步。",
                checker=BASE50._check_word_generate,
            ),
            ScenarioTurnSpec("继续刚才这轮，不用长期记忆。用一句话告诉我刚才产出的是什么文件。", checker=BASE50._check_office_followup_short),
            closeout,
        ]
    if scenario.theme == "system_plan":
        return [
            ScenarioTurnSpec("你先像虚拟员工，帮我规划 Windows 清理临时文件的步骤，但先不要执行，只给方案。"),
            ScenarioTurnSpec("继续刚才这轮，不用长期记忆。保留先不执行这个边界，把步骤压成两步。"),
            closeout,
        ]
    if scenario.theme == "customer_reply":
        return [
            ScenarioTurnSpec("你先像虚拟员工，写一段客户回复：现在还差两条关键证据待核对，别把没完成说成完成。"),
            ScenarioTurnSpec("继续刚才这轮，不用长期记忆。保留别把没完成说成完成这个边界，把回复压成两句。"),
            closeout,
        ]
    theme_text = {
        "boss_update": "老板同步",
        "meeting": "会议纪要",
        "data_analysis": "数据分析结论",
        "trace_approval": "审批和 trace 说明",
        "evidence_closeout": "闭环收尾",
    }.get(scenario.theme, "工作总结")
    return [
        ScenarioTurnSpec(f"你先像靠谱的虚拟员工一样跟进。帮我做一个{theme_text}，重点保留“{marker}”，不要新建任务。"),
        ScenarioTurnSpec(f"继续刚才这轮，不用长期记忆。保留“{marker}”这个重点，把内容改短一点，并说清最该先做的一步。"),
        closeout,
    ]


def run() -> list[ScenarioResult]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP_DATA_DIR, ignore_errors=True)
    shutil.rmtree(TMP_HOME_DIR, ignore_errors=True)
    TMP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_HOME_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(TMP_DATA_DIR)
    os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
    os.environ["FEISHU_APP_ID"] = "feishu-role300-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-role300-secret"
    BASE50._prepare_fake_home()

    results: list[ScenarioResult] = []
    with TestClient(BASE50.create_app()) as client:
        fake = BASE50._install_fake_feishu(client)
        BASE50._bind_feishu(client)
        BASE50._install_office_skills(client)
        with BASE50._TestSite() as site, BASE50._patched_browser_search(client), BASE50._patched_host_uninstall():
            for scenario in _build_scenarios(site):
                ctx: dict[str, Any] = {"task_ids": {}, "checksums": {}}
                turns: list[TurnEval] = []
                for turn_no, turn_spec in enumerate(scenario.turns, start=1):
                    turn = BASE50._send_turn(
                        client,
                        fake,
                        case_id=f"{scenario.case_id}-T{turn_no}",
                        category=f"{scenario.role}:{scenario.theme}",
                        title=scenario.title,
                        peer_ref=scenario.peer_ref,
                        prompt=turn_spec.prompt,
                    )
                    turns.append(_build_turn_eval(client, turn, turn_no, turn_spec, ctx))
                results.append(_scenario_result(scenario, turns))
    return results


def write_outputs(results: list[ScenarioResult]) -> None:
    pass_count = sum(1 for item in results if item.verdict == "pass")
    warn_count = sum(1 for item in results if item.verdict == "warn")
    fail_count = sum(1 for item in results if item.verdict == "fail")
    avg_rule = round(sum(item.total_score for item in results) / max(len(results), 1), 2)
    avg_system = round(sum(item.avg_system_quality_score for item in results) / max(len(results), 1), 2)
    summary = {
        "scenario_count": len(results),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "avg_rule_score": avg_rule,
        "avg_system_quality_score": avg_system,
        "items": [asdict(item) for item in results],
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    role_counts: dict[str, int] = {}
    for item in results:
        role_counts[item.role] = role_counts.get(item.role, 0) + 1

    case_lines = [
        "# 飞书 300 个角色化多轮复杂场景测试用例",
        "",
        f"- 场景总数：`{len(results)}`",
        "- 角色覆盖：`生活管家 / 虚拟恋人 / 虚拟员工`",
        "- 每个场景均为同一飞书 peer 下的 3 轮连续对话，并按整轮质量评分。",
        "",
        "| Case ID | 角色 | 主题 | 标题 | 轮数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        case_lines.append(f"| `{item.case_id}` | `{item.role}` | `{item.theme}` | {item.title} | `{item.turn_count}` |")
    CASESET_PATH.write_text("\n".join(case_lines), encoding="utf-8")

    report_lines = [
        "# 飞书 300 个角色化多轮复杂场景测试执行报告",
        "",
        f"- 场景总数：`{len(results)}`",
        f"- 通过：`{pass_count}`",
        f"- 警告：`{warn_count}`",
        f"- 失败：`{fail_count}`",
        f"- 平均规则分：`{avg_rule}/12`",
        f"- 平均系统质量分：`{avg_system}/10`",
        f"- 角色分布：`{json.dumps(role_counts, ensure_ascii=False)}`",
        "",
        "## 评分维度",
        "",
        "- 连续性与记忆",
        "- 角色一致性",
        "- 任务完成质量",
        "- 边界与诚实",
        "- 语言自然度",
        "- 收尾质量",
        "",
        "## 场景总表",
        "",
        "| Case ID | 角色 | 主题 | 规则分 | 系统分 | 结果 | 原因 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        reasons = ", ".join(item.reasons) if item.reasons else "无"
        report_lines.append(
            f"| `{item.case_id}` | `{item.role}` | `{item.theme}` | `{item.total_score}` | "
            f"`{item.avg_system_quality_score}` | `{item.verdict}` | {reasons} |"
        )

    report_lines.extend(["", "## Warn / Fail 详情", ""])
    flagged = [item for item in results if item.verdict != "pass"]
    if not flagged:
        report_lines.append("- 本轮 300 个多轮场景全部通过。")
    else:
        for item in flagged:
            report_lines.extend(
                [
                    f"### {item.case_id} {item.title}",
                    "",
                    f"- 角色：`{item.role}`",
                    f"- 主题：`{item.theme}`",
                    f"- 规则分：`{item.total_score}/12`",
                    f"- 系统质量分：`{item.avg_system_quality_score}/10`",
                    f"- 原因：`{', '.join(item.reasons)}`",
                    "",
                    "```json",
                    json.dumps(item.score_breakdown, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
            for turn in item.turns:
                report_lines.extend(
                    [
                        f"- Turn {turn.turn_no} prompt: {turn.prompt}",
                        "```text",
                        turn.reply_text or "<empty>",
                        "```",
                        f"  - notes: {', '.join(turn.notes) if turn.notes else '无'}",
                        f"  - tone_mode: {turn.tone_mode or 'unknown'}",
                        f"  - quality_score: {turn.system_quality_score}",
                    ]
                )
            report_lines.append("")

    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")


def main() -> None:
    results = run()
    write_outputs(results)
    print(
        json.dumps(
            {
                "scenario_count": len(results),
                "pass_count": sum(1 for item in results if item.verdict == "pass"),
                "warn_count": sum(1 for item in results if item.verdict == "warn"),
                "fail_count": sum(1 for item in results if item.verdict == "fail"),
                "report_path": str(REPORT_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
