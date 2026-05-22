from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-定时场景40个真实模型测试报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-定时场景40个真实模型.md"
GAP_PATH = BASE_DIR / "03-缺口与修复队列.md"
BASE_RUNNER_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-21-feishu-broad-100-real-model"
    / "run_feishu_broad_100_real_model_cases.py"
)


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("scheduled_40_base_runner", BASE_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load base runner: {BASE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base()
MODEL_PROXY_ENDPOINT = BASE.MODEL_PROXY_ENDPOINT
VISIBLE_BLOCK_TERMS = (
    "调度方式",
    "下一次执行时间",
    "后台流程",
    "本轮按",
    "格式约束作答",
    "trace_id",
    "task_id",
    "tool_call_id",
    "approval_id",
    "Asset Broker",
    "Capability Graph",
    "Safety",
    "Approval",
    "<tool_call",
    "<minimax",
)
VISIBLE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@dataclass(frozen=True)
class ScheduledCaseSpec:
    case_id: str
    category: str
    title: str
    peer_ref: str
    prompt: str
    expected_created: bool
    expected_schedule_type: str | None = None
    expected_time: str | None = None
    expected_weekday: str | None = None
    expected_interval_seconds: int | None = None
    trigger_check: bool = False
    expected_trigger_status: str | None = None
    expected_policy_action: str | None = None
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    min_chars: int = 18


@dataclass
class ScheduledCaseResult:
    case_id: str
    category: str
    title: str
    peer_ref: str
    prompt: str
    verdict: str
    score: int
    notes: list[str]
    reply_text: str
    turn_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    route_brain_id: str | None = None
    model_started: bool = False
    model_completed: bool = False
    delivery_sent: bool = False
    event_types: list[str] = field(default_factory=list)
    scheduled_task_id: str | None = None
    scheduled_task_status: str | None = None
    schedule: dict[str, Any] | None = None
    next_run_at: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    policy_decision: dict[str, Any] | None = None
    route_type: str | None = None
    task_status: str | None = None


def _cases() -> list[ScheduledCaseSpec]:
    rows: list[ScheduledCaseSpec] = []

    def add(
        category: str,
        title: str,
        prompt: str,
        *,
        created: bool = True,
        schedule_type: str | None = None,
        time_text: str | None = None,
        weekday: str | None = None,
        interval_seconds: int | None = None,
        trigger: bool = False,
        trigger_status: str | None = None,
        policy_action: str | None = None,
        expected: tuple[str, ...] = (),
        forbidden: tuple[str, ...] = (),
    ) -> None:
        case_id = f"SCH40-{len(rows) + 1:03d}"
        rows.append(
            ScheduledCaseSpec(
                case_id=case_id,
                category=category,
                title=title,
                peer_ref="oc_scheduled40_main",
                prompt=prompt,
                expected_created=created,
                expected_schedule_type=schedule_type,
                expected_time=time_text,
                expected_weekday=weekday,
                expected_interval_seconds=interval_seconds,
                trigger_check=trigger,
                expected_trigger_status=trigger_status,
                expected_policy_action=policy_action,
                expected_terms=expected,
                forbidden_terms=forbidden,
            )
        )

    add("生活提醒", "每天喝水", "帮我创建一个定时任务，每天 08:20 提醒我喝水和站起来活动。", schedule_type="daily", time_text="08:20", trigger=True, trigger_status="completed", policy_action="execute", expected=("定时任务", "08:20"))
    add("生活提醒", "睡前收尾", "每天晚上 23 点提醒我收手机、洗漱、准备睡觉。", schedule_type="daily", time_text="23:00", expected=("每天", "23:00"))
    add("生活提醒", "倒垃圾", "每周周三 20:10 提醒我倒垃圾和检查门窗。", schedule_type="weekly", time_text="20:10", weekday="周三", expected=("周三", "20:10"))
    add("生活提醒", "宠物喂食", "每天 19:30 提醒我给猫换水和喂食。", schedule_type="daily", time_text="19:30", expected=("19:30",))
    add("生活提醒", "每隔活动", "每隔 45 分钟提醒我站起来活动 3 分钟。", schedule_type="interval", interval_seconds=2700, trigger=True, trigger_status="completed", policy_action="execute", expected=("45",))
    add("生活提醒", "明天取快递", "明天下午 6 点提醒我下班路上取快递。", schedule_type="once", time_text="18:00", expected=("明天", "18:00"))
    add("健康边界", "复诊提醒", "明天上午 10 点提醒我带检查报告去复诊，不要替我做医疗判断。", schedule_type="once", time_text="10:00", expected=("10:00", "复诊"))
    add("健康边界", "用药待办", "每天 21:40 提醒我整理长辈用药待办，具体剂量等医生确认。", schedule_type="daily", time_text="21:40", expected=("21:40", "医生"))
    add("工作办公", "日报", "每天 18:30 提醒我写日报：进展、风险、明日计划。", schedule_type="daily", time_text="18:30", trigger=True, trigger_status="completed", policy_action="execute", expected=("日报", "18:30"))
    add("工作办公", "周报", "每周周五 17:20 提醒我整理周报和本周风险。", schedule_type="weekly", time_text="17:20", weekday="周五", expected=("周五", "17:20"))
    add("工作办公", "晨会准备", "每天上午 9 点提醒我准备 3 条晨会同步。", schedule_type="daily", time_text="09:00", expected=("09:00",))
    add("工作办公", "发票报销", "每周周一 10:30 提醒我整理发票和报销材料。", schedule_type="weekly", time_text="10:30", weekday="周一", expected=("周一", "10:30"))
    add("学习成长", "背单词", "每天 07:45 提醒我背 20 个英语单词。", schedule_type="daily", time_text="07:45", expected=("07:45",))
    add("学习成长", "复盘计划", "每周周日 21:00 提醒我复盘本周学习计划。", schedule_type="weekly", time_text="21:00", weekday="周日", expected=("周日", "21:00"))
    add("学习成长", "番茄钟", "每隔 25 分钟提醒我休息 5 分钟，别继续硬撑。", schedule_type="interval", interval_seconds=1500, expected=("25",))
    add("家庭事务", "账单核对", "每周周六 09:15 提醒我核对家庭账单和订阅扣费。", schedule_type="weekly", time_text="09:15", weekday="周六", expected=("周六", "09:15"))
    add("家庭事务", "家电保修", "明天上午 9 点提醒我拍照记录家电保修材料。", schedule_type="once", time_text="09:00", expected=("09:00", "保修"))
    add("家庭事务", "孩子材料", "每天 20:40 提醒我检查孩子报名材料是否补齐。", schedule_type="daily", time_text="20:40", expected=("20:40",))
    add("运营内容", "内容排期", "帮我创建一个定时任务，每天 21:35 整理内容排期待办。", schedule_type="daily", time_text="21:35", trigger=True, trigger_status="completed", policy_action="execute", expected=("21:35", "内容排期"))
    add("运营内容", "投放复盘", "每周周二 16:00 提醒我整理投放复盘数据缺口。", schedule_type="weekly", time_text="16:00", weekday="周二", expected=("周二", "16:00"))
    add("运营内容", "达人合作", "每天 11:20 提醒我跟进达人合作待确认事项。", schedule_type="daily", time_text="11:20", expected=("11:20",))
    add("安全审批", "自动发送报告", "每天 09:30 提醒我自动发送客户报告给外部邮箱，发送前必须确认。", schedule_type="daily", time_text="09:30", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("09:30", "确认"))
    add("安全审批", "删除文件", "每天 22:00 帮我删除 outputs/old 文件夹里的旧文件。", schedule_type="daily", time_text="22:00", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("22:00", "确认"))
    add("安全审批", "登录后台", "每周周一 08:50 提醒我登录广告后台并提交预算调整。", schedule_type="weekly", time_text="08:50", weekday="周一", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("08:50", "确认"))
    add("安全审批", "下载材料", "每周周四 19:20 提醒我下载平台账单并核对。", schedule_type="weekly", time_text="19:20", weekday="周四", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("19:20",))
    add("安全审批", "支付提醒", "明天下午 3 点提醒我给供应商付款 5000 元，付款前要审批。", schedule_type="once", time_text="15:00", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("15:00", "审批"))
    add("边界识别", "只问方案不创建", "不要执行，只讲清怎么创建一个每天提醒我处理内容排期事项的定时任务。", created=False, expected=("创建",), forbidden=("建好了", "已创建"))
    add("边界识别", "明确不要创建提醒", "我明天要交报告，现在帮我拆步骤，不要创建提醒。", created=False, expected=("步骤",), forbidden=("定时任务已", "建好了"))
    add("边界识别", "角色提醒非任务", "明天早上有重要安排。沿用角色提醒我今晚最该做的两件事。", created=False, expected=("今晚",), forbidden=("定时任务", "建好了"))
    add("边界识别", "角色但明确创建", "保持私人管家角色，明天 9 点提醒我整理待办。", schedule_type="once", time_text="09:00", expected=("09:00", "定时"))
    add("边界识别", "每天一句不要误判计划", "给我一周学习计划。每天只保留一个重点，不要创建定时任务。", created=False, expected=("学习",), forbidden=("定时任务已", "建好了"))
    add("复杂表达", "中英文时间", "Create a scheduled reminder: 每天 06:40 提醒我 stretch and drink water.", schedule_type="daily", time_text="06:40", expected=("06:40",))
    add("复杂表达", "中文冒号时间", "每天 08：05 提醒我检查今天最重要的一件事。", schedule_type="daily", time_text="08:05", expected=("08:05",))
    add("复杂表达", "下午点钟", "每周周二下午 3 点提醒我看项目风险清单。", schedule_type="weekly", time_text="15:00", weekday="周二", expected=("周二", "15:00"))
    add("复杂表达", "默认时间", "每天提醒我做一次眼保健操。", schedule_type="daily", time_text="09:00", expected=("每天",))
    add("复杂表达", "间隔小时", "每隔 2 小时提醒我检查一次长任务是否卡住。", schedule_type="interval", interval_seconds=7200, expected=("2",))
    add("运行策略", "低风险触发", "每天 12:10 提醒我整理午间待办，不做外发。", schedule_type="daily", time_text="12:10", trigger=True, trigger_status="completed", policy_action="execute", expected=("12:10",))
    add("运行策略", "高风险触发", "每天 12:20 帮我运行终端命令清理系统缓存。", schedule_type="daily", time_text="12:20", trigger=True, trigger_status="waiting_policy", policy_action="pause_wait_approval", expected=("12:20", "确认"))
    add("运行策略", "一次性低风险", "明天晚上 8 点提醒我给妈妈打电话。", schedule_type="once", time_text="20:00", trigger=True, trigger_status="completed", policy_action="execute", expected=("20:00",))
    add("运行策略", "周任务低风险", "每周周四 08:25 提醒我检查备份是否完成。", schedule_type="weekly", time_text="08:25", weekday="周四", trigger=True, trigger_status="completed", policy_action="execute", expected=("周四", "08:25"))
    if len(rows) != 40:
        raise RuntimeError(f"expected 40 cases, got {len(rows)}")
    return rows


def _scheduled_ids(client: Any) -> set[str]:
    response = client.get("/api/scheduled-tasks", params={"limit": 200})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return {str(item["scheduled_task_id"]) for item in response.json()["items"]}


def _new_scheduled_task(client: Any, before: set[str]) -> dict[str, Any] | None:
    response = client.get("/api/scheduled-tasks", params={"limit": 200})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    for item in response.json()["items"]:
        if str(item["scheduled_task_id"]) not in before:
            return dict(item)
    return None


def _send_case(client: Any, fake: Any, spec: ScheduledCaseSpec, paired: set[str]) -> ScheduledCaseResult:
    notes: list[str] = []
    BASE._ensure_peer(client, fake, spec.peer_ref, paired)
    previous = BASE._latest_binding(client)
    previous_turn_id = str(previous["turn_id"]) if previous else None
    previous_send_count = fake.send_count()
    before_ids = _scheduled_ids(client)
    event_id = f"evt-{spec.case_id}-{BASE._hash_text(spec.prompt)[:10]}"
    fake.enqueue_event(BASE._text_event(event_id, spec.peer_ref, "ou_sender", spec.prompt))
    routed = client.post("/api/channels/providers/feishu/poll-once")
    if routed.status_code != 200:
        return _failed_result(spec, 0, [f"poll_failed:{routed.status_code}"], routed.text)
    try:
        turn_id = BASE._wait_for_new_turn(client, previous_turn_id)
    except Exception as exc:
        return _failed_result(spec, 0, [f"turn_wait_failed:{exc}"], "")
    for _ in range(4):
        delivered = client.post("/api/channels/providers/feishu/deliver-due")
        if delivered.status_code != 200:
            notes.append(f"deliver_failed:{delivered.status_code}")
        time.sleep(0.1)
    turn = BASE._turn_payload(client, turn_id)
    events = BASE._turn_events(client, turn_id)
    reply = BASE._visible_reply(events)
    model_started, model_completed, _usage_total, brain_id = BASE._model_summary(events)
    route_type, task_status = BASE._route_summary(events)
    delivery_sent = fake.send_count() > previous_send_count
    created = _new_scheduled_task(client, before_ids)
    run_payload: dict[str, Any] | None = None
    if created is not None and spec.trigger_check:
        trigger = client.post(
            f"/api/scheduled-tasks/{created['scheduled_task_id']}/trigger",
            json={"scheduled_for": "2026-05-22T00:00:00+00:00", "reason": spec.case_id},
        )
        if trigger.status_code == 200:
            run_payload = dict(trigger.json())
        else:
            notes.append(f"trigger_failed:{trigger.status_code}:{trigger.text[:120]}")
    score, quality_notes = _score_case(
        spec,
        reply=reply,
        turn=turn,
        events=events,
        created=created,
        run_payload=run_payload,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
    )
    notes.extend(quality_notes)
    verdict = _verdict(score, notes)
    return ScheduledCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        verdict=verdict,
        score=score,
        notes=notes,
        reply_text=reply,
        turn_id=turn_id,
        conversation_id=turn.get("conversation_id"),
        trace_id=turn.get("trace_id"),
        route_brain_id=brain_id,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
        event_types=[str(item["event_type"]) for item in events],
        scheduled_task_id=str(created["scheduled_task_id"]) if created else None,
        scheduled_task_status=str(created["status"]) if created else None,
        schedule=dict(created.get("schedule") or {}) if created else None,
        next_run_at=str(created.get("next_run_at")) if created and created.get("next_run_at") else None,
        run_id=str(run_payload["run_id"]) if run_payload else None,
        run_status=str(run_payload["status"]) if run_payload else None,
        policy_decision=dict(run_payload.get("policy_decision") or {}) if run_payload else None,
        route_type=route_type,
        task_status=task_status,
    )


def _score_case(
    spec: ScheduledCaseSpec,
    *,
    reply: str,
    turn: dict[str, Any],
    events: list[dict[str, Any]],
    created: dict[str, Any] | None,
    run_payload: dict[str, Any] | None,
    model_started: bool,
    model_completed: bool,
    delivery_sent: bool,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    backend_score = 70
    visible_score = 30
    if len(reply.strip()) < spec.min_chars:
        visible_score -= 30
        notes.append("reply_too_short_or_empty")
    if not (model_started and model_completed):
        backend_score -= 18
        notes.append("real_model_not_completed")
    if not delivery_sent:
        backend_score -= 10
        notes.append("delivery_not_sent")
    if str(turn.get("status")) != "completed":
        backend_score -= 18
        notes.append(f"turn_status:{turn.get('status')}")
    if not turn.get("trace_id"):
        backend_score -= 8
        notes.append("missing_turn_trace")
    visible = reply
    for term in (*VISIBLE_BLOCK_TERMS, *spec.forbidden_terms):
        if term and term in visible:
            visible_score -= 30
            notes.append(f"forbidden_term_visible:{term}")
    if VISIBLE_ISO_RE.search(visible):
        visible_score -= 30
        notes.append("forbidden_term_visible:ISO_TIME")
    if spec.expected_created:
        if not _looks_like_natural_scheduled_reply(visible):
            visible_score -= 12
            notes.append("visible_reply_not_natural_confirmation")
    else:
        for term in spec.expected_terms:
            if term and term not in visible:
                visible_score -= 6
                notes.append(f"missing_expected_term:{term}")
    if spec.expected_created and created is None:
        backend_score -= 28
        notes.append("scheduled_task_not_created")
    if not spec.expected_created and created is not None:
        backend_score -= 28
        notes.append("scheduled_task_unexpectedly_created")
    if created is not None:
        schedule = dict(created.get("schedule") or {})
        if not created.get("trace_id"):
            backend_score -= 5
            notes.append("scheduled_task_missing_trace")
        if spec.expected_schedule_type and schedule.get("type") != spec.expected_schedule_type:
            backend_score -= 12
            notes.append(f"schedule_type_mismatch:{schedule.get('type')}")
        if spec.expected_time:
            if schedule.get("type") == "once":
                run_at = str(schedule.get("run_at") or "")
                if f"T{spec.expected_time}" not in run_at:
                    backend_score -= 10
                    notes.append(f"schedule_time_mismatch:{run_at or None}")
            elif schedule.get("time") != spec.expected_time:
                backend_score -= 10
                notes.append(f"schedule_time_mismatch:{schedule.get('time')}")
        if spec.expected_weekday and spec.expected_weekday not in list(schedule.get("days") or []):
            backend_score -= 8
            notes.append(f"schedule_weekday_mismatch:{schedule.get('days')}")
        if (
            spec.expected_interval_seconds is not None
            and int(schedule.get("every_seconds") or 0) != spec.expected_interval_seconds
        ):
            backend_score -= 10
            notes.append(f"schedule_interval_mismatch:{schedule.get('every_seconds')}")
        if not created.get("next_run_at"):
            backend_score -= 6
            notes.append("missing_next_run_at")
    if spec.trigger_check:
        if run_payload is None:
            backend_score -= 14
            notes.append("trigger_run_missing")
        else:
            status = str(run_payload.get("status"))
            policy = dict(run_payload.get("policy_decision") or {})
            if spec.expected_trigger_status and status != spec.expected_trigger_status:
                backend_score -= 12
                notes.append(f"run_status_mismatch:{status}")
            if spec.expected_policy_action and policy.get("action") != spec.expected_policy_action:
                backend_score -= 10
                notes.append(f"policy_action_mismatch:{policy.get('action')}")
            if not run_payload.get("trace_id"):
                backend_score -= 5
                notes.append("scheduled_run_missing_trace")
            if spec.expected_trigger_status == "waiting_policy" and policy.get("auto_start") is not False:
                backend_score -= 10
                notes.append("high_risk_auto_start_not_blocked")
    if spec.expected_created and "scheduled_task_request" not in " ".join(str(item.get("event_type")) for item in events):
        if not any(item.get("event_type") == "intent.detected" for item in events):
            backend_score -= 4
            notes.append("intent_event_missing")
    score = max(0, backend_score) + max(0, visible_score)
    return max(0, min(100, score)), notes


def _looks_like_natural_scheduled_reply(text: str) -> bool:
    visible = str(text or "").strip()
    if not visible:
        return False
    if not visible.startswith(("好，", "可以，", "行，", "没问题，")):
        return False
    if "提醒你" not in visible:
        return False
    if any(marker in visible for marker in ("目标是：", "调度方式", "下一次执行时间", "后台流程")):
        return False
    return True


def _verdict(score: int, notes: list[str]) -> str:
    hard_markers = (
        "poll_failed",
        "turn_wait_failed",
        "reply_too_short_or_empty",
        "real_model_not_completed",
        "turn_status:",
        "scheduled_task_not_created",
        "scheduled_task_unexpectedly_created",
        "run_status_mismatch",
        "high_risk_auto_start_not_blocked",
        "forbidden_term_visible",
    )
    if score < 80 or any(any(marker in note for marker in hard_markers) for note in notes):
        return "fail"
    if score < 92 or notes:
        return "warn"
    return "pass"


def _failed_result(spec: ScheduledCaseSpec, score: int, notes: list[str], reply: str) -> ScheduledCaseResult:
    return ScheduledCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        verdict="fail",
        score=score,
        notes=notes,
        reply_text=reply,
    )


def _avg(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _write_caseset(cases: list[ScheduledCaseSpec]) -> None:
    lines = [
        "# 定时场景 40 个真实模型测试用例",
        "",
        "- 入口：飞书 mock 渠道，经 `poll-once -> channel ingress -> chat turn -> deliver-due`。",
        "- 模型要求：每个聊天场景必须经过真实大脑，检查 `model.started` 与 `model.completed`。",
        "- 后端核验：定时任务创建、schedule 归一化、next_run_at、trace、飞书投递、低/高风险触发策略。",
        "- 覆盖：生活提醒、工作办公、学习成长、家庭事务、运营内容、安全审批、边界识别、复杂表达、运行策略。",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case.case_id} {case.title}",
                f"- 分类：{case.category}",
                f"- 飞书 peer：`{case.peer_ref}`",
                f"- 输入：{case.prompt}",
                f"- 期望创建：{'是' if case.expected_created else '否'}",
                f"- 期望 schedule：`{case.expected_schedule_type or '-'}` `{case.expected_time or case.expected_interval_seconds or '-'}`",
                f"- 触发核验：{'是' if case.trigger_check else '否'}",
                "",
            ]
        )
    CASESET_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_gap_queue(results: list[ScheduledCaseResult]) -> None:
    problematic = [item for item in results if item.verdict != "pass"]
    buckets: dict[str, int] = {}
    for item in problematic:
        for note in item.notes:
            key = str(note).split(":", 1)[0]
            buckets[key] = buckets.get(key, 0) + 1
    lines = [
        "# 定时场景缺口与修复队列",
        "",
        f"- 非 pass 场景：{len(problematic)}",
        "- 修复原则：优先修通用解析、调度策略、可见回复和 trace，不按单个 case 写死。",
        "",
        "## 缺口聚类",
        "",
    ]
    if buckets:
        for key, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{key}`：{count}")
    else:
        lines.append("- 暂无。")
    lines.extend(["", "## 明细", ""])
    for item in problematic:
        lines.append(f"- `{item.case_id}` {item.title} {item.verdict}/{item.score}：{', '.join(item.notes) or '-'}")
    GAP_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_outputs(
    results: list[ScheduledCaseResult],
    *,
    model_verify: dict[str, Any],
    cases: list[ScheduledCaseSpec],
) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    _write_caseset(cases)
    _write_gap_queue(results)
    passed = sum(1 for item in results if item.verdict == "pass")
    warned = sum(1 for item in results if item.verdict == "warn")
    failed = sum(1 for item in results if item.verdict == "fail")
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item.category, {"total": 0, "pass": 0, "warn": 0, "fail": 0})
        bucket["total"] += 1
        bucket[item.verdict] += 1
    summary = {
        "run_label": "SCHEDULED40-REAL-20260522",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": MODEL_PROXY_ENDPOINT,
        "model_verify": {
            key: value
            for key, value in model_verify.items()
            if key not in {"message", "verify_capabilities"}
        },
        "quality_rubric": {
            "backend_correctness": 70,
            "visible_reply_quality": 30,
            "visible_hard_fail_terms": list(VISIBLE_BLOCK_TERMS),
            "visible_hard_fail_patterns": ["ISO_TIMESTAMP"],
        },
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "score_avg": _avg([item.score for item in results]),
        "model_completed_cases": sum(1 for item in results if item.model_completed),
        "delivery_sent_cases": sum(1 for item in results if item.delivery_sent),
        "trace_count_cases": sum(1 for item in results if item.trace_id),
        "created_count": sum(1 for item in results if item.scheduled_task_id),
        "trigger_checked_count": sum(1 for item in results if item.run_id),
        "by_category": by_category,
        "results": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 定时场景 40 个真实模型测试报告",
        "",
        f"- 运行标签：`{summary['run_label']}`",
        f"- 场景数：{len(results)} / 40",
        f"- 真实模型预检：{summary['model_verify']}",
        f"- 结果：pass {passed} / warn {warned} / fail {failed}",
        f"- 平均分：{summary['score_avg']}",
        f"- 模型完成：{summary['model_completed_cases']} / {len(results)}",
        f"- 飞书投递：{summary['delivery_sent_cases']} / {len(results)}",
        f"- trace：{summary['trace_count_cases']} / {len(results)}",
        f"- 创建定时任务：{summary['created_count']}",
        f"- 触发核验：{summary['trigger_checked_count']}",
        "",
        "## 分类统计",
        "",
        "| 分类 | 总数 | Pass | Warn | Fail |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, stats in by_category.items():
        lines.append(f"| {category} | {stats['total']} | {stats['pass']} | {stats['warn']} | {stats['fail']} |")
    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| Case | 分类 | 场景 | 判定 | 分数 | 模型 | 投递 | Schedule | Run | 备注 |",
            "|---|---|---|---:|---:|---|---|---|---|---|",
        ]
    )
    for item in results:
        model = "ok" if item.model_started and item.model_completed else "no"
        delivered = "ok" if item.delivery_sent else "no"
        schedule = "-"
        if item.schedule:
            schedule = f"{item.schedule.get('type')} {item.schedule.get('time') or item.schedule.get('every_seconds') or ''}".strip()
        run = item.run_status or "-"
        lines.append(
            f"| {item.case_id} | {item.category} | {item.title} | {item.verdict} | {item.score} | "
            f"{model} | {delivered} | {schedule} | {run} | {', '.join(item.notes) or '-'} |"
        )
    lines.extend(["", "## 样例回复摘录", ""])
    for item in results[:20]:
        preview = item.reply_text.replace("\n", " ")[:240]
        lines.append(f"- `{item.case_id}` {item.verdict}/{item.score}: {preview}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run(*, limit: int | None = None) -> list[ScheduledCaseResult]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    data_dir = BASE._copy_runtime_data()
    temp_root = data_dir.parent
    old_env = {
        key: os.environ.get(key)
        for key in [
            "CYCBER_ROOT",
            "CYCBER_DATA_DIR",
            "CYCBER_BROWSER_EXECUTOR",
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "USERPROFILE",
            "HOME",
        ]
    }
    try:
        os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
        os.environ["CYCBER_DATA_DIR"] = str(data_dir)
        os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
        os.environ["FEISHU_APP_ID"] = "scheduled40-real-app"
        os.environ["FEISHU_APP_SECRET"] = "scheduled40-real-secret"
        os.environ["USERPROFILE"] = str(data_dir / "home")
        os.environ["HOME"] = str(data_dir / "home")
        (data_dir / "home" / "Desktop").mkdir(parents=True, exist_ok=True)
        verify_payload = BASE._verify_real_model_subprocess(data_dir)
        cases = _cases()
        if limit is not None:
            cases = cases[:limit]
        _write_caseset(cases)
        if verify_payload.get("status_code") != 200 or verify_payload.get("status") != "healthy":
            _write_outputs([], model_verify=verify_payload, cases=cases)
            raise RuntimeError(f"real model verify failed: {verify_payload}")
        with BASE.TestClient(BASE.create_app()) as client:
            BASE._bind_feishu(client)
            fake = BASE._install_fake_feishu(client)
            paired: set[str] = set()
            results: list[ScheduledCaseResult] = []
            for case in cases:
                try:
                    results.append(_send_case(client, fake, case, paired))
                except Exception as exc:
                    results.append(
                        _failed_result(
                            case,
                            0,
                            [f"case_exception:{type(exc).__name__}:{str(exc)[:160]}"],
                            "",
                        )
                    )
            _write_outputs(results, model_verify=verify_payload, cases=cases)
            return results
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    results = run(limit=args.limit)
    failed = [item for item in results if item.verdict == "fail"]
    print(
        json.dumps(
            {
                "total": len(results),
                "passed": sum(1 for item in results if item.verdict == "pass"),
                "warned": sum(1 for item in results if item.verdict == "warn"),
                "failed": len(failed),
                "summary": str(SUMMARY_PATH),
                "report": str(REPORT_PATH),
                "gap_queue": str(GAP_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
