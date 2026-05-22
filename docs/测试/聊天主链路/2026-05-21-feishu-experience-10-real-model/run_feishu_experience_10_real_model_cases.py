from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-飞书经验沉淀10轮真实模型测试执行报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书经验沉淀10轮真实模型场景.md"
TMP_PREFIX = "cycber_feishu_exp10_real_"
MODEL_PROXY_ENDPOINT = os.environ.get("CYCBER_REAL_MODEL_ENDPOINT", "http://127.0.0.1:8317/v1")


def _bootstrap_paths() -> None:
    paths = [
        ROOT_DIR / "apps" / "local-api",
        *ROOT_DIR.glob("packages/*"),
        *ROOT_DIR.glob("services/*"),
    ]
    for path in paths:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


_bootstrap_paths()

from app.core.config import ChannelProviderSection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.channel_connectors import FeishuMockConnector  # noqa: E402


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    title: str
    peer_ref: str
    prompt: str
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()


@dataclass
class CaseResult:
    case_id: str
    title: str
    peer_ref: str
    prompt: str
    verdict: str
    notes: list[str]
    reply_text: str
    turn_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    route_brain_id: str | None = None
    model_started: bool = False
    model_completed: bool = False
    usage_total_tokens: int | None = None
    reflected: bool = False
    experience_id: str | None = None
    memory_id: str | None = None
    experience_decision: str | None = None
    experience_kind: str | None = None
    experience_reuse_score: float | None = None
    skill_growth_decisions: list[str] = field(default_factory=list)
    delivery_sent: bool = False


CASES: list[CaseSpec] = [
    CaseSpec(
        case_id="FEXP10-001",
        title="流程偏好写入与稳定复述",
        peer_ref="oc_feishu_exp10_pref",
        prompt=(
            "记住 FEXP10-PREF：以后我让你做经验沉淀测试复盘时，"
            "先写结论，再写证据，再写风险，最后写下一步。请用一句话确认。"
        ),
        expected_terms=("FEXP10-PREF",),
        checks=("reply", "model", "experience", "memory"),
    ),
    CaseSpec(
        case_id="FEXP10-002",
        title="跨轮召回偏好并应用",
        peer_ref="oc_feishu_exp10_pref",
        prompt="按 FEXP10-PREF 的顺序，给我一个飞书渠道经验沉淀测试的短复盘。",
        expected_terms=("结论", "证据", "风险", "下一步"),
        checks=("reply", "model", "experience"),
    ),
    CaseSpec(
        case_id="FEXP10-003",
        title="项目规则沉淀",
        peer_ref="oc_feishu_exp10_project",
        prompt=(
            "记住 FEXP10-PROJECT：当前阶段只测试后端经验沉淀，"
            "飞书入口必须经过 channel ingress、chat turn、agent workbench reflection。"
        ),
        expected_terms=("记住", "FEXP10-PROJECT"),
        checks=("reply", "model", "experience", "memory"),
    ),
    CaseSpec(
        case_id="FEXP10-004",
        title="项目规则跨轮召回",
        peer_ref="oc_feishu_exp10_project",
        prompt="FEXP10-PROJECT 现在是什么？请只复述这条规则，不要扩展。",
        expected_terms=("后端经验沉淀", "飞书", "agent workbench"),
        checks=("reply", "model", "experience"),
    ),
    CaseSpec(
        case_id="FEXP10-005",
        title="纠正规则并验证冲突治理",
        peer_ref="oc_feishu_exp10_project",
        prompt=(
            "修正 FEXP10-PROJECT：测试入口仍然是飞书，但验收必须额外确认 "
            "memory_experience_records 和 skill growth evidence。"
        ),
        expected_terms=("修正", "memory_experience_records", "skill growth"),
        checks=("reply", "model", "experience", "memory"),
    ),
    CaseSpec(
        case_id="FEXP10-006",
        title="可复用流程经验",
        peer_ref="oc_feishu_exp10_workflow",
        prompt=(
            "这次经验请沉淀：做真实模型渠道测试时，先确认模型端口，"
            "再复制隔离数据，再模拟渠道消息，最后反射 turn 并查经验记录。"
        ),
        expected_terms=("模型端口", "渠道消息", "经验记录"),
        checks=("reply", "model", "experience", "skill_growth"),
    ),
    CaseSpec(
        case_id="FEXP10-007",
        title="失败经验治理",
        peer_ref="oc_feishu_exp10_failure",
        prompt=(
            "帮我分析一个失败经验：如果真实模型端口拒绝连接，"
            "飞书经验沉淀测试应该如何记录证据、降级判断和下一步？"
        ),
        expected_terms=("端口", "证据", "下一步"),
        checks=("reply", "model", "experience"),
    ),
    CaseSpec(
        case_id="FEXP10-008",
        title="敏感信息不应沉淀",
        peer_ref="oc_feishu_exp10_secret",
        prompt=(
            "记住 FEXP10-SECRET：我的测试 token 是 sk-fexp10-secret-should-not-store。"
            "如果不适合记忆，请说明。"
        ),
        forbidden_terms=("sk-fexp10-secret-should-not-store",),
        checks=("reply", "model", "experience", "no_secret_leak"),
    ),
    CaseSpec(
        case_id="FEXP10-009",
        title="多事实整合复盘",
        peer_ref="oc_feishu_exp10_project",
        prompt=(
            "结合 FEXP10-PREF 和修正后的 FEXP10-PROJECT，"
            "给我一段三句话以内的测试复盘。"
        ),
        expected_terms=("经验", "飞书"),
        checks=("reply", "model", "experience"),
    ),
    CaseSpec(
        case_id="FEXP10-010",
        title="经验沉淀边界说明",
        peer_ref="oc_feishu_exp10_boundary",
        prompt=(
            "请说明这轮飞书真实模型消息会如何进入经验沉淀链路，"
            "重点讲 source、trace、memory candidate、experience record、skill growth。"
        ),
        expected_terms=("source", "trace", "experience", "skill"),
        checks=("reply", "model", "experience", "skill_growth"),
    ),
]


class ScenarioFeishuConnector(FeishuMockConnector):
    provider = "feishu"

    def __init__(self) -> None:
        super().__init__(ChannelProviderSection(enabled=True, poll_enabled=True))
        self.sent_text: list[dict[str, Any]] = []

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> Any:
        self.sent_text.append({"recipient": recipient, "text": text})
        return await super().send_text(
            provider_state_ref=provider_state_ref,
            provider_state=provider_state,
            recipient=recipient,
            text=text,
        )

    def send_count(self) -> int:
        return len(self.sent_text)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _copy_runtime_data() -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    data_dir = temp_root / "data"
    (data_dir / "sqlite").mkdir(parents=True, exist_ok=True)
    source_db = ROOT_DIR / "data" / "sqlite" / "app.db"
    if not source_db.exists():
        raise RuntimeError(f"source database not found: {source_db}")
    shutil.copy2(source_db, data_dir / "sqlite" / "app.db")
    source_secrets = ROOT_DIR / "data" / "secrets"
    if source_secrets.exists():
        shutil.copytree(source_secrets, data_dir / "secrets", dirs_exist_ok=True)
    for name in ["traces", "artifacts", "channel-providers", "backups", "restore-workspaces"]:
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    source_codex = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())) / ".codex"
    target_codex = data_dir / "home" / ".codex"
    for filename in ["config.toml", "auth.json"]:
        source = source_codex / filename
        if source.exists():
            target_codex.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_codex / filename)
    _patch_brain_endpoint(data_dir / "sqlite" / "app.db")
    return data_dir


def _patch_brain_endpoint(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE brains
            SET endpoint = ?, status = 'healthy', last_error_code = NULL, last_error_message = NULL
            WHERE provider IN ('openai_compatible', 'custom_openai_compatible')
               OR brain_id = 'brain_not_configured'
            """,
            (MODEL_PROXY_ENDPOINT,),
        )
        conn.commit()


def _bind_feishu(client: TestClient) -> dict[str, Any]:
    started = client.post(
        "/api/channels/bind-sessions",
        json={
            "provider": "feishu",
            "requested_by_member_id": "mem_xiaoyao",
            "display_name_hint": "飞书经验沉淀真实模型测试机器人",
        },
    )
    if started.status_code != 200:
        raise RuntimeError(started.text)
    payload = started.json()
    callback = client.get(
        "/api/channels/inbound/feishu/bind-callback",
        params={
            "state": payload["bind_session_id"],
            "code": "feishu-exp10-oauth-code",
            "tenant_key": "tenant_feishu_exp10_secret",
            "open_id": "ou_feishu_exp10_secret",
        },
    )
    if callback.status_code != 200:
        raise RuntimeError(callback.text)
    finalized = client.post(f"/api/channels/bind-sessions/{payload['bind_session_id']}/finalize")
    if finalized.status_code != 200:
        raise RuntimeError(finalized.text)
    return cast(dict[str, Any], finalized.json()["account"])


def _install_fake_feishu(client: TestClient) -> ScenarioFeishuConnector:
    registry = cast(Any, client.app).state.registry
    fake = ScenarioFeishuConnector()
    registry.channel_binding_service.connector_registry()._connectors["feishu"] = fake
    return fake


def _text_event(event_id: str, chat_id: str, sender_id: str, text: str) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": "2026-05-21T12:00:00+08:00",
        },
        "event": {
            "sender": {"sender_id": {"open_id": sender_id}, "sender_type": "user"},
            "message": {
                "message_id": f"om_{event_id}",
                "chat_id": chat_id,
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


def _latest_binding(client: TestClient) -> dict[str, Any] | None:
    payload = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "feishu", "limit": 1},
    )
    if payload.status_code != 200:
        raise RuntimeError(payload.text)
    items = payload.json()["items"]
    return cast(dict[str, Any], items[0]) if items else None


def _turn_payload(client: TestClient, turn_id: str) -> dict[str, Any]:
    response = client.get(f"/api/chat/turns/{turn_id}")
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return cast(dict[str, Any], response.json())


def _turn_events(client: TestClient, turn_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/chat/turns/{turn_id}/events")
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return cast(list[dict[str, Any]], response.json()["items"])


def _visible_reply(events: list[dict[str, Any]]) -> str:
    text = "".join(
        str(item.get("payload", {}).get("payload", {}).get("text", ""))
        for item in events
        if item["event_type"] == "response.delta"
    )
    if text:
        return text
    for item in reversed(events):
        if item["event_type"] != "response.completed":
            continue
        payload = item.get("payload", {}).get("payload", {})
        response_plan = payload.get("response_plan", {}) or {}
        plain = str(response_plan.get("plain_text") or response_plan.get("summary") or "")
        if plain:
            return plain
    return ""


def _model_summary(events: list[dict[str, Any]]) -> tuple[bool, bool, int | None, str | None]:
    started = any(item["event_type"] == "model.started" for item in events)
    completed = False
    total_tokens = None
    brain_id = None
    for item in events:
        payload = item.get("payload", {}).get("payload", {})
        if item["event_type"] == "model.started":
            brain_id = str(payload.get("brain_id") or "") or brain_id
        if item["event_type"] == "model.completed":
            completed = True
            usage = payload.get("usage") or {}
            if usage.get("total_tokens") is not None:
                total_tokens = int(usage["total_tokens"])
    return started, completed, total_tokens, brain_id


def _wait_for_new_turn(client: TestClient, previous_turn_id: str | None, timeout: float = 240.0) -> str:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        binding = _latest_binding(client)
        if binding:
            turn_id = str(binding["turn_id"])
            if turn_id != previous_turn_id:
                turn = _turn_payload(client, turn_id)
                last_status = turn.get("status")
                if str(last_status) in {"completed", "failed", "cancelled"}:
                    return turn_id
        time.sleep(0.2)
    raise RuntimeError(f"new feishu turn was not observed, last_status={last_status}")


def _ensure_peer(client: TestClient, fake: ScenarioFeishuConnector, peer_ref: str, paired: set[str]) -> None:
    if peer_ref in paired:
        return
    fake.enqueue_event(_text_event(f"evt-pair-{peer_ref}", peer_ref, "ou_sender", "你好"))
    response = client.post("/api/channels/providers/feishu/poll-once")
    if response.status_code != 200:
        raise RuntimeError(response.text)
    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "feishu", "status": "pending"},
    )
    if pairings.status_code != 200:
        raise RuntimeError(pairings.text)
    items = pairings.json()["items"]
    if not items:
        raise RuntimeError(f"no pairing created for {peer_ref}")
    approved = client.post(
        f"/api/channels/pairing-requests/{items[0]['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao", "reason": "feishu experience real model test"},
    )
    if approved.status_code != 200:
        raise RuntimeError(approved.text)
    paired.add(peer_ref)


def _reflect_turn(client: TestClient, turn_id: str) -> dict[str, Any]:
    response = client.post(f"/api/agent-workbench/turns/{turn_id}/reflect")
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return cast(dict[str, Any], response.json()["result"])


def _experience_record(client: TestClient, experience_id: str) -> dict[str, Any] | None:
    response = client.get("/api/memory/experience-records", params={"limit": 50})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    for item in response.json()["items"]:
        if item["experience_id"] == experience_id:
            return cast(dict[str, Any], item)
    return None


def _growth_decisions(client: TestClient, experience_id: str) -> list[str]:
    response = client.get("/api/skills/growth-candidates", params={"limit": 100})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    decisions: list[str] = []
    for item in response.json()["items"]:
        if item.get("experience_id") == experience_id:
            decisions.append(str(item.get("decision")))
    return decisions


def _send_case(
    client: TestClient,
    fake: ScenarioFeishuConnector,
    spec: CaseSpec,
    paired: set[str],
) -> CaseResult:
    notes: list[str] = []
    _ensure_peer(client, fake, spec.peer_ref, paired)
    previous = _latest_binding(client)
    previous_turn_id = str(previous["turn_id"]) if previous else None
    event_id = f"evt-{spec.case_id}-{_hash_text(spec.prompt)[:10]}"
    previous_send_count = fake.send_count()
    fake.enqueue_event(_text_event(event_id, spec.peer_ref, "ou_sender", spec.prompt))
    routed = client.post("/api/channels/providers/feishu/poll-once")
    if routed.status_code != 200:
        return CaseResult(
            case_id=spec.case_id,
            title=spec.title,
            peer_ref=spec.peer_ref,
            prompt=spec.prompt,
            verdict="fail",
            notes=[f"poll_failed:{routed.status_code}"],
            reply_text=routed.text,
        )
    try:
        turn_id = _wait_for_new_turn(client, previous_turn_id)
    except Exception as exc:
        return CaseResult(
            case_id=spec.case_id,
            title=spec.title,
            peer_ref=spec.peer_ref,
            prompt=spec.prompt,
            verdict="fail",
            notes=[f"turn_wait_failed:{exc}"],
            reply_text="",
        )
    for _ in range(4):
        delivered = client.post("/api/channels/providers/feishu/deliver-due")
        if delivered.status_code != 200:
            notes.append(f"deliver_failed:{delivered.status_code}")
        time.sleep(0.1)
    turn = _turn_payload(client, turn_id)
    events = _turn_events(client, turn_id)
    reply = _visible_reply(events)
    model_started, model_completed, usage_total, brain_id = _model_summary(events)
    if not reply.strip():
        notes.append("empty_reply")
    for term in spec.expected_terms:
        if term not in reply:
            notes.append(f"missing_expected_term:{term}")
    serialized_result = json.dumps({"reply": reply, "events": events}, ensure_ascii=False)
    for term in spec.forbidden_terms:
        if term in serialized_result:
            notes.append(f"forbidden_term_visible_or_trace:{term}")
    if "model" in spec.checks and not (model_started and model_completed):
        notes.append("real_model_not_completed")
    delivery_sent = fake.send_count() > previous_send_count
    if not delivery_sent:
        notes.append("delivery_not_sent")
    reflection: dict[str, Any] = {}
    exp_record: dict[str, Any] | None = None
    growth: list[str] = []
    try:
        reflection = _reflect_turn(client, turn_id)
        if reflection.get("experience_id"):
            exp_record = _experience_record(client, str(reflection["experience_id"]))
            growth = _growth_decisions(client, str(reflection["experience_id"]))
    except Exception as exc:
        notes.append(f"reflection_failed:{exc}")
    if "experience" in spec.checks and not reflection.get("experience_id"):
        notes.append("experience_missing")
    if "memory" in spec.checks and not reflection.get("memory_ids"):
        notes.append("memory_missing")
    if "skill_growth" in spec.checks and not growth:
        notes.append("skill_growth_missing")
    if "no_secret_leak" in spec.checks and "sk-fexp10-secret-should-not-store" in serialized_result:
        notes.append("secret_leaked")
    if str(turn.get("status")) != "completed":
        notes.append(f"turn_status:{turn.get('status')}")
    verdict = "pass" if not notes else "warn"
    hard_fail_prefixes = (
        "poll_failed",
        "turn_wait_failed",
        "empty_reply",
        "real_model_not_completed",
        "reflection_failed",
        "experience_missing",
        "forbidden_term_visible_or_trace",
        "secret_leaked",
        "turn_status:",
    )
    if any(any(note.startswith(prefix) for prefix in hard_fail_prefixes) for note in notes):
        verdict = "fail"
    return CaseResult(
        case_id=spec.case_id,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        verdict=verdict,
        notes=notes,
        reply_text=reply,
        turn_id=turn_id,
        conversation_id=turn.get("conversation_id"),
        trace_id=turn.get("trace_id"),
        route_brain_id=brain_id,
        model_started=model_started,
        model_completed=model_completed,
        usage_total_tokens=usage_total,
        reflected=bool(reflection),
        experience_id=str(reflection.get("experience_id") or "") or None,
        memory_id=(reflection.get("memory_ids") or [None])[0],
        experience_decision=exp_record.get("decision") if exp_record else None,
        experience_kind=exp_record.get("kind") if exp_record else None,
        experience_reuse_score=exp_record.get("reuse_score") if exp_record else None,
        skill_growth_decisions=growth,
        delivery_sent=delivery_sent,
    )


def _write_caseset() -> None:
    lines = ["# 飞书经验沉淀 10 轮真实模型测试用例", ""]
    for case in CASES:
        lines.extend(
            [
                f"## {case.case_id} {case.title}",
                f"- 飞书 peer: `{case.peer_ref}`",
                f"- 输入: {case.prompt}",
                f"- 检查: {', '.join(case.checks)}",
                "",
            ]
        )
    CASESET_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_outputs(results: list[CaseResult], *, model_verify: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for item in results if item.verdict == "pass")
    warned = sum(1 for item in results if item.verdict == "warn")
    failed = sum(1 for item in results if item.verdict == "fail")
    summary = {
        "run_label": "FEXP10-REAL-20260521",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": MODEL_PROXY_ENDPOINT,
        "model_verify": {
            key: value
            for key, value in model_verify.items()
            if key not in {"message", "verify_capabilities"}
        },
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "results": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 飞书经验沉淀 10 轮真实模型测试执行报告",
        "",
        "- 测试入口：飞书 mock connector，经 `poll-once -> chat turn -> deliver-due -> agent workbench reflect`。",
        "- 模型要求：真实模型调用，检查 `model.started` 与 `model.completed`。",
        f"- 模型端点：`{MODEL_PROXY_ENDPOINT}`。",
        f"- 结果：{passed} pass / {warned} warn / {failed} fail。",
        "",
        "| Case | 场景 | 结论 | 经验 | 记忆 | Skill growth | 备注 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            "| {case} | {title} | {verdict} | {experience} | {memory} | {growth} | {notes} |".format(
                case=item.case_id,
                title=item.title,
                verdict=item.verdict,
                experience=item.experience_decision or "-",
                memory=item.memory_id or "-",
                growth=",".join(item.skill_growth_decisions) or "-",
                notes=", ".join(item.notes) or "-",
            )
        )
    lines.extend(["", "## 样例回复摘录", ""])
    for item in results:
        preview = item.reply_text.replace("\n", " ")[:220]
        lines.append(f"- `{item.case_id}`: {preview}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run() -> list[CaseResult]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    _write_caseset()
    data_dir = _copy_runtime_data()
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
        os.environ["FEISHU_APP_ID"] = "feishu-exp10-real-app"
        os.environ["FEISHU_APP_SECRET"] = "feishu-exp10-real-secret"
        os.environ["USERPROFILE"] = str(data_dir / "home")
        os.environ["HOME"] = str(data_dir / "home")
        (data_dir / "home" / "Desktop").mkdir(parents=True, exist_ok=True)

        with TestClient(create_app()) as client:
            model_verify = client.post("/api/brains/brain_not_configured/verify")
            verify_payload = model_verify.json() if model_verify.headers.get("content-type", "").startswith("application/json") else {"status_code": model_verify.status_code, "text": model_verify.text}
            if model_verify.status_code != 200 or verify_payload.get("status") != "healthy":
                _write_outputs([], model_verify=verify_payload)
                raise RuntimeError(f"real model verify failed: {verify_payload}")
            _bind_feishu(client)
            fake = _install_fake_feishu(client)
            paired: set[str] = set()
            results = [_send_case(client, fake, case, paired) for case in CASES]
            _write_outputs(results, model_verify=verify_payload)
            return results
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    results = run()
    failed = [item for item in results if item.verdict == "fail"]
    warned = [item for item in results if item.verdict == "warn"]
    print(
        json.dumps(
            {
                "total": len(results),
                "passed": sum(1 for item in results if item.verdict == "pass"),
                "warned": len(warned),
                "failed": len(failed),
                "summary": str(SUMMARY_PATH),
                "report": str(REPORT_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
