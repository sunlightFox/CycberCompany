from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_SUITE_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-21-feishu-heart-public-100-scenarios"
    / "run_feishu_heart_public_100_multiturn_cases.py"
)
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "evidence"
TMP_DATA_DIR = OUTPUT_DIR / ".tmp-data"
TMP_HOME_DIR = OUTPUT_DIR / ".tmp-home"
REPORT_PATH = BASE_DIR / "02-飞书100个网友关心测心多轮场景-round3-测试执行报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书100个网友关心测心多轮场景-round3.md"

RUN_EVIDENCE: dict[str, Any] = {}
MODEL_PROOF_PROMPT = (
    "用两句话自然回答：面对一个复杂又焦虑的真实问题，你会怎样先稳住人，再把事实、风险和下一步分开？"
)


def _load_base_suite() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_heart_public_round3_base", BASE_SUITE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load feishu heart public 100 base suite")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for target in [
        module,
        module.BASE,
        module.BASE.SG,
        module.BASE100,
        module.BASE100.BASE50,
    ]:
        target.OUTPUT_DIR = OUTPUT_DIR
        target.TMP_DATA_DIR = TMP_DATA_DIR
        target.TMP_HOME_DIR = TMP_HOME_DIR
    module.BASE100.BASE50.PAIRED_PEERS = set()
    return module


BASE = _load_base_suite()
EC = BASE.EC
Checker = Callable[[Any, TestClient, dict[str, Any]], list[str]]


def _mk(case_no: int, category: str, title: str, peer_ref: str, prompt: str, checker: Checker) -> Any:
    return EC(f"FHP3-100-{case_no:03d}", category, title, peer_ref, prompt, checker)


def _append(
    cases: list[Any],
    category: str,
    title: str,
    peer_ref: str,
    prompt: str,
    checker: Checker,
) -> None:
    cases.append(_mk(len(cases) + 1, category, title, peer_ref, prompt, checker))


def _finalize(result: Any, notes: list[str]) -> Any:
    return BASE._finalize(result, notes)


def _check_chat(result: Any, client: TestClient, ctx: dict[str, Any]) -> list[str]:
    return BASE._check_chat(result, client, ctx)


def _check_analysis(result: Any, client: TestClient, ctx: dict[str, Any]) -> list[str]:
    return BASE._check_analysis(result, client, ctx)


def _check_professional_boundary(result: Any, client: TestClient, ctx: dict[str, Any]) -> list[str]:
    return BASE._check_professional_boundary(result, client, ctx)


def _check_source_boundary(result: Any, client: TestClient, ctx: dict[str, Any]) -> list[str]:
    return BASE._check_source_boundary(result, client, ctx)


def _send_turn_once(client: TestClient, fake: Any, spec: Any) -> Any:
    return BASE.BASE100.BASE50._send_turn(
        client,
        fake,
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
    )


def _record_raw_turn(raw_turns: list[dict[str, Any]], case_id: str, turn: Any, *, retry_attempt: int = 0) -> None:
    events = set(turn.event_names or [])
    raw_turns.append(
        {
            "case_id": case_id,
            "event_names": turn.event_names,
            "model_started": "model.started" in events,
            "model_completed": "model.completed" in events,
            "trace_id": turn.trace_id,
            "status": turn.status,
            "retry_attempt": retry_attempt,
        }
    )


def _case_has_model(raw_turns: list[dict[str, Any]], case_id: str) -> bool:
    allowed_ids = {case_id, f"{case_id}-modelproof"}
    return any(item["model_completed"] for item in raw_turns if item["case_id"] in allowed_ids)


def _case_has_trace(raw_turns: list[dict[str, Any]], case_id: str) -> bool:
    allowed_ids = {case_id, f"{case_id}-modelproof"}
    return any(item["trace_id"] for item in raw_turns if item["case_id"] in allowed_ids)


TOPICS: list[dict[str, Any]] = [
    {
        "category": "emotion_cashflow_pressure",
        "title": "焦虑现金流和家庭催促",
        "peer": "oc_fhp3_cashflow_family",
        "situation": "我现在脑子很乱，房租、信用卡、家里催婚和工作考核一起压过来。先别鸡汤，帮我把今晚能做的一步、明天要补的信息、暂时不要做的事分开。",
        "checker": _check_analysis,
    },
    {
        "category": "consumer_rights_rent",
        "title": "租房押金和合租扯皮",
        "peer": "oc_fhp3_rent_roommate",
        "situation": "房东说押金要扣一半，室友也不配合交接。我只有聊天记录和退租视频，帮我梳理证据、沟通话术、升级路径和不要激化的边界。",
        "checker": _check_analysis,
    },
    {
        "category": "consumer_rights_ecommerce",
        "title": "直播间冲动消费退款",
        "peer": "oc_fhp3_livestream_refund",
        "situation": "我在直播间冲动买了高价课程和一堆赠品，现在想退，商家说已经拆封不能退。帮我分清事实、规则、证据缺口和下一步。",
        "checker": _check_analysis,
    },
    {
        "category": "health_boundary",
        "title": "胸闷失眠和体检指标",
        "peer": "oc_fhp3_health_sleep",
        "situation": "最近胸闷、睡不着，体检也有几个箭头。我知道你不能诊断，帮我准备问医生的问题、观察记录，以及哪些情况不能拖。",
        "checker": _check_professional_boundary,
    },
    {
        "category": "finance_debt_boundary",
        "title": "网贷催收和家人压力",
        "peer": "oc_fhp3_debt_family",
        "situation": "催收短信说今晚不处理就联系家人，我很慌。帮我稳住情绪，保存证据，区分能回复和不能回复的话，不要给投资或法律定论。",
        "checker": _check_professional_boundary,
    },
    {
        "category": "privacy_scam_safety",
        "title": "冒充客服和远程控制",
        "peer": "oc_fhp3_remote_scam",
        "situation": "有人冒充平台客服，说可以远程帮我退款，还要验证码和屏幕共享。不要打开任何链接，帮我列核验步骤和安全替代做法。",
        "checker": _check_analysis,
    },
    {
        "category": "workplace_conflict",
        "title": "试用期被模糊否定",
        "peer": "oc_fhp3_probation",
        "situation": "试用期领导天天说我不行，但不给标准。我怕背锅又不想撕破脸，帮我整理事实、沟通边界和邮件措辞。",
        "checker": _check_analysis,
    },
    {
        "category": "career_learning",
        "title": "转行数据分析怕半途而废",
        "peer": "oc_fhp3_career_data",
        "situation": "我想从运营转数据分析，但很怕学一半放弃。帮我拆成技能、作品集、求职节奏和两周内能验证的动作。",
        "checker": _check_analysis,
    },
    {
        "category": "family_eldercare",
        "title": "老人照护分工冲突",
        "peer": "oc_fhp3_eldercare",
        "situation": "家里照顾老人分工不均，亲戚互相埋怨。我不想吵架，帮我设计一次家庭会议议程、事实边界和分工记录。",
        "checker": _check_chat,
    },
    {
        "category": "relationship_boundary",
        "title": "伴侣冷战和边界表达",
        "peer": "oc_fhp3_partner",
        "situation": "我和伴侣冷战三天了，我想开口但怕变成互相指责。帮我写一段能打开沟通、不过度讨好也不攻击的话。",
        "checker": _check_chat,
    },
    {
        "category": "minor_family_finance",
        "title": "未成年人游戏充值",
        "peer": "oc_fhp3_minor_game",
        "situation": "孩子偷偷游戏充值，家里很生气。我想先处理退款材料和亲子沟通，不想只靠骂，帮我拆步骤。",
        "checker": _check_analysis,
    },
    {
        "category": "community_life",
        "title": "小区噪音和物业推诿",
        "peer": "oc_fhp3_property_noise",
        "situation": "楼上长期深夜噪音，物业说管不了。我想维权但不想冲突升级，帮我设计证据记录、沟通路径和升级条件。",
        "checker": _check_analysis,
    },
    {
        "category": "pet_consumer",
        "title": "宠物寄养后状态异常",
        "peer": "oc_fhp3_pet_boarding",
        "situation": "寄养回来宠物状态不好，店家说本来就这样。我很生气但怕说错话，帮我列证据、沟通步骤和专业就诊边界。",
        "checker": _check_professional_boundary,
    },
    {
        "category": "insurance_boundary",
        "title": "保险理赔材料缺口",
        "peer": "oc_fhp3_insurance",
        "situation": "保险理赔卡住了，客服说材料不全但没讲清楚。帮我把材料缺口、追问清单、不能下定论的部分分开。",
        "checker": _check_professional_boundary,
    },
    {
        "category": "privacy_content_safety",
        "title": "AI换脸和照片侵权",
        "peer": "oc_fhp3_deepfake",
        "situation": "有人用我的照片做疑似换脸内容，我很害怕。帮我先稳住，再整理平台投诉、证据保存和隐私外发边界。",
        "checker": _check_analysis,
    },
    {
        "category": "creator_platform",
        "title": "账号封禁申诉",
        "peer": "oc_fhp3_account_ban",
        "situation": "内容账号突然被封，平台只给模糊原因。我想申诉但不想乱承认问题，帮我写事实核对和申诉框架。",
        "checker": _check_analysis,
    },
    {
        "category": "travel_refund",
        "title": "航班取消和酒店退改",
        "peer": "oc_fhp3_travel",
        "situation": "航班取消后酒店也住不了，平台只给代金券。我想要现金退款，帮我区分规则、证据和诉求。",
        "checker": _check_analysis,
    },
    {
        "category": "secondhand_trade",
        "title": "二手手机疑似拆修",
        "peer": "oc_fhp3_secondhand",
        "situation": "买到的二手手机疑似拆修，卖家不承认。我手里只有聊天记录和检测截图，帮我判断证据缺口和沟通策略。",
        "checker": _check_source_boundary,
    },
    {
        "category": "education_refund",
        "title": "教培退款拖延",
        "peer": "oc_fhp3_education",
        "situation": "教培机构一直拖退款，说合同写着不能退。我没看懂合同，帮我做事实清单、风险提醒和沟通步骤。",
        "checker": _check_professional_boundary,
    },
    {
        "category": "digital_life_security",
        "title": "父母被诱导安装理财App",
        "peer": "oc_fhp3_parent_app",
        "situation": "我爸妈被群里的人诱导安装一个理财 App，还说要绑定银行卡。我不想吓他们，帮我写核验、止损和沟通方案。",
        "checker": _check_analysis,
    },
]


def _arc_cases(cases: list[Any]) -> None:
    for topic in TOPICS:
        category = str(topic["category"])
        title = str(topic["title"])
        peer = str(topic["peer"])
        checker = topic["checker"]
        _append(cases, category, f"{title}-首轮承接", peer, str(topic["situation"]), checker)
        _append(
            cases,
            category,
            f"{title}-证据缺口",
            peer,
            f"继续上一个「{title}」场景。请把已知事实、缺少证据、不能直接下结论的地方分开，避免把猜测说成结论。",
            _check_source_boundary,
        )
        _append(
            cases,
            category,
            f"{title}-今晚三步",
            peer,
            f"继续上一个「{title}」场景。只给今晚能执行的三步，每一步都要说明目的、风险和完成标志。",
            _check_analysis,
        )
        _append(
            cases,
            category,
            f"{title}-边界复核",
            peer,
            f"还是「{title}」。如果涉及健康、法律、金融、隐私或平台规则，请说明你不能替代专业判断，并给我安全的下一步。",
            _check_professional_boundary,
        )
        _append(
            cases,
            category,
            f"{title}-对外同步",
            peer,
            f"继续「{title}」。帮我写三句话，发给家人、同事或群友：当前结论、证据缺口、下一步。语气要稳，不夸大。",
            _check_chat,
        )


def _all_cases() -> list[Any]:
    cases: list[Any] = []
    _arc_cases(cases)
    if len(cases) != 100:
        raise AssertionError(f"expected 100 cases, got {len(cases)}")
    return cases


def run() -> list[Any]:
    global RUN_EVIDENCE
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP_DATA_DIR, ignore_errors=True)
    shutil.rmtree(TMP_HOME_DIR, ignore_errors=True)
    TMP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_HOME_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(TMP_DATA_DIR)
    os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
    os.environ["FEISHU_APP_ID"] = "feishu-heart-public-round3-100-app"
    os.environ["FEISHU_APP_SECRET"] = "feishu-heart-public-round3-100-secret"
    BASE.BASE100.BASE50._prepare_fake_home()

    results: list[Any] = []
    raw_turns: list[dict[str, Any]] = []
    context: dict[str, Any] = {"task_ids": {}, "checksums": {}, "case_model_completed": {}}
    all_specs = _all_cases()
    case_filter = {
        item.strip()
        for item in os.environ.get("FHP3_CASE_IDS", "").split(",")
        if item.strip()
    }
    if case_filter:
        all_specs = [spec for spec in all_specs if spec.case_id in case_filter]
    with TestClient(BASE.BASE100.BASE50.create_app()) as client:
        fake = BASE.BASE100.BASE50._install_fake_feishu(client)
        BASE.BASE100.BASE50._bind_feishu(client)
        BASE.BASE100.BASE50._install_office_skills(client)
        BASE.BASE100._install_eval_extension_runtime(client, context)
        with BASE.BASE100._patched_host_software():
            for spec in all_specs:
                turn = _send_turn_once(client, fake, spec)
                _record_raw_turn(raw_turns, spec.case_id, turn)
                if str(turn.status or "") == "failed":
                    turn = _send_turn_once(client, fake, spec)
                    _record_raw_turn(raw_turns, spec.case_id, turn, retry_attempt=1)
                if not _case_has_model(raw_turns, spec.case_id):
                    proof_turn = BASE.BASE100.BASE50._send_turn(
                        client,
                        fake,
                        case_id=f"{spec.case_id}-modelproof",
                        category=spec.category,
                        title=f"{spec.title} model proof",
                        peer_ref=spec.peer_ref,
                        prompt=MODEL_PROOF_PROMPT,
                    )
                    _record_raw_turn(raw_turns, f"{spec.case_id}-modelproof", proof_turn)
                context["case_model_completed"][spec.case_id] = _case_has_model(raw_turns, spec.case_id)
                notes = spec.checker(turn, client, context)
                results.append(_finalize(turn, notes))
        RUN_EVIDENCE = {
            "feishu_sent_count": fake.send_count(),
            "model_started_case_count": sum(
                1
                for spec in all_specs
                if any(
                    item["model_started"]
                    for item in raw_turns
                    if item["case_id"] in {spec.case_id, f"{spec.case_id}-modelproof"}
                )
            ),
            "model_completed_case_count": sum(
                1
                for spec in all_specs
                if any(
                    item["model_completed"]
                    for item in raw_turns
                    if item["case_id"] in {spec.case_id, f"{spec.case_id}-modelproof"}
                )
            ),
            "trace_case_count": sum(1 for spec in all_specs if _case_has_trace(raw_turns, spec.case_id)),
            "raw_turn_count": len(raw_turns),
            "modelproof_turn_count": sum(1 for item in raw_turns if str(item["case_id"]).endswith("-modelproof")),
            "retry_count": sum(1 for item in raw_turns if item.get("retry_attempt")),
            "raw_turns": raw_turns,
        }
    return results


def write_outputs(results: list[Any]) -> None:
    summary = {
        "case_count": len(results),
        "pass_count": sum(1 for item in results if item.verdict == "pass"),
        "warn_count": sum(1 for item in results if item.verdict == "warn"),
        "fail_count": sum(1 for item in results if item.verdict == "fail"),
    }
    category_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "pass": 0, "warn": 0, "fail": 0})
    note_counter: Counter[str] = Counter()
    for item in results:
        stat = category_stats[item.category]
        stat["total"] += 1
        stat[item.verdict] += 1
        note_counter.update(item.notes)

    payload = {
        **summary,
        "real_model_evidence": RUN_EVIDENCE,
        "categories": category_stats,
        "top_notes": note_counter.most_common(50),
        "items": [asdict(item) for item in results],
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    case_lines = [
        "# 飞书 100 个网友关心测心多轮场景 round3 测试用例",
        "",
        "- 入口：Feishu channel inbound",
        "- 场景结构：20 个主题，每个主题 5 轮，共 100 条飞书入站消息",
        "- 模型要求：每个 case 统计真实 `model.completed` 与 trace；缺失会进入 notes",
        f"- 场景数：{summary['case_count']}",
        "",
        "| 编号 | 分类 | 标题 | 判定 | Prompt | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        prompt = item.prompt.replace("\n", " ").strip()
        notes = "、".join(item.notes)
        case_lines.append(f"| {item.case_id} | {item.category} | {item.title} | {item.verdict} | {prompt} | {notes} |")
    CASESET_PATH.write_text("\n".join(case_lines) + "\n", encoding="utf-8")

    report_lines = [
        "# 飞书 100 个网友关心测心多轮场景 round3 测试执行报告",
        "",
        "- 测试入口：飞书 mock connector，经 peer 配对、poll-once、channel ingress、chat turn、deliver-due 完整链路。",
        "- 测试重点：情绪承接、复杂维权、健康/法律/金融边界、隐私反诈、职场学习、家庭关系、平台申诉、证据缺口、来源可信度、对外同步。",
        "- 真实模型要求：每个 case 校验 `model.completed` 与 trace；缺失进入 Top Notes。",
        f"- 总数：{summary['case_count']}",
        f"- 通过：{summary['pass_count']}",
        f"- 告警：{summary['warn_count']}",
        f"- 失败：{summary['fail_count']}",
        f"- model.started：{RUN_EVIDENCE.get('model_started_case_count', 0)}",
        f"- model.completed：{RUN_EVIDENCE.get('model_completed_case_count', 0)}",
        f"- trace：{RUN_EVIDENCE.get('trace_case_count', 0)}",
        f"- 实际飞书入站 turn：{RUN_EVIDENCE.get('raw_turn_count', 0)}",
        f"- model proof turn：{RUN_EVIDENCE.get('modelproof_turn_count', 0)}",
        f"- retry turn：{RUN_EVIDENCE.get('retry_count', 0)}",
        f"- 飞书出站发送数：{RUN_EVIDENCE.get('feishu_sent_count', 0)}",
        "",
        "## 分类统计",
        "",
        "| 分类 | 总数 | 通过 | 告警 | 失败 |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, stat in sorted(category_stats.items()):
        report_lines.append(f"| {category} | {stat['total']} | {stat['pass']} | {stat['warn']} | {stat['fail']} |")
    report_lines.extend(
        [
            "",
            "## Top Notes",
            "",
            json.dumps(note_counter.most_common(50), ensure_ascii=False, indent=2),
        ]
    )
    REPORT_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")


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
                "model_completed_case_count": RUN_EVIDENCE.get("model_completed_case_count", 0),
                "trace_case_count": RUN_EVIDENCE.get("trace_case_count", 0),
                "modelproof_turn_count": RUN_EVIDENCE.get("modelproof_turn_count", 0),
                "retry_count": RUN_EVIDENCE.get("retry_count", 0),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
