from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
for rel in [
    "apps/local-cli",
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
]:
    path = str(ROOT_DIR / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from app.main import create_app  # noqa: E402

RUN_LABEL = "goal-engine-real-model-10"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "evals" / RUN_LABEL
DEFAULT_DATA_DIR = ROOT_DIR / ".tmp-goal-engine-real-model-10-data"
MEMBER_ID = "mem_xiaoyao"


@dataclass(frozen=True)
class Scenario:
    case_id: str
    title: str
    description: str
    expected_domain: str
    intake: dict[str, Any]
    feedbacks: tuple[str, ...]
    expected_statuses: tuple[str, ...]
    expect_intervention: bool = False
    schedule: dict[str, Any] = field(
        default_factory=lambda: {"type": "daily", "time": "21:00"}
    )


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    case_id: str
    title: str
    expected_domain: str
    goal_id: str | None = None
    verdict: str = "fail"
    steps: list[StepResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def main() -> None:
    args = _parse_args()
    run_label = f"{RUN_LABEL}-{args.scenario_set}" if args.scenario_set != "round1" else RUN_LABEL
    output_dir = Path(args.output_dir or (ROOT_DIR / "data" / "evals" / run_label))
    data_dir = Path(args.data_dir or (ROOT_DIR / f".tmp-{run_label}-data"))
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(data_dir)

    scenarios = SCENARIO_SETS[args.scenario_set]
    selected = scenarios[: args.limit] if args.limit else scenarios
    started = time.time()
    with TestClient(create_app()) as client:
        results = []
        for index, scenario in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] {scenario.case_id} {scenario.title}", flush=True)
            result = run_scenario(client, scenario)
            results.append(result)
            print(
                f"  -> {result.verdict} in {result.elapsed_seconds:.1f}s "
                f"({sum(1 for step in result.steps if step.ok)}/{len(result.steps)} steps)",
                flush=True,
            )

    summary = build_summary(results, elapsed_seconds=time.time() - started)
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=2), flush=True)
    print(f"summary: {summary_path}", flush=True)
    print(f"report: {report_path}", flush=True)
    if summary["totals"]["failed_cases"]:
        raise SystemExit(1)


def run_scenario(client: TestClient, scenario: Scenario) -> ScenarioResult:
    started = time.time()
    result = ScenarioResult(
        case_id=scenario.case_id,
        title=scenario.title,
        expected_domain=scenario.expected_domain,
    )
    conversation_id = _conversation_id(client)
    before_ids = _goal_ids(client, conversation_id)

    goal_id: str | None = None
    plan_id: str | None = None
    scheduled_task_id: str | None = None

    try:
        reply_text = _chat_goal_request(
            client,
            conversation_id=conversation_id,
            text=scenario.description,
            session_id=f"{RUN_LABEL}-{scenario.case_id}",
        )
        goals = [
            item
            for item in client.get(
                "/api/goals",
                params={"conversation_id": conversation_id, "limit": 200},
            ).json()["items"]
            if item["goal_id"] not in before_ids
        ]
        goal = goals[0] if goals else None
        goal_id = goal["goal_id"] if goal else None
        result.goal_id = goal_id
        _step(
            result,
            "establish_goal_from_chat",
            goal is not None and "目标" in reply_text,
            "chat turn created a goal and returned goal-oriented text",
            {"reply_preview": reply_text[:240], "new_goal_count": len(goals)},
        )
        if goal is None:
            return _finish(result, started)

        detail = _get(client, f"/api/goals/{goal_id}")
        plan_id = detail["active_plan"]["goal_plan_id"]
        _step(
            result,
            "domain_intake_plan_shape",
            goal["domain_label"] == scenario.expected_domain
            and bool(detail["plan_items"])
            and bool(detail["milestones"])
            and bool(detail["routines"])
            and detail["goal"]["status"] == "awaiting_confirmation",
            "domain, intake shell, plan items, milestones, and routines are present",
            {
                "domain_label": goal["domain_label"],
                "missing_fields": detail["intake"]["missing_fields"],
                "plan_item_count": len(detail["plan_items"]),
                "milestone_count": len(detail["milestones"]),
                "routine_count": len(detail["routines"]),
            },
        )

        calls_after_create = _get(client, f"/api/goals/{goal_id}/model-calls")["items"]
        _step(
            result,
            "real_model_plan_create",
            _latest_call_succeeded(calls_after_create),
            "model-first planner created the initial plan with a real model call",
            {"statuses": [item["status"] for item in calls_after_create[:3]]},
        )

        updated = _post(client, f"/api/goals/{goal_id}/intake", scenario.intake)
        plan_id = updated["active_plan"]["goal_plan_id"]
        _step(
            result,
            "intake_update_and_replan",
            updated["intake"]["status"] == "confirmed"
            and updated["active_plan"]["version"] >= 2
            and not updated["intake"]["missing_fields"],
            "intake confirmed and a new plan version was generated",
            {
                "intake_status": updated["intake"]["status"],
                "missing_fields": updated["intake"]["missing_fields"],
                "plan_version": updated["active_plan"]["version"],
            },
        )

        calls_after_replan = _get(client, f"/api/goals/{goal_id}/model-calls")["items"]
        _step(
            result,
            "real_model_replan",
            _latest_call_succeeded(calls_after_replan),
            "model-first planner replanned after intake with a real model call",
            {"statuses": [item["status"] for item in calls_after_replan[:4]]},
        )

        confirmed = _post(
            client,
            f"/api/goals/{goal_id}/plans/{plan_id}/confirm",
            {
                "start_supervision": True,
                "supervision": {"schedule": scenario.schedule},
            },
        )
        policy = confirmed.get("supervision_policy") or {}
        scheduled_task_id = policy.get("scheduled_task_id")
        _step(
            result,
            "confirm_supervision_scheduled_task",
            confirmed["goal"]["status"] == "active" and bool(scheduled_task_id),
            "goal confirmed and supervision created a scheduled goal check-in task",
            {
                "goal_status": confirmed["goal"]["status"],
                "scheduled_task_id": scheduled_task_id,
                "policy_mode": policy.get("mode"),
            },
        )

        scheduled_task = _get(client, f"/api/scheduled-tasks/{scheduled_task_id}")
        _step(
            result,
            "scheduled_task_contract",
            scheduled_task["constraints"].get("purpose") == "goal_checkin"
            and scheduled_task["constraints"].get("goal_id") == goal_id
            and scheduled_task["status"] == "active",
            "scheduled task is a goal_checkin callback task, not a normal task",
            {
                "status": scheduled_task["status"],
                "constraints": scheduled_task["constraints"],
                "next_run_at": scheduled_task.get("next_run_at"),
            },
        )

        run = _post(
            client,
            f"/api/scheduled-tasks/{scheduled_task_id}/trigger",
            {"reason": "goal_engine_real_model_eval"},
        )
        checkin_id = (run.get("result") or {}).get("checkin_id")
        _step(
            result,
            "reminder_trigger_creates_checkin",
            run["status"] == "completed" and bool(checkin_id) and run.get("task_id") is None,
            "manual reminder trigger produced a goal check-in without creating a normal task",
            {
                "run_status": run["status"],
                "checkin_id": checkin_id,
                "task_id": run.get("task_id"),
                "summary": (run.get("result") or {}).get("summary"),
            },
        )

        latest_progress = None
        parsed_statuses: list[str] = []
        for index, feedback in enumerate(scenario.feedbacks):
            if index > 0:
                checkin_id = _post(client, f"/api/goals/{goal_id}/checkins", {})["checkin_id"]
            latest_progress = _post(
                client,
                f"/api/goals/{goal_id}/checkins/{checkin_id}/reply",
                {"reply_text": feedback},
            )
            checkins = _get(client, f"/api/goals/{goal_id}/checkins")["items"]
            replied = [item for item in checkins if item.get("replied_at")]
            parsed_statuses.append(replied[0]["parsed_status"] if replied else "")

        expected_last = scenario.expected_statuses[-1]
        _step(
            result,
            "supervision_care_progress_update",
            latest_progress is not None
            and parsed_statuses[-len(scenario.expected_statuses) :] == list(scenario.expected_statuses)
            and _latest_checkin_has_care(client, goal_id),
            "check-in replies are parsed, progress is updated, and care/advice text is stored",
            {
                "expected_statuses": list(scenario.expected_statuses),
                "parsed_statuses": parsed_statuses,
                "progress_percent": latest_progress.get("progress_percent") if latest_progress else None,
                "last_expected": expected_last,
            },
        )

        detail_after_feedback = _get(client, f"/api/goals/{goal_id}")
        _step(
            result,
            "intervention_policy",
            (detail_after_feedback.get("latest_intervention") is not None)
            if scenario.expect_intervention
            else True,
            "consecutive missed/blocked feedback creates a gentle intervention when expected",
            {"latest_intervention": detail_after_feedback.get("latest_intervention")},
        )

        candidates = _get(
            client,
            "/api/memory/candidates",
            params={"member_id": MEMBER_ID, "limit": 200},
        )["items"]
        goal_memory = [
            item
            for item in candidates
            if item["source"].get("type") == "goal_event"
            and item["source"].get("goal_id") == goal_id
        ]
        _step(
            result,
            "memory_projection",
            bool(goal_memory),
            "goal progress/check-in signal was projected into memory with source",
            {
                "candidate_count": len(goal_memory),
                "source": goal_memory[0]["source"] if goal_memory else None,
            },
        )

        timeline = _get(client, f"/api/goals/{goal_id}/timeline")["items"]
        timeline_kinds = {item["kind"] for item in timeline}
        events = _get(client, f"/api/goals/{goal_id}/events")["items"]
        _step(
            result,
            "timeline_events",
            {"event", "checkin"}.issubset(timeline_kinds)
            and any(item["event_type"] == "goal.checkin_replied" for item in events),
            "timeline and events contain the goal lifecycle and check-in reply",
            {
                "timeline_kinds": sorted(timeline_kinds),
                "event_types": [item["event_type"] for item in events[:12]],
            },
        )
    except Exception as exc:
        _step(
            result,
            "scenario_exception",
            False,
            f"{type(exc).__name__}: {exc}",
        )
    return _finish(result, started)


def _finish(result: ScenarioResult, started: float) -> ScenarioResult:
    result.elapsed_seconds = round(time.time() - started, 3)
    result.verdict = "pass" if result.steps and all(step.ok for step in result.steps) else "fail"
    return result


def _step(
    result: ScenarioResult,
    name: str,
    ok: bool,
    detail: str,
    data: dict[str, Any] | None = None,
) -> None:
    result.steps.append(StepResult(name=name, ok=bool(ok), detail=detail, data=data or {}))


def _conversation_id(client: TestClient) -> str:
    response = client.get("/api/chat/conversations")
    _assert_response(response, "list conversations")
    return str(response.json()["items"][0]["conversation_id"])


def _goal_ids(client: TestClient, conversation_id: str) -> set[str]:
    response = client.get("/api/goals", params={"conversation_id": conversation_id, "limit": 200})
    _assert_response(response, "list goals")
    return {str(item["goal_id"]) for item in response.json()["items"]}


def _chat_goal_request(
    client: TestClient,
    *,
    conversation_id: str,
    text: str,
    session_id: str,
) -> str:
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": MEMBER_ID,
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    _assert_response(created, "create chat turn")
    stream = client.get(created.json()["stream_url"])
    _assert_response(stream, "read chat stream")
    return _reply_from_sse(stream.text)


def _reply_from_sse(raw: str) -> str:
    chunks: list[str] = []
    fallback = ""
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("event") == "response.delta":
                chunks.append(str(event.get("payload", {}).get("text") or ""))
            if event.get("event") == "response.completed":
                response_plan = event.get("payload", {}).get("response_plan", {})
                fallback = str(
                    response_plan.get("plain_text") or response_plan.get("summary") or ""
                )
    return "".join(chunks).strip() or fallback.strip()


def _get(client: TestClient, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(path, params=params)
    _assert_response(response, f"GET {path}")
    return response.json()


def _post(client: TestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    _assert_response(response, f"POST {path}")
    return response.json()


def _assert_response(response: Any, label: str) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:800]}")


def _latest_call_succeeded(calls: list[dict[str, Any]]) -> bool:
    return bool(calls) and calls[0].get("status") == "succeeded"


def _latest_checkin_has_care(client: TestClient, goal_id: str) -> bool:
    checkins = _get(client, f"/api/goals/{goal_id}/checkins")["items"]
    replied = [item for item in checkins if item.get("replied_at")]
    if not replied:
        return False
    latest = replied[0]
    return bool(latest.get("advice")) and bool(str(latest.get("encouragement_text") or ""))


def build_summary(
    results: list[ScenarioResult],
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    step_names = sorted({step.name for result in results for step in result.steps})
    step_summary = {}
    for name in step_names:
        relevant = [step for result in results for step in result.steps if step.name == name]
        step_summary[name] = {
            "passed": sum(1 for step in relevant if step.ok),
            "failed": sum(1 for step in relevant if not step.ok),
            "total": len(relevant),
        }
    return {
        "run_label": RUN_LABEL,
        "totals": {
            "case_count": len(results),
            "passed_cases": sum(1 for item in results if item.verdict == "pass"),
            "failed_cases": sum(1 for item in results if item.verdict != "pass"),
            "elapsed_seconds": round(elapsed_seconds, 3),
        },
        "step_summary": step_summary,
        "cases": [
            {
                "case_id": result.case_id,
                "title": result.title,
                "expected_domain": result.expected_domain,
                "goal_id": result.goal_id,
                "verdict": result.verdict,
                "elapsed_seconds": result.elapsed_seconds,
                "steps": [
                    {
                        "name": step.name,
                        "ok": step.ok,
                        "detail": step.detail,
                        "data": step.data,
                    }
                    for step in result.steps
                ],
            }
            for result in results
        ],
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['run_label']}",
        "",
        "## Totals",
        "",
        f"- Cases: {summary['totals']['case_count']}",
        f"- Passed: {summary['totals']['passed_cases']}",
        f"- Failed: {summary['totals']['failed_cases']}",
        f"- Elapsed seconds: {summary['totals']['elapsed_seconds']}",
        "",
        "## Step Summary",
        "",
    ]
    for name, item in summary["step_summary"].items():
        lines.append(f"- {name}: {item['passed']}/{item['total']} passed")
    lines.extend(["", "## Cases", ""])
    for case in summary["cases"]:
        lines.append(f"### {case['case_id']} {case['title']} - {case['verdict']}")
        for step in case["steps"]:
            marker = "PASS" if step["ok"] else "FAIL"
            lines.append(f"- {marker} `{step['name']}`: {step['detail']}")
            if not step["ok"] and step.get("data"):
                lines.append(f"  - data: `{json.dumps(step['data'], ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Goal Engine 10 real-model scenarios.")
    parser.add_argument("--scenario-set", choices=sorted(SCENARIO_SETS), default="round1")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


SCENARIOS: list[Scenario] = [
    Scenario(
        case_id="GOAL-001",
        title="soft exam certification",
        description="我要考软考高项，帮我制定备考目标和监督计划。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "软考高项"},
            "confirm": True,
        },
        feedbacks=("今天完成了第一章复习和错题整理。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-002",
        title="english speaking",
        description="我要学英语口语，三个月能和客户开会，帮我安排。",
        expected_domain="language_learning",
        intake={
            "current_level": "能读简单材料，但开口少",
            "target_level": "能参加客户会议并表达观点",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"target_language": "英语"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，听力做了但口语还没练完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-003",
        title="python programming",
        description="我要学习Python编程，做一个自动化脚本项目，帮我监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "只会一点基础语法",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Python",
                "project_goal": "做一个自动化脚本项目",
            },
            "confirm": True,
        },
        feedbacks=("今天卡住了，不会处理报错。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-004",
        title="running fitness",
        description="我要开始跑步健身，提升体能，帮我每天监督。",
        expected_domain="fitness",
        intake={
            "current_level": "很久没运动，先低强度开始",
            "available_time": {"type": "daily", "minutes": 25},
            "confirm": True,
        },
        feedbacks=("今天完成了二十分钟慢跑。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-005",
        title="writing habit",
        description="我要坚持写作，半年写完一本小册子，帮我制定计划。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {"success_criteria": "完成一本小册子初稿"},
            "confirm": True,
        },
        feedbacks=("今天写了一点，完成了提纲的一部分。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-006",
        title="pmp certification",
        description="我要备考PMP证书，帮我安排学习和提醒。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "PMP"},
            "confirm": True,
        },
        feedbacks=("今天没做，临时加班。", "今天还是没做，太累了。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-007",
        title="japanese n2",
        description="我要学习日语，准备达到N2阅读水平，帮我监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "N4左右",
            "target_level": "N2阅读",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "日语"},
            "confirm": True,
        },
        feedbacks=("今天按计划完成了两篇阅读。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-008",
        title="react frontend",
        description="我要学前端开发，用React做一个作品集网站。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点HTML和CSS",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "前端 React",
                "project_goal": "做一个作品集网站",
            },
            "confirm": True,
        },
        feedbacks=("今天不懂组件状态，卡住了。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-009",
        title="fat loss habit",
        description="我要减脂，养成每周运动的习惯，帮我监督和关心。",
        expected_domain="fitness",
        intake={
            "current_level": "运动不稳定，久坐较多",
            "available_time": {"type": "daily", "minutes": 30},
            "constraints": {"health_note": "先低强度，不做高风险动作"},
            "confirm": True,
        },
        feedbacks=("今天没时间运动。", "今天也没做，状态不好。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-010",
        title="career interview",
        description="我要准备转岗面试，每天练习表达和复盘，帮我安排。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"success_criteria": "完成面试表达训练和复盘"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，复盘还没写。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND2: list[Scenario] = [
    Scenario(
        case_id="GOAL-011",
        title="cfa level one",
        description="我要备考CFA一级，想系统复习并让你监督我。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 90},
            "raw_answers": {"exam_name": "CFA一级"},
            "confirm": True,
        },
        feedbacks=("今天完成了数量和道德的一部分复习。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-012",
        title="spanish travel conversation",
        description="我要学习西班牙语，半年后能旅行日常交流，帮我规划和提醒。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "旅行日常交流",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "西班牙语"},
            "confirm": True,
        },
        feedbacks=("今天完成了发音和十个常用句。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-013",
        title="go backend service",
        description="我要学Go后端开发，做一个小型API服务，帮我制定目标并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点编程基础，没写过Go",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Go后端",
                "project_goal": "做一个小型API服务",
            },
            "confirm": True,
        },
        feedbacks=("今天卡住了，不懂路由和中间件。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-014",
        title="strength training",
        description="我要增肌，建立力量训练习惯，帮我监督和关心状态。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，先练基础动作",
            "available_time": {"type": "daily", "minutes": 40},
            "constraints": {"health_note": "动作保守，避免受伤"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，深蹲做了，推举没做完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-015",
        title="public speaking",
        description="我要提升公开表达能力，三个月后能做部门分享，请你陪我练。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"success_criteria": "能完成一次20分钟部门分享"},
            "confirm": True,
        },
        feedbacks=("今天练了一点，开场白还没完全顺。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-016",
        title="law exam",
        description="我要准备法考，想让你帮我拆计划、提醒和复盘。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 80},
            "raw_answers": {"exam_name": "法考"},
            "confirm": True,
        },
        feedbacks=("今天没做，工作太满。", "今天还是没完成，状态不好。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-017",
        title="korean topik",
        description="我要学习韩语，准备TOPIK中级，帮我长期监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "初级",
            "target_level": "TOPIK中级",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "韩语"},
            "confirm": True,
        },
        feedbacks=("今天不懂语法连接词，卡住了。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-018",
        title="data analysis",
        description="我要学数据分析，用SQL和Python做一个数据看板项目，帮我监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会Excel，不熟SQL和Python",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "SQL和Python数据分析",
                "project_goal": "做一个数据看板项目",
            },
            "confirm": True,
        },
        feedbacks=("今天按计划完成了SQL筛选练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-019",
        title="sleep schedule",
        description="我要改善作息，稳定早睡早起，帮我制定计划并每天关心。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 15},
            "raw_answers": {"success_criteria": "连续四周保持早睡早起"},
            "confirm": True,
        },
        feedbacks=("今天没做到，睡得太晚。", "今天还是没做到，刷手机太久。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-020",
        title="swimming stamina",
        description="我要学游泳并提升耐力，每周稳定练习，帮我监督进度。",
        expected_domain="fitness",
        intake={
            "current_level": "会一点蛙泳，耐力差",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "注意安全，不独自高强度训练"},
            "confirm": True,
        },
        feedbacks=("今天完成了四组基础练习。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [1, 4], "time": "20:00"},
    ),
]


SCENARIOS_ROUND3: list[Scenario] = [
    Scenario(
        case_id="GOAL-021",
        title="first-class constructor exam",
        description="我想备考一级建造师，半年内完成三轮复习，请帮我制定计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 75},
            "raw_answers": {"exam_name": "一级建造师"},
            "confirm": True,
        },
        feedbacks=("今天完成了一部分法规章节，案例题还没开始。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-022",
        title="german a2 conversation",
        description="我想学德语，目标是到 A2 能做基础对话，每天提醒我练习。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A2 基础对话",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"target_language": "德语"},
            "confirm": True,
        },
        feedbacks=("今天练完了发音和十个问候句。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-023",
        title="rust cli tool",
        description="我要学习 Rust，做一个命令行小工具，请你安排阶段计划和监督进度。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python，没有 Rust 经验",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Rust",
                "project_goal": "做一个命令行小工具",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在所有权和借用规则，不太理解。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-024",
        title="half marathon training",
        description="我想训练半马，三个月后能稳定跑完，请帮我安排训练和关心恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "能跑 5 公里，配速不稳定",
            "available_time": {"type": "weekly", "minutes": 180},
            "constraints": {"health_note": "膝盖偶尔酸，训练强度要保守"},
            "confirm": True,
        },
        feedbacks=("今天没跑，膝盖有点不舒服。", "今天还是休息了，担心加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 5, 7], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-025",
        title="writing habit",
        description="我想养成写作习惯，每周稳定输出一篇文章，帮我拆计划和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"success_criteria": "每周完成一篇文章并复盘选题"},
            "confirm": True,
        },
        feedbacks=("今天写了开头和提纲，正文还没展开。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-026",
        title="cpa accounting",
        description="我要准备 CPA 会计科目，想让你帮我安排学习、提醒和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 90},
            "raw_answers": {"exam_name": "CPA 会计"},
            "confirm": True,
        },
        feedbacks=("今天做完了长期股权投资的一组题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-027",
        title="pte english speaking",
        description="我想准备 PTE 英语口语，目标两个月后能稳定输出，请你长期监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "英语基础一般，口语不稳定",
            "target_level": "PTE 口语稳定输出",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语"},
            "confirm": True,
        },
        feedbacks=("今天没练，会议太多了。", "今天还是没练，晚上状态不好。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-028",
        title="typescript node project",
        description="我想学 TypeScript 和 Node.js，做一个个人记账 API，帮我规划并提醒。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 JavaScript",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "TypeScript Node.js",
                "project_goal": "个人记账 API",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了项目初始化和一个接口。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-029",
        title="yoga flexibility",
        description="我想练瑜伽提升柔韧性，每周三次，请监督我并关注身体状态。",
        expected_domain="fitness",
        intake={
            "current_level": "初学者，肩颈比较紧",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "避免过度拉伸，循序渐进"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面拉伸动作太累就停了。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [1, 3, 6], "time": "20:30"},
    ),
    Scenario(
        case_id="GOAL-030",
        title="personal finance habit",
        description="我想建立个人记账和预算习惯，持续三个月，请帮我监督和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续三个月记录支出并做月度预算复盘"},
            "confirm": True,
        },
        feedbacks=("今天卡住了，不知道预算分类怎么分。",),
        expected_statuses=("blocked",),
    ),
]


SCENARIOS_ROUND4: list[Scenario] = [
    Scenario(
        case_id="GOAL-031",
        title="securities qualification",
        description="我要准备证券从业资格考试，想用四个月系统复习，请帮我计划和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "证券从业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-032",
        title="french b1 listening speaking",
        description="我想学法语，目标到 B1，重点提升听力和口语，请每天提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "A1-A2 之间",
            "target_level": "B1 听说",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "法语"},
            "confirm": True,
        },
        feedbacks=("今天只练了 15 分钟，听力材料没听完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-033",
        title="docker devops pipeline",
        description="我要学习 Docker 和 DevOps 部署，做一条自动化部署流水线，请帮我拆阶段并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会基础 Linux，不熟悉容器和 CI/CD",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Docker DevOps 部署",
                "project_goal": "自动化部署流水线",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在容器网络，不知道服务之间怎么连。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-034",
        title="cycling endurance",
        description="我想训练骑行耐力，两个月后能完成一次 80 公里骑行，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "能骑 20 公里，长距离经验少",
            "available_time": {"type": "weekly", "minutes": 210},
            "constraints": {"health_note": "注意膝盖和腰背，不追求高强度"},
            "confirm": True,
        },
        feedbacks=("今天骑了 20 公里，算完成。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [2, 4, 7], "time": "19:00"},
    ),
    Scenario(
        case_id="GOAL-035",
        title="meditation habit",
        description="我想建立冥想习惯，连续八周每天练习十分钟，请提醒和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续八周每天冥想十分钟"},
            "confirm": True,
        },
        feedbacks=("今天坐了十分钟，但后面分心了。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-036",
        title="pmp certification",
        description="我要考 PMP，想三个月完成备考，请帮我安排计划、提醒和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 70},
            "raw_answers": {"exam_name": "PMP"},
            "confirm": True,
        },
        feedbacks=("今天临时出差，没来得及看。", "今天还是没看，会议排满了。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-037",
        title="italian travel basics",
        description="我想学意大利语旅行基础，半年后去旅行能点餐问路，请陪我练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "旅行点餐问路",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "意大利语"},
            "confirm": True,
        },
        feedbacks=("今天跟读完了一段点餐对话。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-038",
        title="machine learning predictor",
        description="我想学习机器学习，用 Python 做一个预测模型，请帮我制定路线并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Python 基础，不懂模型评估",
            "available_time": {"type": "daily", "minutes": 55},
            "raw_answers": {
                "language_or_track": "Python 机器学习",
                "project_goal": "预测模型",
            },
            "confirm": True,
        },
        feedbacks=("今天模型效果很差，不知道怎么调参。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-039",
        title="healthy meal prep",
        description="我想建立健康饮食和备餐习惯，减少外卖，请监督我每周执行。",
        expected_domain="fitness",
        intake={
            "current_level": "经常点外卖，备餐不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "不做极端节食，只做稳定饮食习惯"},
            "confirm": True,
        },
        feedbacks=("今天买了菜，但没来得及做饭。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-040",
        title="guitar practice",
        description="我想学吉他，三个月能弹唱一首歌，请帮我安排练习和提醒。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"success_criteria": "能完整弹唱一首歌"},
            "confirm": True,
        },
        feedbacks=("今天练完了和弦转换。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND5: list[Scenario] = [
    Scenario(
        case_id="GOAL-041",
        title="aws solutions architect",
        description="我要准备 AWS 解决方案架构师认证考试，三个月内完成备考，请帮我计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 70},
            "raw_answers": {"exam_name": "AWS 解决方案架构师认证"},
            "confirm": True,
        },
        feedbacks=("今天看完了 EC2 和 VPC 的一部分内容。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-042",
        title="toefl writing speaking",
        description="我想准备托福，重点提升写作和口语，目标 90 分，请每天提醒和复盘。",
        expected_domain="language_learning",
        intake={
            "current_level": "四级通过，口语弱",
            "target_level": "托福 90 分",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"target_language": "英语"},
            "confirm": True,
        },
        feedbacks=("今天完成了独立写作提纲和一段口语录音。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-043",
        title="kotlin android app",
        description="我要学 Kotlin 和 Android 开发，做一个待办清单 App，请帮我拆计划和监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Java，不熟悉 Android",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Kotlin Android",
                "project_goal": "待办清单 App",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 Activity 生命周期，不太理解。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-044",
        title="bodyweight pullup",
        description="我想做自重训练，目标三个月能完成 5 个标准引体向上，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "现在一个标准引体都做不了",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "肩膀偶尔紧，避免硬拉硬撑"},
            "confirm": True,
        },
        feedbacks=("今天练了辅助引体，但只做了两组。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [1, 3, 5], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-045",
        title="digital drawing habit",
        description="我想学数字绘画，半年后能画出完整角色头像，请帮我安排练习和提醒。",
        expected_domain="general",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {"success_criteria": "能画出完整角色头像"},
            "confirm": True,
        },
        feedbacks=("今天练了线稿，阴影还没处理。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-046",
        title="teacher qualification",
        description="我要考教师资格证，想系统准备笔试和面试，请提醒我并监督进度。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 65},
            "raw_answers": {"exam_name": "教师资格证"},
            "confirm": True,
        },
        feedbacks=("今天没复习，临时有事耽搁了。", "今天还是没复习，状态不太好。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-047",
        title="portuguese a1 travel",
        description="我想学葡萄牙语 A1，先达到旅行基础沟通，请陪我长期练习。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 旅行基础沟通",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "葡萄牙语"},
            "confirm": True,
        },
        feedbacks=("今天跟读了问路对话，但是发音还不稳。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-048",
        title="excel vba automation",
        description="我想学 Excel VBA 自动化，做一个报表整理工具，请帮我制定路线并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Excel 公式，不会 VBA",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {
                "language_or_track": "Excel VBA 自动化",
                "project_goal": "报表整理工具",
            },
            "confirm": True,
        },
        feedbacks=("今天写完了第一个宏，但报错还没解决。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-049",
        title="posture improvement",
        description="我想改善体态和肩颈问题，坚持做拉伸和康复训练，请监督我。",
        expected_domain="fitness",
        intake={
            "current_level": "久坐，肩颈紧张",
            "available_time": {"type": "daily", "minutes": 20},
            "constraints": {"health_note": "如出现疼痛先暂停，动作保守"},
            "confirm": True,
        },
        feedbacks=("今天没有做拉伸，忙到很晚。", "今天还是没有做，忘了安排时间。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-050",
        title="knowledge management",
        description="我想建立个人知识管理系统，把读书笔记和项目经验整理起来，请帮我监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "形成稳定的笔记整理和每周复盘流程"},
            "confirm": True,
        },
        feedbacks=("今天整理了一部分读书笔记，项目经验还没归档。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND6: list[Scenario] = [
    Scenario(
        case_id="GOAL-051",
        title="azure administrator certification",
        description="我要准备 Azure 管理员认证，三个月内拿证，请帮我安排计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "Azure 管理员认证"},
            "confirm": True,
        },
        feedbacks=("今天没看课程，被临时会议打断了。", "今天还是没看，晚上太累。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-052",
        title="chinese hsk4",
        description="我想学中文，目标通过 HSK4，并且能做基础口语交流，请长期提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "HSK2 左右",
            "target_level": "HSK4 基础交流",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "中文"},
            "confirm": True,
        },
        feedbacks=("今天听写完了二十个词。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-053",
        title="unity csharp game",
        description="我想学 Unity 和 C#，做一个 2D 小游戏，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点编程，没有游戏项目经验",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Unity C#",
                "project_goal": "2D 小游戏",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在碰撞检测和脚本绑定。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-054",
        title="badminton footwork",
        description="我想练羽毛球步伐和挥拍，每周三次，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "业余新手，步伐乱",
            "available_time": {"type": "weekly", "minutes": 180},
            "constraints": {"health_note": "膝盖保护优先，不做过量冲刺"},
            "confirm": True,
        },
        feedbacks=("今天练完了步伐，但挥拍只做了一半。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 4, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-055",
        title="fund qualification",
        description="我要准备基金从业认证，想两个月过一科，请帮我安排复习和提醒。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "基金从业认证"},
            "confirm": True,
        },
        feedbacks=("今天刷题只做了 20 道，错题还没整理。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-056",
        title="slide presentation skill",
        description="我想提升做汇报 PPT 的能力，一个月后能独立完成部门汇报，请监督练习。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"success_criteria": "独立完成一次部门汇报 PPT"},
            "confirm": True,
        },
        feedbacks=("今天完成了第一页结构草稿。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-057",
        title="spark data engineering",
        description="我要学 Spark 和数据工程，做一个日志分析管道，请帮我制定路线并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Python 和 SQL，不懂 Spark",
            "available_time": {"type": "daily", "minutes": 55},
            "raw_answers": {
                "language_or_track": "Spark 数据工程",
                "project_goal": "日志分析管道",
            },
            "confirm": True,
        },
        feedbacks=("今天只搭了环境，作业提交还没跑通。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-058",
        title="pilates core",
        description="我想练普拉提提升核心稳定和体态，请每天温和提醒我。",
        expected_domain="fitness",
        intake={
            "current_level": "核心弱，久坐腰背紧",
            "available_time": {"type": "daily", "minutes": 20},
            "constraints": {"health_note": "动作低强度，腰背不适就暂停"},
            "confirm": True,
        },
        feedbacks=("今天没练，腰背有点紧。", "今天还是没练，工作太满。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-059",
        title="home decluttering",
        description="我想做断舍离，四周整理完卧室和书桌，请帮我拆计划和提醒。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "四周整理完卧室和书桌"},
            "confirm": True,
        },
        feedbacks=("今天整理完了一个抽屉。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-060",
        title="gre vocabulary",
        description="我想准备 GRE，先把词汇和阅读稳定下来，请帮我长期监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "英语阅读一般，词汇量不够",
            "target_level": "GRE 词汇和阅读稳定",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "英语"},
            "confirm": True,
        },
        feedbacks=("今天背完了 50 个词，但是阅读没做。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND7: list[Scenario] = [
    Scenario(
        case_id="GOAL-061",
        title="fire engineer exam",
        description="我要准备一级消防工程师考试，想半年内完成备考，请帮我计划和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 75},
            "raw_answers": {"exam_name": "一级消防工程师"},
            "confirm": True,
        },
        feedbacks=("今天完成了消防法规第一节。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-062",
        title="arabic a1 basics",
        description="我想学阿拉伯语，先达到 A1 入门，帮我每天提醒和复盘。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 入门",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "阿拉伯语"},
            "confirm": True,
        },
        feedbacks=("今天只学了字母表的一半。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-063",
        title="flutter dart app",
        description="我要学 Flutter 和 Dart，做一个习惯打卡 App，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端，不会移动端",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Flutter Dart",
                "project_goal": "习惯打卡 App",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在状态管理，不知道 Provider 怎么用。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-064",
        title="tennis serve",
        description="我想练网球发球和步伐，每周两次，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "初学者，发球不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "肩肘保护优先，不做过量挥拍"},
            "confirm": True,
        },
        feedbacks=("今天练了发球，但动作还不稳定。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-065",
        title="postgraduate math",
        description="我要准备考研数学，想系统刷题和复盘，请每天提醒我。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "daily", "minutes": 90},
            "raw_answers": {"exam_name": "考研数学"},
            "confirm": True,
        },
        feedbacks=("今天没刷题，晚上临时加班。", "今天还是没刷，脑子很累。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-066",
        title="podcast speaking",
        description="我想练播客表达，三个月能录一期 20 分钟节目，请帮我安排练习和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"success_criteria": "录制一期 20 分钟播客节目"},
            "confirm": True,
        },
        feedbacks=("今天录了一段开场，但是结构还没顺。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-067",
        title="r data visualization",
        description="我要学 R 语言和数据可视化，做一个分析报告，请帮我制定路线并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Excel，不会 R",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {
                "language_or_track": "R 语言 数据可视化",
                "project_goal": "分析报告",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了 ggplot 第一张图。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-068",
        title="climbing beginner",
        description="我想学攀岩，先提升基础力量和安全动作，请监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，手臂力量弱",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "安全第一，不做高风险动作"},
            "confirm": True,
        },
        feedbacks=("今天没去训练馆，下雨堵车。", "今天还是没去，状态一般。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-069",
        title="photography portfolio",
        description="我想学摄影，三个月做出一组城市街拍作品，请帮我规划并提醒。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成一组城市街拍作品"},
            "confirm": True,
        },
        feedbacks=("今天拍完了十张练习照片。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-070",
        title="delf b2 french",
        description="我想准备 DELF B2 法语考试，重点补写作和听力，请长期监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "B1 左右",
            "target_level": "DELF B2",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"target_language": "法语"},
            "confirm": True,
        },
        feedbacks=("今天写作只改了一段，听力没做完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND8: list[Scenario] = [
    Scenario(
        case_id="GOAL-071",
        title="ccna network certification",
        description="我要准备 CCNA 网络工程师认证，三个月内考过，请帮我规划和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "CCNA 网络工程师认证"},
            "confirm": True,
        },
        feedbacks=("今天做完了子网划分练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-072",
        title="thai a1 travel",
        description="我想学泰语，先达到 A1 旅行基础交流，请每天提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 旅行基础交流",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "泰语"},
            "confirm": True,
        },
        feedbacks=("今天只背了五个句子，发音还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-073",
        title="swift ios app",
        description="我要学 Swift 和 iOS，做一个记事本 App，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点编程，不熟悉移动端",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Swift iOS",
                "project_goal": "记事本 App",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在页面跳转和数据保存。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-074",
        title="basketball shooting",
        description="我想练篮球投篮和体能，每周三次，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "投篮不稳定，体能一般",
            "available_time": {"type": "weekly", "minutes": 180},
            "constraints": {"health_note": "膝盖保护优先，循序渐进"},
            "confirm": True,
        },
        feedbacks=("今天练了投篮，但体能训练没做完。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [1, 3, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-075",
        title="no buy habit",
        description="我想建立少买东西的习惯，连续两个月控制冲动消费，请提醒和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续两个月记录并控制冲动消费"},
            "confirm": True,
        },
        feedbacks=("今天没买奶茶，也完成了消费记录。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-076",
        title="nurse qualification",
        description="我要考护士资格证，想系统复习并每天打卡，请帮我监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 65},
            "raw_answers": {"exam_name": "护士资格证"},
            "confirm": True,
        },
        feedbacks=("今天没做题，临时值班。", "今天还是没做，太困了。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-077",
        title="vietnamese basics",
        description="我想学越南语，三个月能进行基础寒暄，请陪我练习。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "基础寒暄",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"target_language": "越南语"},
            "confirm": True,
        },
        feedbacks=("今天跟读了一小段，但声调还不稳。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-078",
        title="linux command line",
        description="我要学 Linux 命令行和 Shell 脚本，做一个自动备份脚本，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点电脑操作，不熟悉终端",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {
                "language_or_track": "Linux Shell",
                "project_goal": "自动备份脚本",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了 cd 和 ls 练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-079",
        title="boxing fitness",
        description="我想练拳击基础，提高协调和体能，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，协调性一般",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "不做实战对抗，注意手腕保护"},
            "confirm": True,
        },
        feedbacks=("今天没练，手腕有点不舒服。", "今天还是休息了，怕加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 5], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-080",
        title="screen time reduction",
        description="我想减少刷短视频时间，连续六周把每天使用控制在 30 分钟内，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 5},
            "raw_answers": {"success_criteria": "连续六周每天短视频不超过 30 分钟"},
            "confirm": True,
        },
        feedbacks=("今天又刷短视频，没控制住。", "今天还是超时了，有点沮丧。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
]


SCENARIOS_ROUND9: list[Scenario] = [
    Scenario(
        case_id="GOAL-081",
        title="actuary exam",
        description="我要准备精算师考试，想一年内通过两门，请帮我制定计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2027-05",
            "available_time": {"type": "daily", "minutes": 80},
            "raw_answers": {"exam_name": "精算师考试"},
            "confirm": True,
        },
        feedbacks=("今天完成了概率论第一章练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-082",
        title="indonesian travel basics",
        description="我想学印尼语，先能旅行基础沟通，请每天提醒我练习。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "旅行基础沟通",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"target_language": "印尼语"},
            "confirm": True,
        },
        feedbacks=("今天学完了数字和问候语。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-083",
        title="unreal cpp game prototype",
        description="我要学 Unreal Engine 和 C++，做一个 3D 小原型，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点编程，不熟悉游戏引擎",
            "available_time": {"type": "daily", "minutes": 55},
            "raw_answers": {
                "language_or_track": "Unreal Engine C++",
                "project_goal": "3D 小原型",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在蓝图和 C++ 通信。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-084",
        title="table tennis footwork",
        description="我想练乒乓球步伐和正手，每周三次，请监督训练和恢复。",
        expected_domain="fitness",
        intake={
            "current_level": "业余初学，步伐慢",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "膝盖保护优先，不做过量侧移"},
            "confirm": True,
        },
        feedbacks=("今天只练了步伐，正手没练完。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [1, 3, 5], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-085",
        title="job search portfolio",
        description="我想准备求职作品集，两个月内完成简历和三个项目展示，请监督我。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"success_criteria": "完成简历和三个项目展示"},
            "confirm": True,
        },
        feedbacks=("今天整理了一部分项目材料，简历还没改。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-086",
        title="licensed pharmacist",
        description="我要考执业药师资格证，想系统复习药一药二，请每天提醒我。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 70},
            "raw_answers": {"exam_name": "执业药师资格证"},
            "confirm": True,
        },
        feedbacks=("今天没背知识点，临时加班。", "今天还是没背，太困了。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-087",
        title="turkish a1 basics",
        description="我想学土耳其语，目标 A1 日常寒暄，请长期提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 日常寒暄",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "土耳其语"},
            "confirm": True,
        },
        feedbacks=("今天只听了发音课，单词还没背。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-088",
        title="php laravel blog",
        description="我要学 PHP 和 Laravel，做一个个人博客，请帮我制定路线并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 HTML，不熟悉后端",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "PHP Laravel",
                "project_goal": "个人博客",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了路由和控制器练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-089",
        title="ski fitness prep",
        description="我想为滑雪做体能准备，提升腿部力量和平衡，请监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "滑雪新手，腿部力量一般",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "保护膝盖，避免高风险跳跃"},
            "confirm": True,
        },
        feedbacks=("今天没训练，膝盖有点酸。", "今天还是休息了，不想硬撑。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 6], "time": "20:30"},
    ),
    Scenario(
        case_id="GOAL-090",
        title="notion knowledge base",
        description="我想搭一个 Notion 个人知识库，把项目、阅读和灵感统一整理，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "搭好项目、阅读和灵感三个知识库流程"},
            "confirm": True,
        },
        feedbacks=("今天搭完了阅读数据库。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND10: list[Scenario] = [
    Scenario(
        case_id="GOAL-091",
        title="doctor qualification",
        description="我要准备执业医师资格考试，想系统复习基础和病例题，请监督我。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 80},
            "raw_answers": {"exam_name": "执业医师资格考试"},
            "confirm": True,
        },
        feedbacks=("今天完成了生理学一章练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-092",
        title="persian a1",
        description="我想学波斯语，先达到 A1 日常问候，请每天提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 日常问候",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"target_language": "波斯语"},
            "confirm": True,
        },
        feedbacks=("今天只记了字母，发音还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-093",
        title="kubernetes operator",
        description="我要学 Kubernetes 和云原生部署，做一个服务发布示例，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Docker，不熟悉 Kubernetes",
            "available_time": {"type": "daily", "minutes": 55},
            "raw_answers": {
                "language_or_track": "Kubernetes 云原生部署",
                "project_goal": "服务发布示例",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 ingress 配置，不知道域名怎么转发。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-094",
        title="full marathon base",
        description="我想准备全马，先建立跑步基础和恢复习惯，请监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "只能稳定跑 8 公里",
            "available_time": {"type": "weekly", "minutes": 240},
            "constraints": {"health_note": "脚踝偶尔不适，先保守增加里程"},
            "confirm": True,
        },
        feedbacks=("今天没跑，脚踝有点疼。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 4, 7], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-095",
        title="early morning routine",
        description="我想建立早起晨间流程，连续 30 天稳定执行，请提醒和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "连续 30 天完成早起和晨间流程"},
            "confirm": True,
        },
        feedbacks=("今天完成了早起和十分钟整理。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-096",
        title="legal professional exam",
        description="我要准备法律职业资格考试，想让你安排复习、提醒和复盘。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 90},
            "raw_answers": {"exam_name": "法律职业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天没看刑法，晚上有事耽误了。", "今天还是没看，状态很差。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-097",
        title="swedish travel basics",
        description="我想学瑞典语，半年后能旅行基础沟通，请陪我练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "旅行基础沟通",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "瑞典语"},
            "confirm": True,
        },
        feedbacks=("今天跟读完了问候对话。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-098",
        title="web3 solidity basics",
        description="我要学 Solidity 和 Web3，做一个简单合约示例，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 JavaScript，不懂智能合约",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Solidity Web3",
                "project_goal": "简单合约示例",
            },
            "confirm": True,
        },
        feedbacks=("今天写完了第一个合约，但测试还没跑通。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-099",
        title="dance basic rhythm",
        description="我想学跳舞，先提升基础节奏和身体协调，请每周监督练习。",
        expected_domain="fitness",
        intake={
            "current_level": "零基础，节奏感一般",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "避免膝盖过度扭转"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面动作跟不上。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 5], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-100",
        title="personal investing habit",
        description="我想建立长期理财和投资复盘习惯，每周记录资产和决策，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 60},
            "raw_answers": {"success_criteria": "每周记录资产变化和投资决策复盘"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分资产，投资复盘还没写。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND11: list[Scenario] = [
    Scenario(
        case_id="GOAL-101",
        title="cfa level one ethics accounting",
        description="我要准备 CFA 一级，想三个月内完成 Ethics 和财报复习，请监督我。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 75},
            "raw_answers": {"exam_name": "CFA 一级"},
            "confirm": True,
        },
        feedbacks=("今天卡在财报里的递延所得税，题目看不懂。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-102",
        title="dutch b1 conversation",
        description="我想学荷兰语，明年能进行 B1 日常沟通，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "B1 日常沟通",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"target_language": "荷兰语"},
            "confirm": True,
        },
        feedbacks=("今天只跟读了 15 分钟，生词还没复习完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-103",
        title="elixir phoenix api",
        description="我想学 Elixir 和 Phoenix，做一个小型 API 服务，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python 和后端基础",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Elixir Phoenix",
                "project_goal": "小型 API 服务",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了路由和第一个 controller。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-104",
        title="climbing basics",
        description="我想练攀岩，先提升握力和基础路线阅读，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，只去过两次岩馆",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "手腕偶尔酸，避免过度抓握"},
            "confirm": True,
        },
        feedbacks=("今天没去训练馆，手腕有点酸。", "今天还是没练，怕加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [3, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-105",
        title="sleep schedule reset",
        description="我想把睡眠作息调整到 23 点前睡，连续 21 天稳定执行，请提醒和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续 21 天 23 点前上床"},
            "confirm": True,
        },
        feedbacks=("今天又刷短视频到一点，没控制住。", "今天还是超时了，有点沮丧。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-106",
        title="dalf c1 writing",
        description="我想准备 DALF C1 法语写作，半年后考试，请安排练习和监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "B2 左右",
            "target_level": "DALF C1 写作",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"target_language": "法语", "exam_name": "DALF C1"},
            "confirm": True,
        },
        feedbacks=("今天写完了一篇议论文提纲和第一段。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-107",
        title="backend architecture interview",
        description="我准备后端架构面试，想系统练缓存、消息队列和数据库设计，请监督我。",
        expected_domain="programming_learning",
        intake={
            "current_level": "有 3 年后端经验",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"project_goal": "完成后端架构面试题库和讲解练习"},
            "confirm": True,
        },
        feedbacks=("今天只复盘了缓存题，消息队列还没看。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-108",
        title="piano sight reading",
        description="我想练钢琴视奏，先把五线谱反应速度提上来，请每天监督练习。",
        expected_domain="general",
        intake={
            "current_level": "会基础指法，视奏慢",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"success_criteria": "能稳定视奏简单曲谱"},
            "confirm": True,
        },
        feedbacks=("今天完成了 20 分钟视奏和节拍器练习。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-109",
        title="shoulder neck stretch",
        description="我想建立肩颈拉伸和放松习惯，缓解久坐僵硬，请每天提醒我。",
        expected_domain="fitness",
        intake={
            "current_level": "久坐后肩颈紧张",
            "available_time": {"type": "daily", "minutes": 15},
            "constraints": {"health_note": "不做疼痛动作，如持续疼痛就就医"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面开会来不及做完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-110",
        title="household budgeting habit",
        description="我想建立家庭预算和记账习惯，每周复盘支出，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 45},
            "raw_answers": {"success_criteria": "每周完成一次支出分类和预算复盘"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分支出，预算复盘还没写。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [7], "time": "20:30"},
    ),
]


SCENARIOS_ROUND12: list[Scenario] = [
    Scenario(
        case_id="GOAL-111",
        title="actuary exam probability",
        description="我要准备精算师考试，先攻概率论和金融数学，请帮我制定复习计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 80},
            "raw_answers": {"exam_name": "精算师考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了概率论第一章的二十道题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-112",
        title="hebrew a2 reading speaking",
        description="我想学希伯来语，先达到 A2 阅读和基础口语，请每天提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A2 阅读和基础口语",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"target_language": "希伯来语"},
            "confirm": True,
        },
        feedbacks=("今天背完了十个字母和五个问候句。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-113",
        title="c data structures",
        description="我想学 C 语言数据结构，做一个链表和栈的小练习，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python，C 语言刚开始",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "C 语言数据结构",
                "project_goal": "链表和栈的小练习",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在指针和 malloc，代码编译不过。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-114",
        title="tai chi balance",
        description="我想练太极和身体平衡，先养成每周三次基础动作练习，请监督我。",
        expected_domain="fitness",
        intake={
            "current_level": "零基础，平衡感一般",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "膝盖不能深蹲太久"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面膝盖有点不舒服就停了。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [1, 3, 5], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-115",
        title="meditation focus habit",
        description="我想建立冥想和专注习惯，连续 40 天每天 10 分钟，请提醒和复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续 40 天完成 10 分钟冥想"},
            "confirm": True,
        },
        feedbacks=("今天没有冥想，睡前太困直接睡了。", "今天还是忘了，白天太忙。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-116",
        title="toeic listening score",
        description="我准备 TOEIC 听力，想两个月把听力分数提到 400，请每天陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "听力大概 300 分",
            "target_level": "TOEIC 听力 400",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语", "exam_name": "TOEIC"},
            "confirm": True,
        },
        feedbacks=("今天只听了一套 Part 2，错题还没整理。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-117",
        title="blue team security basics",
        description="我想学蓝队安全和日志分析，做一个告警排查小项目，请拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Linux 基础，不熟安全分析",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "蓝队安全 日志分析",
                "project_goal": "告警排查小项目",
            },
            "confirm": True,
        },
        feedbacks=("今天搭完了日志样例和第一条检测规则。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-118",
        title="sketch portrait practice",
        description="我想练素描头像，三个月画出完整作品集，请每周监督练习。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成 8 张素描头像作品"},
            "confirm": True,
        },
        feedbacks=("今天画了一半五官结构，明暗还没铺开。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 5], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-119",
        title="meal prep low sugar",
        description="我想建立低糖备餐习惯，控制外卖和夜宵，请每天提醒和监督。",
        expected_domain="fitness",
        intake={
            "current_level": "经常点外卖，晚饭后想吃甜食",
            "available_time": {"type": "daily", "minutes": 25},
            "constraints": {"health_note": "不做医疗诊断，只记录饮食习惯"},
            "confirm": True,
        },
        feedbacks=("今天买了菜，但没来得及做饭，晚上还是点了外卖。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-120",
        title="emergency fund habit",
        description="我想建立家庭应急金和每月储蓄习惯，定期复盘收支，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "每月完成一次储蓄和收支复盘"},
            "confirm": True,
        },
        feedbacks=("今天整理完了本月固定支出和储蓄目标。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND13: list[Scenario] = [
    Scenario(
        case_id="GOAL-121",
        title="aws solutions architect associate",
        description="我要准备 AWS Solutions Architect Associate 认证，三个月内通过，请安排复习和监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 70},
            "raw_answers": {"exam_name": "AWS Solutions Architect Associate"},
            "confirm": True,
        },
        feedbacks=("今天刷完了 VPC 和 IAM 的一组题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-122",
        title="polish a2 conversation",
        description="我想学波兰语，先达到 A2 日常交流，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A2 日常交流",
            "available_time": {"type": "daily", "minutes": 30},
            "raw_answers": {"target_language": "波兰语"},
            "confirm": True,
        },
        feedbacks=("今天只背了 12 个单词，发音练习还没做。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-123",
        title="scala akka service",
        description="我想学 Scala 和 Akka，做一个小型并发服务，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Java，函数式编程不熟",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Scala Akka",
                "project_goal": "小型并发服务",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 Future 和 actor 通信模型，示例跑不通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-124",
        title="pickleball footwork",
        description="我想练匹克球，先提升步伐和反应速度，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，步伐慢",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "脚踝偶尔不稳，先做低强度移动"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面脚踝有点酸就停了。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-125",
        title="inbox zero habit",
        description="我想建立邮件收件箱清理习惯，每天 15 分钟处理未读和待办，请提醒监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 15},
            "raw_answers": {"success_criteria": "连续 30 天完成收件箱清理"},
            "confirm": True,
        },
        feedbacks=("今天整理完了未读邮件和三个待办。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-126",
        title="jlpt n2 grammar",
        description="我要准备 JLPT N2，重点补语法和听力，请安排练习并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "N3 左右",
            "target_level": "JLPT N2",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "日语", "exam_name": "JLPT N2"},
            "confirm": True,
        },
        feedbacks=("今天没复习语法，晚上临时加班。", "今天还是没看，太累了。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-127",
        title="power bi dashboard portfolio",
        description="我想学 Power BI，做一个销售仪表盘作品集，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Excel，不熟 BI 建模",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {
                "language_or_track": "Power BI 数据分析",
                "project_goal": "销售仪表盘作品集",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了数据导入和第一张图表。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-128",
        title="public speaking confidence",
        description="我想提升公众表达能力，三个月后能做 10 分钟分享，请每周监督练习。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 90},
            "raw_answers": {"success_criteria": "完成一次 10 分钟公开分享"},
            "confirm": True,
        },
        feedbacks=("今天只写了开场和提纲，演练还没开始。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-129",
        title="rowing cardio base",
        description="我想练划船机有氧，先提升心肺和动作稳定性，请监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "能划 10 分钟，动作不稳定",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "腰背不适时停止训练"},
            "confirm": True,
        },
        feedbacks=("今天没练，腰背有点不舒服。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-130",
        title="debt repayment review habit",
        description="我想建立还债计划和每月现金流复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "每月更新还债进度和现金流复盘"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分账单，还款优先级还没排完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND14: list[Scenario] = [
    Scenario(
        case_id="GOAL-131",
        title="gre verbal vocabulary",
        description="我要准备 GRE Verbal，先把填空和阅读词汇提上来，请安排复习并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "词汇量不足，阅读速度慢",
            "target_level": "GRE Verbal 稳定提升",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"target_language": "英语", "exam_name": "GRE"},
            "confirm": True,
        },
        feedbacks=("今天背完了 40 个词，但阅读还没做。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-132",
        title="norwegian travel conversation",
        description="我想学挪威语，先能完成旅行场景问路和点餐，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "旅行问路和点餐",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "挪威语"},
            "confirm": True,
        },
        feedbacks=("今天跟读完了问路对话和五个餐厅词。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-133",
        title="terraform aws infra",
        description="我想学 Terraform 和 AWS IaC，做一个可复用基础设施模板，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 AWS 控制台，不熟 IaC",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Terraform AWS IaC",
                "project_goal": "可复用基础设施模板",
            },
            "confirm": True,
        },
        feedbacks=("今天搭完了 provider 和 VPC 模块，但变量还没整理。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-134",
        title="fencing footwork",
        description="我想练击剑，先提升步伐、反应和基础持剑姿势，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，步伐不稳",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "膝盖不适时降低弓步强度"},
            "confirm": True,
        },
        feedbacks=("今天没去训练，膝盖有点酸。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 5], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-135",
        title="digital declutter habit",
        description="我想建立数字断舍离习惯，每天清理照片、文件和收藏，请提醒复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "连续 30 天完成一项数字整理"},
            "confirm": True,
        },
        feedbacks=("今天整理完了 50 张照片和一个下载文件夹。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-136",
        title="sat math prep",
        description="我要准备 SAT Math，两个月内把错题稳定降下来，请制定计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "SAT Math"},
            "confirm": True,
        },
        feedbacks=("今天卡在函数应用题，解析看懂但自己不会列式。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-137",
        title="data warehouse modeling",
        description="我想学数仓建模和 dbt，做一个订单分析模型，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 SQL，不熟数仓分层",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "数仓建模 dbt",
                "project_goal": "订单分析模型",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了维度表草图和一个 dbt model。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-138",
        title="short video editing portfolio",
        description="我想学短视频剪辑，三个月做出 10 条作品，请每周监督练习。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成 10 条短视频作品"},
            "confirm": True,
        },
        feedbacks=("今天只剪了开头 15 秒，字幕和转场还没做。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-139",
        title="posture strength routine",
        description="我想改善圆肩驼背，建立背部力量和姿态训练习惯，请每天提醒。",
        expected_domain="fitness",
        intake={
            "current_level": "久坐，肩背力量弱",
            "available_time": {"type": "daily", "minutes": 20},
            "constraints": {"health_note": "疼痛时停止训练并考虑就医"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面肩膀有点酸就停了。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-140",
        title="subscription spending review",
        description="我想建立订阅支出清理和每月账单复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "每月取消无用订阅并复盘账单"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分订阅，取消清单还没整理完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND15: list[Scenario] = [
    Scenario(
        case_id="GOAL-141",
        title="cpa accounting audit",
        description="我要准备注会 CPA，会计和审计先过一轮，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 90},
            "raw_answers": {"exam_name": "注会 CPA"},
            "confirm": True,
        },
        feedbacks=("今天刷完了长期股权投资的一组题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-142",
        title="ukrainian a1 basics",
        description="我想学乌克兰语，先达到 A1 问候和自我介绍，请每天提醒我。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 问候和自我介绍",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "乌克兰语"},
            "confirm": True,
        },
        feedbacks=("今天只练了字母发音，问候句还没背。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-143",
        title="graphql api service",
        description="我想学 GraphQL 和 Apollo，做一个查询 API 示例，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 REST API，不熟 GraphQL",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "GraphQL Apollo",
                "project_goal": "查询 API 示例",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了 schema 和第一个 query resolver。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-144",
        title="ultimate frisbee stamina",
        description="我想练飞盘，提升启动速度、传接盘和有氧，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，传盘不稳",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "脚踝不适时减少冲刺"},
            "confirm": True,
        },
        feedbacks=("今天没训练，脚踝有点不舒服。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-145",
        title="home cleaning routine",
        description="我想建立每周家务清洁习惯，把厨房、浴室和地面固定整理，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "weekly", "minutes": 120},
            "raw_answers": {"success_criteria": "每周完成一次厨房、浴室和地面清洁"},
            "confirm": True,
        },
        feedbacks=("今天整理完了厨房和地面，浴室还没刷。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [7], "time": "10:00"},
    ),
    Scenario(
        case_id="GOAL-146",
        title="ielts speaking band seven",
        description="我要准备 IELTS 口语，目标 7 分，请每天陪练、提醒和复盘。",
        expected_domain="language_learning",
        intake={
            "current_level": "大概 6 分，Part 2 容易卡",
            "target_level": "IELTS Speaking 7",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语", "exam_name": "IELTS"},
            "confirm": True,
        },
        feedbacks=("今天录完了一个 Part 2，但 Part 3 还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-147",
        title="kafka stream processing",
        description="我想学 Kafka 和流处理，做一个实时订单统计 demo，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点后端，不熟消息流",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Kafka 流处理",
                "project_goal": "实时订单统计 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 consumer group 和 offset，demo 跑不通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-148",
        title="novel chapter draft",
        description="我想写一部长篇小说，先连续三个月完成前 6 章草稿，请每周监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 240},
            "raw_answers": {"success_criteria": "完成前 6 章草稿"},
            "confirm": True,
        },
        feedbacks=("今天写完了第一章大纲和开头 800 字。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [1, 4], "time": "21:00"},
    ),
    Scenario(
        case_id="GOAL-149",
        title="mobility hip routine",
        description="我想建立髋部灵活性和下肢活动度训练习惯，请每天提醒我。",
        expected_domain="fitness",
        intake={
            "current_level": "久坐，髋部紧",
            "available_time": {"type": "daily", "minutes": 15},
            "constraints": {"health_note": "出现刺痛就停止"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面时间不够没做完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-150",
        title="tax document organization",
        description="我想建立报税资料整理和每月票据归档习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "每月完成票据归档和报税资料检查"},
            "confirm": True,
        },
        feedbacks=("今天整理完了本月发票和两个报销记录。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND16: list[Scenario] = [
    Scenario(
        case_id="GOAL-151",
        title="securities qualification exam",
        description="我要准备证券从业资格考试，一个半月内过基础和法规，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "证券从业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了金融市场基础第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-152",
        title="czech a2 travel basics",
        description="我想学捷克语，先达到 A2 旅行基础沟通，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A2 旅行基础沟通",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "捷克语"},
            "confirm": True,
        },
        feedbacks=("今天跟读了一半问路句子，数字还没背完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-153",
        title="sveltekit typescript app",
        description="我想学 SvelteKit 和 TypeScript，做一个个人仪表盘小应用，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 React，不熟 SvelteKit",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "SvelteKit TypeScript",
                "project_goal": "个人仪表盘小应用",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了路由和第一个组件。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-154",
        title="kettlebell strength routine",
        description="我想练壶铃，提升核心力量和髋部发力，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，动作不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "腰背不舒服时停止摆动动作"},
            "confirm": True,
        },
        feedbacks=("今天没训练，腰背有点不舒服。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 5], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-155",
        title="family photo archive",
        description="我想建立家庭照片整理和备份习惯，每周清理相册并归档，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 60},
            "raw_answers": {"success_criteria": "每周完成一次照片整理、命名和备份"},
            "confirm": True,
        },
        feedbacks=("今天整理完了 80 张照片和一个云盘备份文件夹。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-156",
        title="cambridge b2 first",
        description="我要准备剑桥英语 B2 First，重点练写作和口语，请安排计划并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "B1-B2 之间",
            "target_level": "Cambridge B2 First",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "英语", "exam_name": "B2 First"},
            "confirm": True,
        },
        feedbacks=("今天写完了一篇短文，但口语还没录音。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-157",
        title="redis cache design",
        description="我想学 Redis 和缓存设计，做一个接口缓存优化 demo，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会后端 API，不熟缓存策略",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {
                "language_or_track": "Redis 缓存设计",
                "project_goal": "接口缓存优化 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在缓存击穿和过期策略，demo 还没跑通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-158",
        title="watercolor landscape portfolio",
        description="我想练水彩风景，三个月完成 12 张练习作品，请每周监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成 12 张水彩风景练习"},
            "confirm": True,
        },
        feedbacks=("今天画了一半天空和远山，前景还没铺色。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-159",
        title="badminton smash footwork",
        description="我想提升羽毛球杀球和后场步伐，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "业余初级，后场移动慢",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "肩膀不适时降低杀球强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半后场步伐，杀球还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-160",
        title="insurance policy review",
        description="我想建立家庭保险保单整理和年度复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "完成保单清单、保障缺口和年度复盘记录"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分保单，保障缺口还没分析完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND17: list[Scenario] = [
    Scenario(
        case_id="GOAL-161",
        title="fund qualification exam",
        description="我要准备基金从业资格考试，两个月内通过科目一和科目二，请监督复习。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "基金从业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了基金法律法规第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-162",
        title="finnish a1 basics",
        description="我想学芬兰语，先达到 A1 问候和旅行基础，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 问候和旅行基础",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "芬兰语"},
            "confirm": True,
        },
        feedbacks=("今天只练了字母和五个问候句，数字还没背。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-163",
        title="fastapi service project",
        description="我想学 FastAPI 和异步接口，做一个任务管理 API，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Python 基础，不熟异步接口",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "FastAPI 异步接口",
                "project_goal": "任务管理 API",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了路由和第一个 async endpoint。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-164",
        title="trx core stability",
        description="我想练 TRX 悬挂训练，提升核心稳定和肩背力量，请每周监督。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，核心力量弱",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "肩膀不舒服时停止支撑动作"},
            "confirm": True,
        },
        feedbacks=("今天没训练，肩膀有点不舒服。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [2, 5], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-165",
        title="reading notes habit",
        description="我想建立读书笔记和每周输出习惯，每周读完一章并写复盘，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 120},
            "raw_answers": {"success_criteria": "每周完成一章阅读和一篇复盘笔记"},
            "confirm": True,
        },
        feedbacks=("今天读完了一章，复盘笔记还没写。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-166",
        title="pte academic speaking",
        description="我要准备 PTE Academic 口语，目标 65 分，请每天陪练和提醒。",
        expected_domain="language_learning",
        intake={
            "current_level": "口语流利度一般",
            "target_level": "PTE Academic 口语 65",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语", "exam_name": "PTE Academic"},
            "confirm": True,
        },
        feedbacks=("今天录完了 Read Aloud，但 Retell Lecture 还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-167",
        title="snowflake analytics model",
        description="我想学 Snowflake 和数据建模，做一个用户留存分析模型，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 SQL，不熟 Snowflake",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Snowflake 数据建模",
                "project_goal": "用户留存分析模型",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在窗口函数和 cohort 口径，模型没跑通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-168",
        title="photography street portfolio",
        description="我想练街头摄影，三个月完成 30 张作品集，请每周监督练习。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成 30 张街头摄影作品集"},
            "confirm": True,
        },
        feedbacks=("今天拍完了十张练习照片，但还没筛选修图。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 6], "time": "20:30"},
    ),
    Scenario(
        case_id="GOAL-169",
        title="pilates core routine",
        description="我想练普拉提核心，改善体态和呼吸控制，请每天提醒我。",
        expected_domain="fitness",
        intake={
            "current_level": "零基础，核心弱",
            "available_time": {"type": "daily", "minutes": 20},
            "constraints": {"health_note": "腰背不适时降低动作难度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面腰背有点酸就停了。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-170",
        title="estate documents organization",
        description="我想建立遗嘱和重要文件整理习惯，定期检查保单、账户和联系人，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 40},
            "raw_answers": {"success_criteria": "完成重要文件清单和季度复盘记录"},
            "confirm": True,
        },
        feedbacks=("今天整理完了一部分账户清单，联系人信息还没补齐。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND18: list[Scenario] = [
    Scenario(
        case_id="GOAL-171",
        title="insurance agent qualification",
        description="我要准备保险代理人资格考试，一个月内过基础法规，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "保险代理人资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了保险基础知识第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-172",
        title="romanian a1 travel",
        description="我想学罗马尼亚语，先达到 A1 旅行问候和点餐，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 旅行问候和点餐",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "罗马尼亚语"},
            "confirm": True,
        },
        feedbacks=("今天跟读完了问候对话，但点餐词还没背。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-173",
        title="nextjs full stack app",
        description="我想学 Next.js 和 Prisma，做一个全栈待办应用，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 React，不熟全栈数据层",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Next.js Prisma",
                "project_goal": "全栈待办应用",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了页面路由和 Prisma schema。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-174",
        title="swimming freestyle breathing",
        description="我想练自由泳换气，先提升连续游和呼吸节奏，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "能游 25 米，换气不稳",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "呛水或肩膀不适时降低强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半，后面换气乱了就停了。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-175",
        title="sleep hygiene habit",
        description="我想建立睡前放松和减少屏幕时间习惯，连续 30 天执行，请提醒复盘。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 15},
            "raw_answers": {"success_criteria": "连续 30 天睡前减少屏幕并完成放松流程"},
            "confirm": True,
        },
        feedbacks=("今天又刷手机到很晚，没有做睡前放松。", "今天还是超时了，状态很差。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-176",
        title="duolingo english test",
        description="我要准备 Duolingo English Test，目标 120 分，请每天练口语和写作。",
        expected_domain="language_learning",
        intake={
            "current_level": "大概 105 分",
            "target_level": "Duolingo English Test 120",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {"target_language": "英语", "exam_name": "Duolingo English Test"},
            "confirm": True,
        },
        feedbacks=("今天写完了一篇短回答，但口语题还没录。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-177",
        title="clickhouse analytics",
        description="我想学 ClickHouse 和列式分析，做一个日志查询 demo，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 SQL，不熟列式数据库",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "ClickHouse 列式分析",
                "project_goal": "日志查询 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 MergeTree 分区和排序键，查询还没调通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-178",
        title="podcast production habit",
        description="我想做播客，每月发布两期访谈节目，请每周监督选题、录音和剪辑。",
        expected_domain="general",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "每月发布两期播客访谈"},
            "confirm": True,
        },
        feedbacks=("今天只整理了选题和嘉宾名单，录音还没开始。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-179",
        title="run recovery routine",
        description="我想建立跑后拉伸和恢复习惯，减少小腿紧张，请每天提醒我。",
        expected_domain="fitness",
        intake={
            "current_level": "跑后小腿紧，恢复不稳定",
            "available_time": {"type": "daily", "minutes": 15},
            "constraints": {"health_note": "疼痛加重时停止并考虑就医"},
            "confirm": True,
        },
        feedbacks=("今天没拉伸，小腿有点紧。", "今天还是忘了恢复动作。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-180",
        title="retirement pension review",
        description="我想建立养老金和退休账户年度复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "完成养老金、退休账户和年度复盘记录"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分账户余额，退休目标还没更新完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND19: list[Scenario] = [
    Scenario(
        case_id="GOAL-181",
        title="hr professional certificate",
        description="我要准备人力资源管理师考试，先过基础知识和实务，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "人力资源管理师考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了劳动关系管理第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-182",
        title="hungarian a1 greetings",
        description="我想学匈牙利语，先达到 A1 问候和旅行短句，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 问候和旅行短句",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "匈牙利语"},
            "confirm": True,
        },
        feedbacks=("今天只背了 8 个问候词，旅行短句还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-183",
        title="nuxt vue storefront",
        description="我想学 Nuxt 和 Vue，做一个小型电商展示页，请帮我拆计划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端，不熟 Vue 生态",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Nuxt Vue",
                "project_goal": "小型电商展示页",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了商品列表页和一个组件。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-184",
        title="tennis serve basics",
        description="我想练网球发球，先提升抛球、挥拍和脚步协调，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "初学者，发球不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "肩膀不适时降低发球强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半抛球，挥拍还没练。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-185",
        title="daily hydration habit",
        description="我想建立每天喝水和减少含糖饮料习惯，请提醒和监督。",
        expected_domain="fitness",
        intake={
            "current_level": "经常忘记喝水，下午会买甜饮",
            "available_time": {"type": "daily", "minutes": 5},
            "constraints": {"health_note": "不做医疗诊断，只记录饮水习惯"},
            "confirm": True,
        },
        feedbacks=("今天没记录饮水，下午还是买了奶茶。", "今天还是忘了，晚上才想起来。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-186",
        title="cambridge c1 advanced",
        description="我要准备 Cambridge C1 Advanced，重点练阅读和 Use of English，请监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "B2 左右",
            "target_level": "Cambridge C1 Advanced",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"target_language": "英语", "exam_name": "Cambridge C1 Advanced"},
            "confirm": True,
        },
        feedbacks=("今天做完了一套 Use of English，阅读还没订正。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-187",
        title="airflow data pipeline",
        description="我想学 Airflow 和数据管道，做一个每日 ETL DAG，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Python 和 SQL，不熟调度系统",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Airflow 数据管道",
                "project_goal": "每日 ETL DAG",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 DAG 依赖和调度时间，任务还没跑通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-188",
        title="comic storyboard portfolio",
        description="我想练漫画分镜，三个月完成 6 个短篇分镜作品，请每周监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "完成 6 个短篇漫画分镜"},
            "confirm": True,
        },
        feedbacks=("今天画了一半第一篇分镜，台词还没整理。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-189",
        title="hiit beginner conditioning",
        description="我想练 HIIT 入门体能，先提升心肺和动作控制，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "体能一般，跳跃动作容易累",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "膝盖不适时改低冲击动作"},
            "confirm": True,
        },
        feedbacks=("今天没训练，膝盖有点不舒服。", "今天还是休息了，怕受伤。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-190",
        title="donation budget review",
        description="我想建立公益捐赠预算和年度复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "完成捐赠预算、记录和年度复盘"},
            "confirm": True,
        },
        feedbacks=("今天记录了一部分捐赠项目，预算比例还没算完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND20: list[Scenario] = [
    Scenario(
        case_id="GOAL-191",
        title="banking qualification exam",
        description="我要准备银行从业资格考试，先过法律法规和个人理财，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "银行从业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了法律法规第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-192",
        title="lithuanian a1 travel",
        description="我想学立陶宛语，先达到 A1 旅行问候和购物短句，请每天提醒和陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "零基础",
            "target_level": "A1 旅行问候和购物短句",
            "available_time": {"type": "daily", "minutes": 25},
            "raw_answers": {"target_language": "立陶宛语"},
            "confirm": True,
        },
        feedbacks=("今天跟读完了问候句，但购物词还没背。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-193",
        title="remix supabase app",
        description="我想学 Remix 和 Supabase，做一个个人书签应用，请帮我规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 React，不熟后端服务",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Remix Supabase",
                "project_goal": "个人书签应用",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了路由和第一张表设计。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-194",
        title="skateboarding balance",
        description="我想练滑板，先提升平衡、刹车和基础转弯，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，平衡感一般",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "护具齐全，摔倒疼痛时停止"},
            "confirm": True,
        },
        feedbacks=("今天练了一半平衡，刹车还没练。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-195",
        title="desk reset habit",
        description="我想建立每天书桌整理和工作区复位习惯，请提醒和监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续 30 天完成书桌整理和工作区复位"},
            "confirm": True,
        },
        feedbacks=("今天整理完了桌面和两个抽屉。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-196",
        title="oet medical english",
        description="我要准备 OET 医学英语，重点练听力和写作，请每天安排练习并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "医学词汇弱，听力一般",
            "target_level": "OET 听力和写作达标",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "英语", "exam_name": "OET"},
            "confirm": True,
        },
        feedbacks=("今天只听了一段病例对话，转诊信还没写。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-197",
        title="elasticsearch search demo",
        description="我想学 Elasticsearch 和全文检索，做一个文档搜索 demo，请监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会后端 API，不熟搜索引擎",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Elasticsearch 全文检索",
                "project_goal": "文档搜索 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 mapping 和分词器，搜索结果不准。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-198",
        title="newsletter writing habit",
        description="我想做个人 Newsletter，每周发布一期主题文章，请每周监督写作。",
        expected_domain="general",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "每周发布一期 Newsletter"},
            "confirm": True,
        },
        feedbacks=("今天写完了选题和开头，正文还没展开。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [4, 7], "time": "20:30"},
    ),
    Scenario(
        case_id="GOAL-199",
        title="resistance band shoulders",
        description="我想练弹力带肩背训练，改善圆肩和上背力量，请每天提醒。",
        expected_domain="fitness",
        intake={
            "current_level": "肩背力量弱，久坐",
            "available_time": {"type": "daily", "minutes": 15},
            "constraints": {"health_note": "肩膀疼痛时停止训练"},
            "confirm": True,
        },
        feedbacks=("今天没训练，肩膀有点不舒服。", "今天还是休息了，怕加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-200",
        title="tax planning documents review",
        description="我想建立税务规划资料整理和季度复盘习惯，请定期监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "完成税务资料清单和季度复盘记录"},
            "confirm": True,
        },
        feedbacks=("今天整理了一部分税务资料，抵扣清单还没核对完。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND21: list[Scenario] = [
    Scenario(
        case_id="GOAL-201",
        title="gcp cloud engineer certification",
        description="我要准备 Google Cloud Associate Cloud Engineer 认证，三个月内拿证，请帮我安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "Google Cloud Associate Cloud Engineer"},
            "confirm": True,
        },
        feedbacks=("今天完成了 IAM 和 VPC 的一套练习题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-202",
        title="jlpt n2 reading",
        description="我想准备 JLPT N2 日语考试，重点提升阅读和语法，请每天提醒并陪练。",
        expected_domain="language_learning",
        intake={
            "current_level": "N3 左右",
            "target_level": "JLPT N2 阅读和语法达标",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "日语", "exam_name": "JLPT N2"},
            "confirm": True,
        },
        feedbacks=("今天做了一半阅读题，语法错题还没整理。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-203",
        title="fastapi postgres api",
        description="我想学 FastAPI 和 PostgreSQL，做一个待办事项 API 项目，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python，不熟数据库设计",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "FastAPI PostgreSQL",
                "project_goal": "待办事项 API 项目",
            },
            "confirm": True,
        },
        feedbacks=("今天卡住了，数据库迁移和依赖注入没跑通。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-204",
        title="pickleball footwork",
        description="我想练匹克球步伐和正手稳定性，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，移动慢，正手不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "膝盖不舒服时降低强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半步伐，正手稳定性还没练。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-205",
        title="street photo portfolio",
        description="我想建立街头摄影作品集，三个月整理 30 张可发布作品，请每周监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "weekly", "minutes": 180},
            "raw_answers": {"success_criteria": "整理 30 张可发布街头摄影作品"},
            "confirm": True,
        },
        feedbacks=("今天完成了 8 张初选照片，但还没做最终调色。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [3, 7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-206",
        title="topik ii writing",
        description="我要准备 TOPIK II 韩语写作，想两个月提高作文分数，请每天陪练和监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "TOPIK 3-4 级之间",
            "target_level": "TOPIK II 写作提分",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {"target_language": "韩语", "exam_name": "TOPIK II"},
            "confirm": True,
        },
        feedbacks=("今天只写了开头，没时间完成整篇作文。",),
        expected_statuses=("missed",),
    ),
    Scenario(
        case_id="GOAL-207",
        title="kubernetes observability",
        description="我想学 Kubernetes 可观测性，做 Prometheus 和 Grafana 监控面板，请监督推进。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会 Docker 和一点 Linux，不熟 K8s 监控",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Kubernetes Prometheus Grafana 可观测性",
                "project_goal": "服务监控面板",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了 Prometheus 部署，但 Grafana 仪表盘还没接好。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-208",
        title="archery posture",
        description="我想练射箭基础姿势和瞄准稳定性，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，站姿和撒放不稳定",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "肩肘疼痛时停止训练"},
            "confirm": True,
        },
        feedbacks=("今天没训练，肩膀有点不舒服。", "今天还是没练，担心加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [1, 5], "time": "19:00"},
    ),
    Scenario(
        case_id="GOAL-209",
        title="emergency fund habit",
        description="我想建立 6 个月应急金储蓄计划，每周记录预算和进度，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-12",
            "available_time": {"type": "weekly", "minutes": 30},
            "raw_answers": {"success_criteria": "建立 6 个月应急金并持续记录预算"},
            "confirm": True,
        },
        feedbacks=("今天完成了一半预算分类，储蓄比例还没算完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-210",
        title="web accessibility testing",
        description="我想学 Web 无障碍测试，做一个 WCAG 检查清单和前端修复 demo，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端，不熟 WCAG 和读屏测试",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Web 无障碍测试 WCAG",
                "project_goal": "检查清单和前端修复 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了按钮 aria-label 修复和键盘导航检查。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND22: list[Scenario] = [
    Scenario(
        case_id="GOAL-211",
        title="comptia security plus certification",
        description="我要准备 CompTIA Security+ 认证，三个月内通过考试，请帮我安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "CompTIA Security+"},
            "confirm": True,
        },
        feedbacks=("今天完成了网络安全基础章节和一套选择题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-212",
        title="ielts speaking band seven",
        description="我想准备 IELTS 英语口语，目标两个月到 7 分，请每天陪练并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "口语 6 分左右",
            "target_level": "IELTS Speaking 7",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语", "exam_name": "IELTS Speaking"},
            "confirm": True,
        },
        feedbacks=("今天录完了 Part 2 话题，但 Part 3 追问还没练。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-213",
        title="django celery background jobs",
        description="我想学 Django 和 Celery，做一个异步任务后台和 API，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python 和 Web API，不熟异步任务",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "Django Celery Python API",
                "project_goal": "异步任务后台",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 Celery worker 和 broker 配置，任务没有消费。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-214",
        title="rowing machine cardio",
        description="我想练划船机有氧，提升心肺和动作节奏，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "有氧基础一般，划船动作不稳定",
            "available_time": {"type": "weekly", "minutes": 100},
            "constraints": {"health_note": "腰背不适时降低阻力或停止"},
            "confirm": True,
        },
        feedbacks=("今天练了一半划船机，后半段腰背有点紧。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-215",
        title="home inventory system",
        description="我想建立家庭物品清单和保修记录，每周整理一个区域，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "weekly", "minutes": 60},
            "raw_answers": {"success_criteria": "完成家庭物品清单和保修记录"},
            "confirm": True,
        },
        feedbacks=("今天整理完了厨房小家电清单。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-216",
        title="dele b1 spanish",
        description="我要准备 DELE B1 西班牙语考试，重点练阅读和写作，请每天监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "A2 到 B1 之间",
            "target_level": "DELE B1 阅读和写作达标",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "西班牙语", "exam_name": "DELE B1"},
            "confirm": True,
        },
        feedbacks=("今天只做了阅读前两篇，作文没时间完成。",),
        expected_statuses=("missed",),
    ),
    Scenario(
        case_id="GOAL-217",
        title="nextjs prisma dashboard",
        description="我想学 Next.js、Prisma 和 TypeScript，做一个 SaaS 指标看板 demo，请监督推进。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 React 和 TypeScript，不熟 Prisma",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Next.js Prisma TypeScript",
                "project_goal": "SaaS 指标看板 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了数据模型和首页路由，但图表还没接数据。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-218",
        title="boxing footwork basics",
        description="我想练拳击基础步伐和出拳节奏，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，步伐乱，肩膀容易紧",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "肩膀或手腕疼痛时停止"},
            "confirm": True,
        },
        feedbacks=("今天没训练，手腕有点疼。", "今天还是休息了，怕影响恢复。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-219",
        title="patent agent qualification",
        description="我要准备专利代理师资格考试，半年内通过法律和实务科目，请监督复习。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-11",
            "available_time": {"type": "daily", "minutes": 70},
            "raw_answers": {"exam_name": "专利代理师资格考试"},
            "confirm": True,
        },
        feedbacks=("今天刷完了专利法第一章题库。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-220",
        title="meditation journal habit",
        description="我想建立每天 10 分钟冥想和情绪日志习惯，请提醒和监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 10},
            "raw_answers": {"success_criteria": "连续 30 天完成冥想和情绪日志"},
            "confirm": True,
        },
        feedbacks=("今天只冥想了 5 分钟，情绪日志还没写。",),
        expected_statuses=("partial",),
    ),
]


SCENARIOS_ROUND23: list[Scenario] = [
    Scenario(
        case_id="GOAL-221",
        title="aws solutions architect associate",
        description="我要准备 AWS Solutions Architect Associate 认证，三个月内通过，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "AWS Solutions Architect Associate"},
            "confirm": True,
        },
        feedbacks=("今天完成了 EC2 和 VPC 的一套练习题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-222",
        title="tef canada french",
        description="我想准备 TEF Canada 法语考试，重点提升听力和口语，请每天陪练并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "A2 到 B1 之间",
            "target_level": "TEF Canada 听力和口语提分",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "法语", "exam_name": "TEF Canada"},
            "confirm": True,
        },
        feedbacks=("今天练了一半听力，口语复述还没做。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-223",
        title="playwright e2e testing",
        description="我想学 Playwright 和 E2E 自动化测试，做一个登录流程测试 demo，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端和 JavaScript，不熟端到端测试",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Playwright E2E 自动化测试",
                "project_goal": "登录流程测试 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在测试账号初始化和浏览器上下文隔离。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-224",
        title="squash footwork stamina",
        description="我想练壁球步伐和耐力，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，启动慢，耐力一般",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "脚踝或膝盖不适时降低强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半折返步伐，耐力组还没做。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-225",
        title="personal crm followup",
        description="我想建立个人关系维护 CRM，每周整理联系人和跟进记录，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "weekly", "minutes": 45},
            "raw_answers": {"success_criteria": "建立联系人清单和每周跟进记录"},
            "confirm": True,
        },
        feedbacks=("今天整理完了 20 个联系人标签。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [5], "time": "20:30"},
    ),
    Scenario(
        case_id="GOAL-226",
        title="fund qualification exam",
        description="我要准备基金从业资格考试，两个月内通过两科，请监督复习。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "基金从业资格考试"},
            "confirm": True,
        },
        feedbacks=("今天只看了法规第一节，没时间完成整章刷题。",),
        expected_statuses=("missed",),
    ),
    Scenario(
        case_id="GOAL-227",
        title="langchain rag demo",
        description="我想学 LangChain、RAG 和 Chroma，做一个知识库问答 demo，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python，不熟向量数据库和检索增强生成",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {
                "language_or_track": "LangChain RAG Chroma",
                "project_goal": "知识库问答 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了文档切分和向量入库，但召回结果还不稳定。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-228",
        title="jump rope stamina",
        description="我想练跳绳耐力和节奏，先连续跳到 5 分钟，请每天提醒和监督。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，连续跳不到 2 分钟",
            "available_time": {"type": "daily", "minutes": 15},
            "constraints": {"health_note": "小腿或脚踝疼痛时休息"},
            "confirm": True,
        },
        feedbacks=("今天没训练，小腿有点紧。", "今天还是没跳，脚踝不太舒服。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-229",
        title="bedtime reading habit",
        description="我想建立睡前 20 分钟纸书阅读习惯，减少睡前刷手机，请提醒和监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "daily", "minutes": 20},
            "raw_answers": {"success_criteria": "连续 30 天睡前纸书阅读并减少刷手机"},
            "confirm": True,
        },
        feedbacks=("今天只读了 10 分钟，后面又刷手机了。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-230",
        title="npdp product manager certification",
        description="我要准备 NPDP 产品经理认证，四个月内通过，请安排学习计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "NPDP 产品经理认证"},
            "confirm": True,
        },
        feedbacks=("今天完成了新产品战略章节笔记。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND24: list[Scenario] = [
    Scenario(
        case_id="GOAL-231",
        title="azure ai engineer certification",
        description="我要准备 Azure AI Engineer Associate 认证，三个月内通过，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "Azure AI Engineer Associate"},
            "confirm": True,
        },
        feedbacks=("今天完成了 Azure OpenAI 和搜索服务的一套练习题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-232",
        title="celpip speaking canada",
        description="我想准备 CELPIP 英语口语，目标两个月提升到 8，请每天陪练并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "口语 6-7 左右",
            "target_level": "CELPIP Speaking 8",
            "available_time": {"type": "daily", "minutes": 35},
            "raw_answers": {"target_language": "英语", "exam_name": "CELPIP Speaking"},
            "confirm": True,
        },
        feedbacks=("今天完成了两段口语录音，但复盘还没做完。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-233",
        title="flask sqlalchemy rest api",
        description="我想学 Flask 和 SQLAlchemy，做一个 REST API 小项目，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 Python，不熟 ORM 和接口分层",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Flask SQLAlchemy REST API",
                "project_goal": "REST API 小项目",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 ORM relationship 和接口序列化。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-234",
        title="indoor cycling cadence",
        description="我想练动感单车踏频和有氧耐力，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "有氧基础一般，踏频不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "膝盖不适时降低阻力"},
            "confirm": True,
        },
        feedbacks=("今天练了一半踏频，耐力组还没完成。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-235",
        title="weekly meal prep protein",
        description="我想建立每周高蛋白备餐习惯，减少外卖，请监督。",
        expected_domain="fitness",
        intake={
            "current_level": "经常点外卖，蛋白质摄入不稳定",
            "available_time": {"type": "weekly", "minutes": 120},
            "constraints": {"health_note": "饮食调整以个人耐受和医生建议为准"},
            "confirm": True,
        },
        feedbacks=("今天买了菜，但没来得及做完三天备餐。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [7], "time": "18:00"},
    ),
    Scenario(
        case_id="GOAL-236",
        title="cils b1 italian",
        description="我要准备 CILS B1 意大利语考试，重点练听力和写作，请每天监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "A2 左右",
            "target_level": "CILS B1 听力和写作达标",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "意大利语", "exam_name": "CILS B1"},
            "confirm": True,
        },
        feedbacks=("今天只听了一段材料，写作没时间完成。",),
        expected_statuses=("missed",),
    ),
    Scenario(
        case_id="GOAL-237",
        title="electron desktop app",
        description="我想学 Electron 和 TypeScript，做一个桌面便签应用，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端，不熟桌面应用打包",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Electron TypeScript",
                "project_goal": "桌面便签应用",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了窗口创建和便签输入框，但打包还没做。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-238",
        title="tennis serve basics",
        description="我想练网球发球基础动作和稳定性，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "新手，抛球不稳，肩膀容易紧",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "肩肘疼痛时停止训练"},
            "confirm": True,
        },
        feedbacks=("今天没训练，肩膀有点不舒服。", "今天还是休息了，怕影响恢复。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
    ),
    Scenario(
        case_id="GOAL-239",
        title="paperless bills archive",
        description="我想建立无纸化账单归档习惯，每周整理发票、保单和缴费记录，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "weekly", "minutes": 45},
            "raw_answers": {"success_criteria": "完成发票、保单和缴费记录归档"},
            "confirm": True,
        },
        feedbacks=("今天整理完了水电和宽带账单。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-240",
        title="cfa esg certificate",
        description="我要准备 CFA ESG Investing Certificate，两个月内通过，请监督复习。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "CFA ESG Investing Certificate"},
            "confirm": True,
        },
        feedbacks=("今天刷完了 ESG integration 第一章题库。",),
        expected_statuses=("done",),
    ),
]


SCENARIOS_ROUND25: list[Scenario] = [
    Scenario(
        case_id="GOAL-241",
        title="oracle java certification",
        description="我要准备 Oracle Java SE 认证，三个月内通过，请安排复习并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-09",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "Oracle Java SE 认证"},
            "confirm": True,
        },
        feedbacks=("今天完成了泛型和集合框架的一套练习题。",),
        expected_statuses=("done",),
    ),
    Scenario(
        case_id="GOAL-242",
        title="pte academic writing",
        description="我想准备 PTE Academic 英语写作，目标两个月提高到 70 分，请每天陪练并监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "写作 60 分左右",
            "target_level": "PTE Academic Writing 70",
            "available_time": {"type": "daily", "minutes": 40},
            "raw_answers": {"target_language": "英语", "exam_name": "PTE Academic"},
            "confirm": True,
        },
        feedbacks=("今天写完了一篇 summarize written text，但 essay 还没改。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-243",
        title="astro drizzle workers app",
        description="我想学 Astro、Drizzle 和 Cloudflare Workers，做一个边缘部署博客 demo，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点前端和 TypeScript，不熟边缘部署",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Astro Drizzle Cloudflare Workers",
                "project_goal": "边缘部署博客 demo",
            },
            "confirm": True,
        },
        feedbacks=("今天卡在 Drizzle schema 和 Workers 环境变量配置。",),
        expected_statuses=("blocked",),
    ),
    Scenario(
        case_id="GOAL-244",
        title="pilates reformer posture",
        description="我想练普拉提器械入门，改善核心和体态，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "核心弱，肩颈容易紧",
            "available_time": {"type": "weekly", "minutes": 90},
            "constraints": {"health_note": "腰背不适时降低强度"},
            "confirm": True,
        },
        feedbacks=("今天练了一半核心动作，拉伸还没做完。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [2, 6], "time": "19:30"},
    ),
    Scenario(
        case_id="GOAL-245",
        title="book notes habit",
        description="我想建立每周读书笔记习惯，一个月输出 4 篇书摘和思考，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-07",
            "available_time": {"type": "weekly", "minutes": 120},
            "raw_answers": {"success_criteria": "一个月输出 4 篇书摘和思考"},
            "confirm": True,
        },
        feedbacks=("今天整理完了第一篇书摘。",),
        expected_statuses=("done",),
        schedule={"type": "weekly", "days": [7], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-246",
        title="goethe b2 german",
        description="我要准备 Goethe-Zertifikat B2 德语考试，重点练听力和写作，请每天监督。",
        expected_domain="language_learning",
        intake={
            "current_level": "B1 到 B2 之间",
            "target_level": "Goethe B2 听力和写作达标",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {"target_language": "德语", "exam_name": "Goethe-Zertifikat B2"},
            "confirm": True,
        },
        feedbacks=("今天只听了一套题，写作没时间完成。",),
        expected_statuses=("missed",),
    ),
    Scenario(
        case_id="GOAL-247",
        title="blazor dotnet dashboard",
        description="我想学 Blazor 和 .NET，做一个内部指标看板，请规划并监督。",
        expected_domain="programming_learning",
        intake={
            "current_level": "会一点 C#，不熟 Blazor 组件",
            "available_time": {"type": "daily", "minutes": 45},
            "raw_answers": {
                "language_or_track": "Blazor .NET C#",
                "project_goal": "内部指标看板",
            },
            "confirm": True,
        },
        feedbacks=("今天完成了页面组件和第一张图表，但数据接口还没接。",),
        expected_statuses=("partial",),
    ),
    Scenario(
        case_id="GOAL-248",
        title="hiking endurance habit",
        description="我想练徒步耐力，三个月内能轻松走完 15 公里，请每周监督训练。",
        expected_domain="fitness",
        intake={
            "current_level": "平时走路少，连续走 5 公里会累",
            "available_time": {"type": "weekly", "minutes": 150},
            "constraints": {"health_note": "膝盖或脚踝疼痛时降低里程"},
            "confirm": True,
        },
        feedbacks=("今天没走，脚踝有点不舒服。", "今天还是休息了，怕加重。"),
        expected_statuses=("missed", "missed"),
        expect_intervention=True,
        schedule={"type": "weekly", "days": [3, 7], "time": "19:00"},
    ),
    Scenario(
        case_id="GOAL-249",
        title="password manager audit",
        description="我想建立密码管理器整理和账号安全审计习惯，每周整理一类账号，请监督。",
        expected_domain="general",
        intake={
            "target_date": "2026-08",
            "available_time": {"type": "weekly", "minutes": 45},
            "raw_answers": {"success_criteria": "完成密码管理器分类和账号安全审计记录"},
            "confirm": True,
        },
        feedbacks=("今天整理完了邮箱账号分类，但两步验证清单还没核对。",),
        expected_statuses=("partial",),
        schedule={"type": "weekly", "days": [6], "time": "20:00"},
    ),
    Scenario(
        case_id="GOAL-250",
        title="hrbp certification",
        description="我要准备 HRBP 认证考试，四个月内通过，请安排学习计划并监督。",
        expected_domain="exam_certification",
        intake={
            "target_date": "2026-10",
            "available_time": {"type": "daily", "minutes": 50},
            "raw_answers": {"exam_name": "HRBP 认证考试"},
            "confirm": True,
        },
        feedbacks=("今天完成了组织诊断章节笔记。",),
        expected_statuses=("done",),
    ),
]


SCENARIO_SETS = {
    "round1": SCENARIOS,
    "round2": SCENARIOS_ROUND2,
    "round3": SCENARIOS_ROUND3,
    "round4": SCENARIOS_ROUND4,
    "round5": SCENARIOS_ROUND5,
    "round6": SCENARIOS_ROUND6,
    "round7": SCENARIOS_ROUND7,
    "round8": SCENARIOS_ROUND8,
    "round9": SCENARIOS_ROUND9,
    "round10": SCENARIOS_ROUND10,
    "round11": SCENARIOS_ROUND11,
    "round12": SCENARIOS_ROUND12,
    "round13": SCENARIOS_ROUND13,
    "round14": SCENARIOS_ROUND14,
    "round15": SCENARIOS_ROUND15,
    "round16": SCENARIOS_ROUND16,
    "round17": SCENARIOS_ROUND17,
    "round18": SCENARIOS_ROUND18,
    "round19": SCENARIOS_ROUND19,
    "round20": SCENARIOS_ROUND20,
    "round21": SCENARIOS_ROUND21,
    "round22": SCENARIOS_ROUND22,
    "round23": SCENARIOS_ROUND23,
    "round24": SCENARIOS_ROUND24,
    "round25": SCENARIOS_ROUND25,
}


if __name__ == "__main__":
    main()
